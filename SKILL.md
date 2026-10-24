---
name: project-continuity
description: Maintain project progress, concise task handoffs, and recoverable local file checkpoints across Codex conversations. Use when implementing continuing project work, changing established results, preparing a handoff, or restoring an earlier version. Read-only consultation does not authorize record or snapshot writes.
---

# Project continuity

Use Python 3.9+ to run `scripts/continuity.py` relative to this SKILL.md's actual directory (`C` below is shorthand only). Quote paths containing spaces. If the global AGENTS block provides a helper command, use that command: it pins the correct private data store. Otherwise the helper uses `$CODEX_HOME/continuity`, falling back to `~/.codex/continuity`. It uses the current project, or `--project /absolute/project` **before** the subcommand. All output is JSON. Only use actual returned checkpoint IDs. macOS and Linux are supported; native Windows is not supported by this version's POSIX file locking.

## Start and resume

For project implementation, read `.codex-continuity/STATE.md` and the relevant `tasks/<task>.md` if present. Also honor the project's existing instructions. Load detailed history only as needed. Compare records with actual files and Git state. New user instructions take precedence over stale recorded intent.

Before the first mutation, run:

`C begin --task descriptive-unique-id --goal "User's actual goal; scope and things to preserve"`

The helper binds `CODEX_THREAD_ID` when available. If absent, use `--session` with the actual session ID supplied by the runtime; never invent it. A new conversation resuming an existing task can call begin with its **exact recorded goal** to bind it. For a different assignment use a new task ID. Do not silently bind to another active task.

Snapshot scope is the concrete Git root or registered project folder. Never enroll a home directory, Desktop/Documents collection, or Codex configuration directory as a project. Global configuration deployment uses its own explicit deployment manifest and backup, not a broad home snapshot. Follow the returned `skipped` coverage and the coverage limits in [operations.md](references/operations.md).

## During work

Every implementation task gets a baseline. For a shared component/API, core dependency upgrade, bulk deletion/move, broad redesign, or replacement of a user-approved result, add an explicitly labelled checkpoint first:

`C checkpoint --task TASK --label "before navigation redesign" --pin`

Save meaningful stages, not every read or tiny edit. Hooks, when trusted and supported, save once before a modifying turn and again at its end. They cannot cover external tools, arbitrary shell side effects, or every specialized execution path. For unsupported mutations call checkpoint explicitly BEFORE the action. A checkpoint timeout/failure is not evidence of a successful backup.

Respect existing user changes, active task ownership, and the requested scope. Independent experimental work may use separate worktrees when appropriate; uncommitted files are not automatically present in a new worktree. Do not broaden a local change into project-wide redesign without a concrete need within the user's request.

At a decision, verified stage, blocker, or handoff run:

`C note --task TASK --summary "What actually changed, paths and unresolved issues" --next "Concrete next action" --validation "Command/scenario and observed result; say not run when applicable" --decision "Only when a decision changed" --status active`

Use `complete` only when the actual assignment is done, `blocked` for waiting items. Omit `--decision` when none changed. Record external job IDs, pending user actions and running services in summary/next when relevant; do not restart or resubmit them merely because a new conversation started. Store no credentials or raw private transcripts.

## Handoff and context

`C handoff` accepts the same arguments as note and returns a ready-to-use new-chat prompt. Confirm records and referenced snapshots exist before saying handoff is ready. Keep active summary short (about 100 lines); task details/history remain separate. Completion does not discard task records.

Recommend a new chat at a useful phase boundary or after demonstrated context confusion; do not insist on it just because a conversation is long. Do not fabricate token percentages. Remind once per handoff, keeping the user's choice. After compaction read the task again and verify working state. Do not automatically create, message, close or archive chats.

## Nodes and recovery

Read [operations.md](references/operations.md) before recovery, pinning a user-approved baseline, handling excluded files, or debugging hooks. `C status` is read-only and lists nodes/parents/tasks.

Recovery defaults to a preview:

`C restore --node ID --path relative/file`

Only apply a user-requested recovery whose scope is clear:

`C restore --node ID --path relative/file --apply`

The tool refuses conflicts with uncheckpointed or staged changes. Do not bypass refusal or overwrite a whole file to erase a later valid change. Instead compare the task's before/after/current versions, prepare a narrow patch, checkpoint, and validate dependencies. Restoring creates a new node with `restored_from`; the old node and later branches remain available. Explicit whole protected-scope recovery requires `--all-protected`; it does not include excluded files or external systems.

Pin a baseline when the user explicitly says to retain it. Technical verification is not user approval. Ordinary completion does not authorize Git pushes, deployments, database mutations, or unrelated configuration changes.
