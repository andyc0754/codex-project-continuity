#!/usr/bin/env python3
"""Deploy reviewed continuity files, with exact backups and recoverable rollback."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import uuid

sys.path.insert(0, str(Path(__file__).parent / "skills" / "project-continuity" / "scripts"))
from continuity import atomic, read_json, write_json

SOURCE = Path(__file__).resolve().parent
START = "<!-- project-continuity:start v1 -->"
END = "<!-- project-continuity:end -->"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def plan(codex):
    skill = codex / "skills" / "project-continuity"
    source_skill = SOURCE / "skills" / "project-continuity"
    program = str(skill / "scripts" / "continuity.py")
    helper = shlex.join([sys.executable, program, "--home", str(codex / "continuity")])
    targets = {
        skill / "SKILL.md": (source_skill / "SKILL.md").read_bytes(),
        skill / "references" / "operations.md": (source_skill / "references" / "operations.md").read_bytes(),
        skill / "scripts" / "continuity.py": (source_skill / "scripts" / "continuity.py").read_bytes(),
    }
    agents = codex / "AGENTS.md"
    original = agents.read_text() if agents.exists() else ""
    rule = (SOURCE / "templates" / "global-rules.md").read_text().strip()
    rule = rule.replace("__SKILL_DIR__", str(skill)).replace("__HELPER_COMMAND__", helper)
    if START in original:
        if END not in original:
            raise ValueError("Existing managed block is incomplete")
        before, rest = original.split(START, 1)
        _, after = rest.split(END, 1)
        combined = before + rule + after
    else:
        combined = original.rstrip() + ("\n\n" if original.strip() else "") + rule + "\n"
    targets[agents] = combined.encode()
    hooks_path = codex / "hooks.json"
    hooks = read_json(hooks_path, {"hooks": {}})
    table = hooks.setdefault("hooks", {})
    command = helper + " hook"
    for event in ("SessionStart", "UserPromptSubmit", "PreToolUse", "Stop", "PreCompact", "Interrupt"):
        groups = table.setdefault(event, [])
        for group in groups:
            group["hooks"] = [h for h in group.get("hooks", []) if program not in h.get("command", "")]
        groups[:] = [g for g in groups if g.get("hooks")]
        entry = {"type": "command", "command": command, "timeout": 10 if event != "Interrupt" else 3}
        if event in {"SessionStart", "UserPromptSubmit", "PreToolUse"}:
            entry["additionalContextLimit"] = 1600
        group = {"hooks": [entry]}
        if event == "PreToolUse":
            group["matcher"] = "^(Bash|exec_command|apply_patch|Edit|Write)$"
        groups.append(group)
    targets[hooks_path] = (json.dumps(hooks, ensure_ascii=False, indent=2) + "\n").encode()
    return targets


def install(codex):
    codex = codex.expanduser().resolve()
    targets = plan(codex)
    changes = {p: data for p, data in targets.items() if not p.exists() or p.read_bytes() != data}
    if not changes:
        return {"changed": False, "message": "Already deployed; no files changed"}
    for p in changes:
        if p.is_symlink() or any(parent.is_symlink() for parent in p.parents if parent != codex):
            raise ValueError("Refusing symlink deployment target: " + str(p))
    ident = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    receipt = codex / "continuity" / "deployments" / ident
    receipt.mkdir(parents=True, mode=0o700)
    records = []
    for i, (path, data) in enumerate(changes.items()):
        old = path.read_bytes() if path.exists() else None
        backup = str(i) + ".before"
        if old is not None:
            atomic(receipt / backup, old)
        records.append({"path": str(path), "before_sha": sha(old) if old is not None else None,
                        "backup": backup if old is not None else None, "installed_sha": sha(data),
                        "before_mode": path.stat().st_mode & 0o777 if path.exists() else None})
    config = codex / "config.toml"
    manifest = {"id": ident, "created": dt.datetime.now(dt.timezone.utc).isoformat(), "source": str(SOURCE),
                "codex_home": str(codex), "status": "prepared", "files": records,
                "config_observed_sha": sha(config.read_bytes()) if config.exists() else None,
                "config_modified": False, "hook_trust": "requires runtime review; never bypassed"}
    write_json(receipt / "manifest.json", manifest)
    try:
        for path, data in changes.items():
            atomic(path, data)
        if any(sha(Path(r["path"]).read_bytes()) != r["installed_sha"] for r in records):
            raise ValueError("Read-back verification failed")
        manifest["status"] = "installed"
        write_json(receipt / "manifest.json", manifest)
    except Exception as exc:
        manifest["status"] = "partial-install"
        manifest["error"] = str(exc)
        write_json(receipt / "manifest.json", manifest)
        raise
    return {"changed": True, "deployment": ident, "manifest": str(receipt / "manifest.json"),
            "files": [r["path"] for r in records], "hook_trust": manifest["hook_trust"]}


def rollback(codex, ident, apply=False):
    import re
    if not re.fullmatch(r"[A-Za-z0-9-]+", ident):
        raise ValueError("Invalid deployment ID")
    codex = codex.expanduser().resolve()
    receipt = codex / "continuity" / "deployments" / ident
    m = read_json(receipt / "manifest.json")
    if not m or m.get("codex_home") != str(codex):
        raise ValueError("Deployment receipt missing or belongs to another installation")
    allowed = set(plan(codex))
    conflicts = []
    for r in m["files"]:
        p = Path(r["path"])
        if p not in allowed or p.is_symlink():
            raise ValueError("Unexpected rollback target")
        if not p.exists() or sha(p.read_bytes()) != r["installed_sha"]:
            conflicts.append(str(p))
        if r["backup"]:
            backup = receipt / r["backup"]
            if backup.parent != receipt or sha(backup.read_bytes()) != r["before_sha"]:
                raise ValueError("Invalid deployment backup")
    result = {"deployment": ident, "conflicts": conflicts, "files": m["files"], "applied": False}
    if not apply:
        return result
    if conflicts:
        raise ValueError("Installed files changed since deployment; refusing overwrite: " + ", ".join(conflicts))
    for i, r in enumerate(m["files"]):
        p = Path(r["path"])
        recovered = receipt / "recovered" / (str(i) + ".installed")
        recovered.parent.mkdir(exist_ok=True, mode=0o700)
        # Keep every removed installed file recoverable, including newly-created targets.
        os.replace(p, recovered)
        if r["backup"]:
            atomic(p, (receipt / r["backup"]).read_bytes(), r["before_mode"] or 0o600)
    m["status"] = "rolled-back"
    write_json(receipt / "manifest.json", m)
    result["applied"] = True
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["install", "rollback"])
    p.add_argument("--codex-home", default=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    p.add_argument("--deployment")
    p.add_argument("--apply", action="store_true")
    a = p.parse_args()
    try:
        result = install(Path(a.codex_home)) if a.action == "install" else rollback(Path(a.codex_home), a.deployment or "", a.apply)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
