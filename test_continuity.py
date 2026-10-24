import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "project-continuity" / "scripts"))
sys.path.insert(0, str(ROOT))
from continuity import Store, hook, read_json, write_json
import deploy


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="codex-continuity-test-")
        self.base = Path(self.tmp.name)
        self.project = self.base / "project"
        self.project.mkdir()
        self.store = Store(self.project, self.base / "data")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, data):
        p = self.project / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data)

    def snap(self, label="stage", task="nav", pin=False):
        with self.store.lock():
            return self.store.checkpoint(self.store.meta(), task, label, pin)

    def test_backward_forward_scoped_preserves_other_files_and_graph(self):
        self.write("nav.txt", "A")
        self.write("footer.txt", "old")
        a = self.store.begin("nav", "navigation", "s")["checkpoint"]
        self.write("nav.txt", "B")
        b = self.snap()["id"]
        self.write("footer.txt", "new footer")
        c = self.snap(task="footer")["id"]
        preview = self.store.restore(a, ["nav.txt"])
        self.assertEqual(preview["paths"], ["nav.txt"])
        self.assertEqual((self.project / "nav.txt").read_text(), "B")
        r = self.store.restore(a, ["nav.txt"], True)
        self.assertEqual((self.project / "footer.txt").read_text(), "new footer")
        self.assertEqual((self.project / "nav.txt").read_text(), "A")
        self.assertEqual(self.store.node(r["checkpoint"])["restored_from"], a)
        self.store.restore(b, ["nav.txt"], True)
        self.assertEqual((self.project / "nav.txt").read_text(), "B")
        self.assertEqual((self.project / "footer.txt").read_text(), "new footer")
        self.assertTrue(self.store.node(c))

    def test_uncheckpointed_edit_refused(self):
        self.write("nav.txt", "A")
        a = self.store.begin("nav", "nav")["checkpoint"]
        self.write("nav.txt", "B")
        self.snap()
        self.write("nav.txt", "user later edit")
        with self.assertRaisesRegex(ValueError, "conflict"):
            self.store.restore(a, ["nav.txt"], True)
        self.assertEqual((self.project / "nav.txt").read_text(), "user later edit")

    def test_new_deleted_files_recoverable(self):
        self.write("old.txt", "old")
        a = self.store.begin("nav", "nav")["checkpoint"]
        (self.project / "old.txt").unlink()
        self.write("new.txt", "new")
        b = self.snap()["id"]
        self.store.restore(a, ["old.txt", "new.txt"], True)
        self.assertFalse((self.project / "new.txt").exists())
        self.assertEqual((self.project / "old.txt").read_text(), "old")
        self.store.restore(b, ["old.txt", "new.txt"], True)
        self.assertFalse((self.project / "old.txt").exists())
        self.assertEqual((self.project / "new.txt").read_text(), "new")

    def test_no_file_changes_no_duplicate_node(self):
        a = self.store.begin("nav", "nav")["checkpoint"]
        self.assertEqual(a, self.snap()["id"])

    def test_secrets_and_symlinks_excluded(self):
        self.write(".env", "PASSWORD=private")
        self.write("private.key", "SECRET")
        self.write("src/a.py", "safe")
        (self.project / "outside").symlink_to(self.base)
        n = self.store.begin("nav", "nav")["checkpoint"]
        self.assertEqual(set(self.store.node(n)["files"]), {"src/a.py"})
        with self.assertRaises(ValueError):
            self.store.restore(n, ["../oops"], True)
        with self.assertRaises(ValueError):
            self.store.restore(n, ["outside/oops"], True)

    def test_git_index_and_user_changes_preserved(self):
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        self.write("tracked.txt", "original user change")
        subprocess.run(["git", "-C", str(self.project), "add", "tracked.txt"], check=True)
        index = (self.project / ".git" / "index").read_bytes()
        self.write("untracked.txt", "keep me")
        n = self.store.begin("nav", "nav")["checkpoint"]
        self.assertEqual(index, (self.project / ".git" / "index").read_bytes())
        self.assertIn("untracked.txt", self.store.node(n)["files"])
        self.write("tracked.txt", "agent change")
        self.snap()
        with self.assertRaisesRegex(ValueError, "staged"):
            self.store.restore(n, ["tracked.txt"], True)

    def test_corrupt_blob_refused_before_write(self):
        self.write("nav.txt", "A")
        a = self.store.begin("nav", "nav")["checkpoint"]
        h = self.store.node(a)["files"]["nav.txt"]["sha"]
        self.write("nav.txt", "B")
        self.snap()
        (self.store.base / "objects" / h[:2] / h[2:]).write_bytes(b"corrupt")
        with self.assertRaises(Exception):
            self.store.restore(a, ["nav.txt"], True)
        self.assertEqual((self.project / "nav.txt").read_text(), "B")

    def test_readonly_hooks_leave_no_files(self):
        for event in ["SessionStart", "UserPromptSubmit", "Stop", "PreCompact", "Interrupt"]:
            self.assertEqual(hook({"cwd": str(self.project), "session_id": "s", "hook_event_name": event}, self.base / "data"), {})
        self.assertEqual(list(self.project.iterdir()), [])
        self.assertFalse((self.base / "data").exists())

    def test_hook_bounded_summary_reminder_and_session_isolation(self):
        self.write("a.txt", "A")
        self.store.begin("nav", "nav", "session-one")
        self.store.begin("footer", "footer", "session-two")
        self.assertEqual(self.store.binding("session-one")["task"], "nav")
        p = {"cwd": str(self.project), "session_id": "session-one", "turn_id": "t", "hook_event_name": "PreToolUse",
             "tool_name": "apply_patch", "tool_input": {"command": "patch"}}
        hook(p, self.base / "data")
        self.write("a.txt", "B")
        p["hook_event_name"] = "Stop"
        result = hook(p, self.base / "data")
        self.assertEqual(result.get("decision"), "block")
        p["stop_hook_active"] = True
        self.assertEqual(hook(p, self.base / "data"), {})
        self.assertEqual(self.store.binding("session-two")["task"], "footer")

    def test_notes_durable_and_handoff_readable(self):
        self.store.begin("nav", "navigation")
        self.store.note("nav", "first", "next", "not tested", "scope", "active")
        out = self.store.note("nav", "second", "review", "tests passed", "", "active", True)
        self.assertEqual(len(list((self.project / ".codex-continuity" / "history" / "nav").glob("*.json"))), 2)
        self.assertIn("second", Path(out["handoff"]).read_text())
        self.assertIn("nav", out["new_chat_prompt"])

    def test_retention_pins_and_active_tasks(self):
        self.store.begin("nav", "nav")
        ids = []
        for i in range(56):
            self.write("a.txt", str(i))
            n = self.snap(pin=i == 1)
            n["created"] = f"2020-01-01T00:00:{i:02d}+00:00"
            write_json(self.store.base / "nodes" / (n["id"] + ".json"), n)
            ids.append(n["id"])
        with self.store.lock():
            self.assertEqual(self.store.prune(self.store.meta())["expired_temporary_nodes"], 0)
        self.store.note("nav", "done", "none", "passed", "", "complete")
        self.assertTrue(self.store.node(ids[1])["pinned"])
        self.assertTrue(read_json(self.store.base / "nodes" / (ids[0] + ".json"))["expired"])
        self.assertTrue(self.store.node(ids[-1]))

    def test_deploy_backup_merge_idempotence_rollback(self):
        codex = self.base / "codex"
        codex.mkdir()
        (codex / "AGENTS.md").write_text("Existing personal rule\n")
        original = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "existing-hook"}]}]}}
        write_json(codex / "hooks.json", original)
        receipt = deploy.install(codex)
        self.assertIn("Existing personal rule", (codex / "AGENTS.md").read_text())
        self.assertEqual(read_json(codex / "hooks.json")["hooks"]["Stop"][0]["hooks"][0]["command"], "existing-hook")
        self.assertFalse(deploy.install(codex)["changed"])
        self.assertFalse(deploy.rollback(codex, receipt["deployment"])["applied"])
        deploy.rollback(codex, receipt["deployment"], True)
        self.assertEqual((codex / "AGENTS.md").read_text(), "Existing personal rule\n")
        self.assertEqual(read_json(codex / "hooks.json"), original)
        self.assertFalse((codex / "skills/project-continuity/SKILL.md").exists())

    def test_rollback_refuses_later_user_edit(self):
        codex = self.base / "codex"
        receipt = deploy.install(codex)
        (codex / "AGENTS.md").write_text("later user edit")
        with self.assertRaisesRegex(ValueError, "changed"):
            deploy.rollback(codex, receipt["deployment"], True)
        self.assertEqual((codex / "AGENTS.md").read_text(), "later user edit")

    def test_custom_home_with_spaces_and_real_hook_command(self):
        import shlex
        codex = self.base / "custom codex home"
        run = subprocess.run([sys.executable, str(ROOT / "deploy.py"), "install"],
                             env={**os.environ, "CODEX_HOME": str(codex)},
                             capture_output=True, text=True, check=True)
        self.assertTrue(json.loads(run.stdout)["changed"])
        custom = Store(self.project, codex / "continuity")
        self.write("work.txt", "before")
        custom.begin("portable", "portable integration", "portable-session")
        definition = read_json(codex / "hooks.json")
        command = definition["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        payload = {"hook_event_name": "PreToolUse", "session_id": "portable-session",
                   "turn_id": "portable-turn", "cwd": str(self.project),
                   "tool_name": "apply_patch", "tool_input": "fixture edit"}
        response = subprocess.run(shlex.split(command), input=json.dumps(payload),
                                  env={key: val for key, val in os.environ.items()
                                       if key not in {"CODEX_HOME", "CODEX_CONTINUITY_HOME"}},
                                  capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(response.stdout), {})
        self.assertTrue(custom.binding("portable-session")["dirty"])
        rules = (codex / "AGENTS.md").read_text()
        self.assertIn(str(codex), rules)
        self.assertNotIn("__HELPER_COMMAND__", rules)
        self.assertNotIn("__SKILL_DIR__", rules)


if __name__ == "__main__":
    unittest.main()
