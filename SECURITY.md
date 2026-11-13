# Security policy

## Reporting a vulnerability

Please report security problems privately: open the **Security** tab of this repository and choose
**Report a vulnerability** (GitHub private vulnerability reporting). Please do not open a public issue
for a security problem. You will get an answer in the same thread.

## Supported versions

Only the latest release is supported. Fixes land in a new release; older versions are not patched.

## What this plugin accesses

- **Files it reads:** session transcripts of every project in `~/.claude/projects/*/*.jsonl` (your messages, interrupts, tool rejections and the last lines of Claude's reply before each message), your `CLAUDE.md` files (global, in the folders you worked in, and under `project_roots`), skills, memory files, `~/.claude/settings.json`, hooks, agents, commands and output styles, the optional `wiki_dir` and `raw_dir`, and its settings file `~/.config/self-improve/config.json` (created owner-only).
- **Files it writes:** run state, copies of the message excerpts it extracted, review files, the change log and backups under `~/.claude/self-improve/`. Copies follow your umask and are not made owner-only. Without your approval it only makes the AUTO fixes: wiki links, index and log lines, dates and typos in the wiki, `MEMORY.md` index lines, and moving sync-conflict copies and `*.bak-*` files older than 14 days into its own folders. An item you approve with `[x]` can edit or create any file it names, including skills, `CLAUDE.md`, settings and hooks; a `.bak-<time>` copy is kept next to an existing file first (for wiki pages, in its own backup folder).
- **Programs it starts:** the commands you set (pre-run, extra lint, notify, launch), split without a shell, plus `claude agents --json` and `ps`. `launch_command` opens a new Claude session that records your review choices or applies the approved items without asking.
- **Guard hook:** on every Edit, Write, Bash, Monitor and PowerShell call in every session, while a run is open, it blocks writes it recognizes outside the run's allowed files. It is a guardrail, not a sandbox.
- **Network:** none from the plugin's scripts. The analysis runs in Claude subagents, so the message excerpts go to Anthropic like the rest of a session.
