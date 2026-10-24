#!/usr/bin/env python3
"""Local, content-addressed checkpoints and bounded task handoffs. Python 3.9+."""
import argparse
import contextlib
import datetime as dt
import fcntl
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import zlib

VERSION = "1.0.0"
DOCS = ".codex-continuity"
SKIP_DIRS = {".git", DOCS, "node_modules", ".venv", "venv", "__pycache__", ".next", ".nuxt", ".cache", ".pytest_cache", "dist", "build", "target", ".DS_Store"}
SECRET = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "credentials*", "auth.json", "id_rsa*", "id_ed25519*", "*.sqlite*", "*.db", "*.log")
ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}$")


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def atomic(path, data, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError("Refusing symlink output: " + str(path))
    fd, temp = tempfile.mkstemp(prefix=".continuity-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def write_json(path, data):
    atomic(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode())


def git(root, *args):
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                           timeout=4, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
        if r.returncode:
            return None
        text = r.stdout.decode("utf-8", "surrogateescape")
        return text if "-z" in args else text.rstrip("\n")
    except (OSError, subprocess.TimeoutExpired):
        return None


def project_root(path):
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        raise ValueError("Project directory does not exist")
    for candidate in [p] + list(p.parents):
        if (candidate / DOCS / "STATE.md").is_file():
            return candidate
        if (candidate / ".git").exists():
            return candidate
    return p


class Store:
    def __init__(self, project, home=None):
        self.root = project_root(project)
        self.home = Path(home or os.environ.get("CODEX_CONTINUITY_HOME") or
                         Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "continuity").expanduser().resolve()
        self.key = digest(str(self.root).encode())[:24]
        self.base = self.home / "projects" / self.key
        self.docs = self.root / DOCS

    def safety(self):
        home = Path.home().resolve()
        if self.root in {Path("/"), home, home / "Desktop", home / "Documents", home / "Downloads", self.home, self.home.parent}:
            raise ValueError("Choose a specific project, not a home/system/backup directory")
        for path in [self.docs, self.docs / "tasks", self.docs / "history"]:
            if path.is_symlink():
                raise ValueError("Project records must not be symlinks")
        if self.root == self.home or self.home in self.root.parents:
            raise ValueError("Cannot snapshot the backup store itself")

    @contextlib.contextmanager
    def lock(self):
        self.safety()
        self.base.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(self.base / "lock", "a") as handle:
            until = time.monotonic() + 4
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() > until:
                        raise RuntimeError("Another checkpoint operation is busy; retry")
                    time.sleep(0.03)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def meta(self):
        return read_json(self.base / "project.json", {"version": VERSION, "root": str(self.root), "head": None, "tasks": {}})

    def save(self, m):
        write_json(self.base / "project.json", m)

    def node(self, ident):
        if not ID.fullmatch(ident):
            raise ValueError("Invalid checkpoint ID")
        n = read_json(self.base / "nodes" / (ident + ".json"))
        if not n:
            raise ValueError("Checkpoint not found locally: " + ident)
        if n.get("root") != str(self.root):
            raise ValueError("Checkpoint belongs to another project")
        if n.get("expired"):
            raise ValueError("Temporary checkpoint expired; history remains, contents unavailable")
        return n

    def blob(self, sha):
        if not re.fullmatch(r"[a-f0-9]{64}", sha):
            raise ValueError("Invalid checkpoint object ID")
        data = zlib.decompress((self.base / "objects" / sha[:2] / sha[2:]).read_bytes())
        if digest(data) != sha:
            raise ValueError("Checkpoint content failed integrity check")
        return data

    def safe_path(self, rel):
        p = Path(rel)
        if p.is_absolute() or ".." in p.parts or not p.parts:
            raise ValueError("Paths must be project-relative")
        current = self.root
        for part in p.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("Symlink paths cannot be restored: " + rel)
        if self.root not in current.resolve().parents:
            raise ValueError("Path outside project")
        return current

    def excluded(self, rel):
        p = Path(rel)
        if any(part in SKIP_DIRS or part in {".ssh", ".aws", ".gnupg"} for part in p.parts):
            return True
        return any(fnmatch.fnmatch(p.name.lower(), pattern) for pattern in SECRET)

    def scan(self, max_mb=20, persist=False):
        self.safety()
        start = time.monotonic()
        cached = read_json(self.base / "scan-cache.json", {})
        new_cache, files, skipped = {}, {}, []
        total = 0
        tracked = git(self.root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
        if tracked is not None:
            paths = sorted(set(x for x in tracked.split("\0") if x))
        else:
            paths = []
            for directory, dirs, names in os.walk(self.root, followlinks=False):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not (Path(directory) / d).is_symlink()
                           and (Path(directory) / d).resolve() != self.home]
                for name in names:
                    paths.append((Path(directory) / name).relative_to(self.root).as_posix())
                if len(paths) > 15000 or time.monotonic() - start > 6:
                    raise RuntimeError("Project scan exceeds budget; choose a smaller project scope")
        for rel in paths:
            if time.monotonic() - start > 6:
                raise RuntimeError("Snapshot exceeds time budget; split the project or narrow scope")
            if self.excluded(rel):
                continue
            try:
                p = self.safe_path(rel)
                if not p.exists():
                    continue
                s = p.lstat()
                if not stat.S_ISREG(s.st_mode):
                    skipped.append({"path": rel, "reason": "not a regular file"})
                    continue
                if s.st_nlink > 1:
                    skipped.append({"path": rel, "reason": "hard-linked file"})
                    continue
                if s.st_size > max_mb * 1024 * 1024:
                    skipped.append({"path": rel, "reason": "above file size limit"})
                    continue
                total += s.st_size
                if total > 512 * 1024 * 1024 or len(files) > 10000:
                    raise RuntimeError("Snapshot exceeds 512 MiB/10000 files; choose smaller scope")
                fingerprint = [s.st_mtime_ns, s.st_ctime_ns, s.st_size, s.st_ino, stat.S_IMODE(s.st_mode)]
                old = cached.get(rel)
                if old and old.get("stat") == fingerprint and (self.base / "objects" / old["sha"][:2] / old["sha"][2:]).is_file():
                    sha = old["sha"]
                else:
                    data = p.read_bytes()
                    after = p.lstat()
                    if [after.st_mtime_ns, after.st_ctime_ns, after.st_size, after.st_ino, stat.S_IMODE(after.st_mode)] != fingerprint:
                        raise RuntimeError("File changed during snapshot: " + rel)
                    sha = digest(data)
                    obj = self.base / "objects" / sha[:2] / sha[2:]
                    if persist and not obj.exists():
                        atomic(obj, zlib.compress(data))
                files[rel] = {"sha": sha, "mode": stat.S_IMODE(s.st_mode) & 0o777, "size": s.st_size}
                new_cache[rel] = {"stat": fingerprint, "sha": sha}
            except ValueError as exc:
                skipped.append({"path": rel, "reason": str(exc)})
        if persist:
            write_json(self.base / "scan-cache.json", new_cache)
        return files, skipped

    def checkpoint(self, m, task, label, pin=False, restored_from=None, max_mb=20):
        files, skipped = self.scan(max_mb=max_mb, persist=True)
        parent = m.get("head")
        previous = self.node(parent) if parent else None
        if previous and previous["files"] == files and previous.get("skipped") == skipped and not pin and not restored_from:
            return previous
        ident = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:10]
        n = {"id": ident, "parent": parent, "restored_from": restored_from, "root": str(self.root),
             "created": stamp(), "task": task, "label": label, "pinned": pin, "files": files,
             "skipped": skipped, "coverage": "regular non-secret non-generated files; gitignored paths excluded in Git projects",
             "git_head": git(self.root, "rev-parse", "HEAD"), "git_branch": git(self.root, "branch", "--show-current"),
             "task_state": m["tasks"].get(task, {})}
        write_json(self.base / "nodes" / (ident + ".json"), n)
        m["head"] = ident
        self.save(m)
        return n

    def render(self, m, task=None):
        self.safety()
        lines = ["# 项目状态 / Project continuity", "", "此摘要由 continuity 工具维护；详细记录按任务读取。", "",
                 "项目：" + str(self.root), "更新时间：" + stamp(), "",
                 "本地检查点库：" + str(self.base), "",
                 "检查点不会自动同步到其他设备；接手前核验实际文件、分支及快照可用性。", "",
                 "## 任务索引", ""]
        tasks = sorted(m["tasks"].items(), key=lambda item: item[1].get("updated", ""), reverse=True)
        for key, value in tasks[:20]:
            goal = value["goal"].replace("\n", " ")[:140]
            lines.append(f"- [{key}](tasks/{key}.md)：{value['status']}；{goal}")
        if len(tasks) > 20:
            lines.append("- 更多任务见 tasks/；不要一次性读取所有历史。")
        atomic(self.docs / "STATE.md", ("\n".join(lines) + "\n").encode())
        if task:
            t = m["tasks"][task]
            text = ["# " + task, "", "目标：" + t["goal"], "状态：" + t["status"], "更新时间：" + t["updated"],
                    "", "## 当前结果", t.get("summary", "尚未记录完成结果"), "", "## 下一步", t.get("next", "核验实际状态后继续"),
                    "", "## 验证证据", t.get("validation", "未验证；不能声称完成或用户已认可"),
                    "", "## 决策与边界", t.get("decisions", "无新增决策"), "",
                    "## 版本与交接", "开始检查点：" + str(t.get("start_node")), "最近交接检查点：" + str(t.get("handoff_node")),
                    "", "本地存储：" + str(self.base), "用户满意状态须由用户明确确认，不能由测试通过推断。"]
            atomic(self.docs / "tasks" / (task + ".md"), ("\n".join(text) + "\n").encode())

    def bind(self, session, task):
        if session:
            write_json(self.base / "sessions" / (digest(session.encode()) + ".json"), {"task": task})

    def binding(self, session):
        return read_json(self.base / "sessions" / (digest(session.encode()) + ".json"), {})

    def begin(self, task, goal, session=""):
        if not ID.fullmatch(task):
            raise ValueError("Task ID: letters, numbers, dot, underscore, hyphen; max 100")
        with self.lock():
            m = self.meta()
            if task in m["tasks"] and m["tasks"][task]["goal"] != goal:
                raise ValueError("Task exists with another goal; use a new task ID")
            if task not in m["tasks"]:
                m["tasks"][task] = {"goal": goal, "status": "active", "updated": stamp()}
                n = self.checkpoint(m, task, "before task")
                m["tasks"][task]["start_node"] = n["id"]
                self.save(m)
            self.bind(session, task)
            self.render(m, task)
            first = self.node(m["tasks"][task]["start_node"])
            return {"task": task, "checkpoint": first["id"], "records": str(self.docs),
                    "coverage": first["coverage"], "skipped": first["skipped"]}

    def note(self, task, summary, next_step, validation, decision, status_value, handoff=False):
        with self.lock():
            m = self.meta()
            if task not in m["tasks"]:
                raise ValueError("Task not registered")
            t = m["tasks"][task]
            t.update(summary=summary, next=next_step, validation=validation, status=status_value, updated=stamp())
            if decision:
                t["decisions"] = (t.get("decisions", "") + "\n- " + stamp() + " " + decision).strip()
            n = self.checkpoint(m, task, "handoff" if handoff else "task update")
            t["handoff_node"] = n["id"]
            self.save(m)
            self.render(m, task)
            revision = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
            write_json(self.docs / "history" / task / (revision + ".json"), {"checkpoint": n["id"], "task": t})
            if status_value == "complete":
                self.prune(m)
            return {"checkpoint": n["id"], "status": status_value, "skipped": n["skipped"], "handoff": str(self.docs / "tasks" / (task + ".md")),
                    "new_chat_prompt": f"继续本项目任务 {task}。先读取 {DOCS}/STATE.md 和 {DOCS}/tasks/{task}.md，核验实际文件、分支和本地检查点，再从下一步继续。"}

    def prune(self, m):
        nodes = [read_json(p) for p in (self.base / "nodes").glob("*.json")]
        nodes.sort(key=lambda n: n["created"], reverse=True)
        keep = {n["id"] for n in nodes[:50]} | {m.get("head")}
        for t in m["tasks"].values():
            keep.update([t.get("start_node"), t.get("handoff_node")])
        active = {key for key, t in m["tasks"].items() if t["status"] != "complete"}
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
        for n in nodes:
            if n.get("pinned") or n["task"] in active or dt.datetime.fromisoformat(n["created"]) >= cutoff:
                keep.add(n["id"])
        live = set()
        expired = 0
        for n in nodes:
            if n["id"] in keep and not n.get("expired"):
                live.update(v["sha"] for v in n["files"].values())
            elif not n.get("expired"):
                n["expired"] = True
                n["expired_at"] = stamp()
                n["files"] = {}
                write_json(self.base / "nodes" / (n["id"] + ".json"), n)
                expired += 1
        # Only our own SHA-addressed objects, under this exact registered project.
        for p in (self.base / "objects").glob("*/*"):
            sha = p.parent.name + p.name
            if re.fullmatch(r"[a-f0-9]{64}", sha) and sha not in live and not p.is_symlink():
                p.unlink()
        return {"expired_temporary_nodes": expired, "history_preserved": True}

    def status(self):
        m = self.meta()
        nodes = [read_json(p) for p in (self.base / "nodes").glob("*.json")]
        nodes.sort(key=lambda n: n["created"])
        return {"version": VERSION, "root": str(self.root), "head": m["head"], "tasks": m["tasks"],
                "nodes": [{k: n.get(k) for k in ("id", "parent", "restored_from", "task", "label", "pinned", "expired")} for n in nodes]}

    def restore(self, ident, paths, apply=False, all_protected=False):
        if not self.base.exists():
            raise ValueError("No project checkpoints")
        with self.lock():
            m = self.meta()
            target, head = self.node(ident), self.node(m["head"])
            current, skipped = self.scan()
            known = set(target["files"]) | set(head["files"])
            selected = set()
            for rel in paths:
                self.safe_path(rel)
                matches = {p for p in known if p == rel or p.startswith(rel.rstrip("/") + "/")}
                if not matches:
                    raise ValueError("Path not covered by checkpoints: " + rel)
                selected.update(matches)
            if all_protected or not paths:
                selected = known
            changed = sorted(p for p in selected if current.get(p) != target["files"].get(p))
            skipped_paths = {x["path"] for x in skipped + target.get("skipped", []) + head.get("skipped", [])}
            conflicts = [p for p in changed if current.get(p) != head["files"].get(p) or p in skipped_paths]
            staged = set((git(self.root, "diff", "--cached", "--name-only", "-z") or "").split("\0"))
            conflicts = sorted(set(conflicts) | (staged & set(changed)))
            preview = {"target": ident, "current": m["head"], "paths": changed, "conflicts": conflicts,
                       "applied": False, "all_other_nodes_preserved": True}
            if not apply:
                return preview
            if not paths and not all_protected:
                raise ValueError("Apply requires explicit --path or --all-protected")
            if conflicts:
                raise ValueError("Later/uncheckpointed/staged/unsupported changes conflict: " + ", ".join(conflicts))
            if not changed:
                return preview
            # Validate every target object and destination BEFORE the first mutation.
            payload = {p: self.blob(target["files"][p]["sha"]) if p in target["files"] else None for p in changed}
            old = {}
            for rel in changed:
                p = self.safe_path(rel)
                if p.exists() and not p.is_file():
                    raise ValueError("Destination is not a regular file: " + rel)
                old[rel] = p.read_bytes() if p.exists() else None
                if old[rel] is not None and digest(old[rel]) != current[rel]["sha"]:
                    raise ValueError("Concurrent file change: " + rel)
            recovery = self.checkpoint(m, target["task"], "before scoped restore", pin=True)
            written = []
            try:
                for rel in changed:
                    p = self.safe_path(rel)
                    actual = p.read_bytes() if p.exists() else None
                    if actual != old[rel]:
                        raise ValueError("Concurrent file change: " + rel)
                    if payload[rel] is None:
                        if p.exists():
                            p.unlink()
                    else:
                        atomic(p, payload[rel], target["files"][rel]["mode"] & 0o777)
                    written.append(rel)
                n = self.checkpoint(m, target["task"], "scoped restore", restored_from=ident)
            except Exception:
                for rel in reversed(written):
                    p = self.safe_path(rel)
                    actual = p.read_bytes() if p.exists() else None
                    if actual != payload[rel]:
                        continue  # Never overwrite a concurrent third-party write.
                    if old[rel] is None:
                        if p.exists():
                            p.unlink()
                    else:
                        atomic(p, old[rel], current[rel]["mode"] & 0o777)
                raise
            preview.update(applied=True, checkpoint=n["id"], recovery_checkpoint=recovery["id"])
            t = m["tasks"].get(target["task"])
            if t:
                t.update(status="active", updated=stamp(),
                         summary="局部恢复节点 " + ident + "；文件：" + ", ".join(changed),
                         validation="文件内容已恢复并建立新节点；功能及依赖尚需重新验证。",
                         next="核验恢复结果与其他任务的依赖；保留所有原有分支。", handoff_node=n["id"])
                self.save(m)
                self.render(m, target["task"])
            write_json(self.base / "events" / (uuid.uuid4().hex + ".json"),
                       {"event": "restore", "at": stamp(), **preview})
            return preview


def read_only_command(command):
    if any(token in command for token in (">", "\n", ";", "&&", "||", "$(`", "$(", "`")):
        return False
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if not parts:
        return True
    if parts[0] in {"pwd", "ls", "rg", "cat", "head", "tail", "wc", "stat", "which"}:
        return True
    return parts[0] == "git" and len(parts) > 1 and parts[1] in {"status", "diff", "log", "show", "rev-parse", "ls-files"}


def hook(payload, home=None):
    event = payload.get("hook_event_name", "")
    session = payload.get("session_id", "")
    store = Store(payload.get("cwd") or os.getcwd(), home)
    if event in {"SessionStart", "UserPromptSubmit"}:
        # Read-only lifecycle events NEVER create a project or update records.
        if not (store.docs / "STATE.md").is_file():
            return {}
        binding = store.binding(session)
        context = f"项目连续性：先读 {DOCS}/STATE.md，再按需读相关任务。"
        if binding.get("task"):
            context += f" 本会话绑定任务 {binding['task']}。"
        else:
            context += " 本会话尚未绑定任务；不要把其他进行中的任务当作自己的。"
        context += " 只讨论/只读请求不得创建或更新档案。"
        return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}}
    binding = store.binding(session)
    if event == "PreToolUse":
        name = payload.get("tool_name", "")
        inp = payload.get("tool_input", {})
        if isinstance(inp, str):
            command = inp
        else:
            command = inp.get("command", inp.get("cmd", ""))
        if "continuity.py" in command or read_only_command(command) and name in {"Bash", "exec_command"}:
            return {}
        if name not in {"Bash", "exec_command", "apply_patch", "Edit", "Write"}:
            return {}
        if not binding.get("task"):
            # Don't fabricate a task goal or mutate files in consultation-only chats.
            msg = "开始实际修改前，按全局 project-continuity 规则运行 begin 登记本任务并保存恢复点。只读操作无需登记。"
            if name in {"apply_patch", "Edit", "Write"}:
                return {"hookSpecificOutput": {"hookEventName": event, "permissionDecision": "deny", "permissionDecisionReason": msg}}
            return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": msg}}
        task = binding["task"]
        with store.lock():
            m = store.meta()
            if m["tasks"].get(task, {}).get("status") == "complete":
                return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": "先为新的修改登记新任务；上一任务已完成。"}}
            turn = payload.get("turn_id") or "unknown"
            if not binding.get("dirty") or binding.get("before_turn") != turn:
                before = store.checkpoint(m, task, "before mutation turn")
                binding.update(before_turn=turn, before_node=before["id"], dirty=True)
                write_json(store.base / "sessions" / (digest(session.encode()) + ".json"), binding)
        return {}
    if event not in {"Stop", "PreCompact", "Interrupt"} or not binding.get("dirty"):
        return {}
    if event == "Interrupt":
        # Keep interrupt cheap; the already-saved before-node remains recoverable.
        return {"systemMessage": "本轮修改前检查点已保留；中断后的实际文件状态将在恢复任务时核验。"}
    with store.lock():
        m = store.meta()
        task = binding["task"]
        n = store.checkpoint(m, task, "before compaction" if event == "PreCompact" else "turn end")
        before = store.node(binding["before_node"])
        changed = sorted(p for p in set(before["files"]) | set(n["files"]) if before["files"].get(p) != n["files"].get(p))
        write_json(store.base / "events" / (uuid.uuid4().hex + ".json"),
                   {"event": event, "task": task, "at": stamp(), "before": before["id"], "after": n["id"], "changed_paths": changed})
        binding["dirty"] = False
        write_json(store.base / "sessions" / (digest(session.encode()) + ".json"), binding)
        t = m["tasks"][task]
        if changed and t.get("handoff_node") != n["id"]:
            if event == "Stop" and not payload.get("stop_hook_active"):
                return {"decision": "block", "reason": f"本轮文件检查点已保存。请为任务 {task} 更新简短 summary、next、validation 后结束；不要扩大工作范围或把未验证工作标成完成。仅补记录一次。"}
            return {"systemMessage": f"任务 {task} 已保存文件检查点；语义交接可能未更新，接手时须核验。"}
    return {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=os.getcwd())
    parser.add_argument("--home", help="Override private data store (primarily for tests)")
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("begin")
    p.add_argument("--task", required=True)
    p.add_argument("--goal", required=True)
    p.add_argument("--session", default=os.environ.get("CODEX_THREAD_ID", ""))
    sub.add_parser("status")
    p = sub.add_parser("checkpoint")
    p.add_argument("--task", required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--pin", action="store_true")
    p.add_argument("--max-file-mb", type=int, default=20)
    for name in ("note", "handoff"):
        p = sub.add_parser(name)
        p.add_argument("--task", required=True)
        p.add_argument("--summary", required=True)
        p.add_argument("--next", required=True)
        p.add_argument("--validation", default="未验证")
        p.add_argument("--decision", default="")
        p.add_argument("--status", choices=["active", "blocked", "complete"], default="active")
    p = sub.add_parser("restore")
    p.add_argument("--node", required=True)
    p.add_argument("--path", action="append", default=[])
    p.add_argument("--all-protected", action="store_true")
    p.add_argument("--apply", action="store_true")
    sub.add_parser("hook")
    args = parser.parse_args()
    try:
        if args.action == "hook":
            payload = json.load(sys.stdin)
            try:
                result = hook(payload, args.home)
            except Exception as exc:
                if payload.get("hook_event_name") == "PreToolUse":
                    result = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                              "permissionDecisionReason": "检查点保存失败，尚未执行本次工具操作：" + str(exc)}}
                else:
                    result = {"systemMessage": "连续性记录异常，请检查项目实际状态：" + str(exc)}
        else:
            store = Store(args.project, args.home)
            if args.action == "begin":
                result = store.begin(args.task, args.goal, args.session)
            elif args.action == "status":
                result = store.status()
            elif args.action in {"note", "handoff"}:
                result = store.note(args.task, args.summary, args.next, args.validation, args.decision, args.status, args.action == "handoff")
            elif args.action == "restore":
                result = store.restore(args.node, args.path, args.apply, args.all_protected)
            else:
                with store.lock():
                    m = store.meta()
                    if args.task not in m["tasks"]:
                        raise ValueError("Register task with begin first")
                    n = store.checkpoint(m, args.task, args.label, args.pin, max_mb=args.max_file_mb)
                    result = {"checkpoint": n["id"], "pinned": n["pinned"], "files": len(n["files"]), "skipped": n["skipped"]}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
