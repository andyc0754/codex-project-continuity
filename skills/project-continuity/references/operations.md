# Operations and coverage

## Storage and portability

The global rule is short; this skill supplies the workflow. The helper records portable progress under `<project>/.codex-continuity/` (STATE.md, task files, immutable note revisions). Existing project documents remain authoritative for their domains; link to them in task notes rather than duplicating requirements.

Actual checkpoint bytes are compressed and SHA-256-addressed under `$CODEX_HOME/continuity/projects/<project-path-hash>/` (default `~/.codex/continuity/projects/`). The installer can explicitly target another Codex home and pins its data-store path in the generated helper command. Unchanged bytes are reused. Nodes have parent, task, label, timestamp, source Git commit/branch, coverage and optional restored_from. No remote service, Git commit, stage, reset, checkout or stash operation is performed. Ordinary Git status/read commands identify the existing baseline; file snapshots work without Git too. This independent store is an intentional adaptation from Cline's private-checkpoint approach, avoiding changes to user Git indexes and supporting non-Git folders.

Snapshots are local, not disaster recovery or cross-device sync. A new worktree has a different project-path identity. Its checked-in handoff can describe prior work, but it must not assume the other checkout's snapshots/files are locally available. Never claim code recovery merely because metadata exists; restore checks object hashes.

## Covered and excluded data

Protected: regular project files, working-tree edits and eligible untracked files, including file modes. Missing/new files are represented by manifest differences. Git staging state is recorded indirectly by Git but is not restored or changed; recovery refuses selected staged files.

Excluded by default: `.git`, tool-managed continuity documents, dependencies/build outputs/caches, symlinks, hard links, credentials/private keys, `.env*`, databases, logs, Git-ignored files and files above 20 MiB. Baseline captures progress metadata separately; file restore does not rewind progress to an obsolete conclusion. Directories themselves, empty directories, ACLs/xattrs and external resources are not backed up. Important binaries above the limit can be included with a deliberate `checkpoint --max-file-mb N` (total scan cap 512 MiB); the restore helper's default scan limit means large-file restoration requires a separate reviewed plan, not blind --apply. Prefer app-native versions/exports for large creative assets, database-native backups for data, and exportable versions for cloud documents.

The scan has a bounded runtime and file count. Read errors/races fail the snapshot. Exclusions are not a successful full-project backup. Before changing any excluded item, arrange a suitable recovery method and disclose coverage limits. Never auto-copy credentials into the general checkpoint store.

## Quiet automation

Only local supported Codex hooks apply. Startup/prompt hooks are read-only and inject a short pointer to project records. They do not initialize a project. Agent instructions register the task at implementation start. Bound implementation tasks checkpoint before the first potentially modifying local tool call per turn, and at Stop or PreCompact; unchanged snapshots are reused. Read-only consultation does not authorize begin/note/snapshot writes. Global rules apply even where hooks are unsupported; then the agent calls the helper explicitly.

PreCompact writes file state, not a model-generated semantic summary. Ordinary note/handoff calls must preserve intent during work. Stop may ask the agent exactly once to update a stale task handoff after detected file changes. There is no endless completion loop. Interrupt only reports the previously saved recovery point; sudden termination can prevent the final snapshot. Code-mode/specialized tools, commands targeting another directory, and external applications may bypass automatic coverage. Their task workflow must checkpoint explicitly.

Hooks require normal Codex trust review via `/hooks`. Never bypass trust, silently approve hashes, or turn off user permissions. Hooks on cloud-orchestrated tasks may not run. New sessions are the reliable point for loading changed global instructions; current chats may need an explicit reread.

## Retention and concurrent tasks

Automatic pruning runs only when a task is explicitly marked complete. It preserves the last 50 nodes, all nodes from the last 30 days, pinned nodes, all nodes belonging to unfinished tasks, every task's start/handoff references and the current head. Expired nodes keep graph metadata but their unreferenced bytes may be removed; they are clearly unavailable. Pinned milestones and restore safety points are retained until explicitly unpinned through a future reviewed maintenance operation. No background scanning of unrelated projects.

One project lock protects tool writes. Each session has its own task binding; a project index contains separate tasks. This does not isolate concurrent edits by other tools. For overlapping tasks use distinct worktrees or coordinate ownership. A file snapshot can contain other tasks' concurrent work and is not proof of authorship; use task descriptions and before/after comparisons for selective recovery.

Restore previews exact paths, verifies object hashes and current contents, refuses later/staged changes, saves a pinned safety point, and writes only selected paths. A failed apply attempts to restore only the bytes it wrote, never a new third-party edit. Actual dependent-feature correctness still needs relevant tests. Forward recovery means loading a saved later node, not inventing future results.

## Provenance

Adapted ideas, not installed third-party frameworks or copied runtime code:
- Planning with Files: separated task/progress notes; our history is durable instead of overwritten. https://github.com/OthmanAdi/planning-with-files/blob/master/docs/workflow.md
- Cline Memory Bank: small active context and durable project records. https://github.com/cline/cline/blob/main/docs/best-practices/memory-bank.mdx
- Cline checkpoint implementation: private recoverable snapshots and explicit coverage. https://github.com/cline/cline/blob/main/sdk/packages/core/src/hooks/checkpoint-hooks.ts
- GSD Core: structured handoff, scoped recovery and dependency awareness. https://github.com/open-gsd/gsd-core/blob/main/gsd-core/workflows/pause-work.md
- Aider: traceable change boundaries; we do not auto-commit user work. https://github.com/Aider-AI/aider/blob/main/aider/website/docs/git.md
- Hook protocol: https://learn.chatgpt.com/docs/hooks

## Deployment trace and rollback

Deployment receipts, original bytes, installed hashes and rollback copies are under `<codex-home>/continuity/deployments/<id>/`. From the retained sharing repository, `python3 deploy.py rollback --deployment ID` previews rollback; add `--apply` only for a requested rollback. Use the same `--codex-home` used for installation if customized. It refuses if any installed target changed since deployment. Newly installed files are moved into the receipt's recovered directory; previous files are restored from backups. Task/checkpoint data is retained. Reload Codex afterward; hook trust for changed definitions may need another review. A skill-only copy does not create a deployment receipt or install global rules/hooks.
