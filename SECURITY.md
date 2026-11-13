# Security policy

## Reporting a vulnerability

Please report security problems privately: open the **Security** tab of this repository and choose
**Report a vulnerability** (GitHub private vulnerability reporting). Please do not open a public issue
for a security problem. You will get an answer in the same thread.

## Supported versions

Only the latest release is supported. Fixes land in a new release; older versions are not patched.

## What this plugin accesses

- **Files it reads:** your session transcripts `~/.claude/projects/*/*.jsonl` (your own messages, interrupts and tool rejections), your `CLAUDE.md` files, skills and memory files, and its settings file `~/.config/self-improve/config.json`.
- **Files it writes:** its state, review files, backups and change log under `~/.claude/self-improve/`. It edits a `CLAUDE.md`, skill or memory file only for a fixed list of small fixes, or after you approve the item, and keeps a backup next to the file first.
- **Programs it starts:** only the commands you set in the settings file (such as a pre-run command, a lint command or the review launcher), split without a shell.
- **Guard hook:** while a run is open, a PreToolUse hook blocks writes it recognizes outside the run's allowed files. It is a guardrail, not a sandbox.
- **Network:** none from the plugin's scripts.
