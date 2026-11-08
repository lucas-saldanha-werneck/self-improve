# self-improve

A Claude Code plugin that reads your recent sessions, finds the places where you had to correct Claude more than once, and turns each one into a proposed change to your `CLAUDE.md`, skills or memory files. Small safe fixes are applied right away. Anything that changes how Claude behaves waits for your OK, and only what you approve is applied.

If you keep telling Claude the same thing ("run the tests first", "don't ask, just do it"), the rule is either missing or not strong enough. This plugin finds those repeats so you fix them once.

## What you see

A run ends with a review file. Each item says where the change goes, why, and the exact text that will be swapped:

```markdown
## New

- [ ] **S01 · Show the test output, not just 'it works'** <!-- 20261108-103605/S01 -->
    - Where: `~/.claude/CLAUDE.md` (claude-md)
    - Evidence: 3 times in 2 sessions
    - Why: the rule exists but was skipped twice
    - Risk: low
    - Exact change: replace "- Run the tests before saying it works." with "- Run the tests before saying it works, and paste the last lines of the output."
```

You decide by marking `[x]` (apply), `[-]` (reject for good) or leaving `[ ]` (decide later). Or run `/self-improve:human-review` and answer each item with buttons. Questions the plugin can't settle alone go to a second file, with an `**Answer:**` line for you to fill in.

After you approve, `/self-improve:execute-reviewed` applies the item, keeps a backup (next to the file; wiki pages are backed up under `~/.claude/self-improve/backups/`) and adds a line to `change-log.md`:

```
- 10:36 · APPROVED · 20261108-103605/S01 · `~/.claude/CLAUDE.md`: exact patch (39->79 chars) (backup `~/.claude/CLAUDE.md.bak-20261108103605`)
```

## Install

```
/plugin marketplace add lucas-saldanha-werneck/self-improve
/plugin install self-improve@self-improve
```

Needs `python3` (3.9 or newer, standard library only) on your PATH.

## Use

| Command | What it does |
|---|---|
| `/self-improve:improve-system` | One run: applies what you approved last time, reads the sessions since the last run (the first run covers 7 days), applies safe fixes, writes the review files. |
| `/self-improve:human-review` | Shows each waiting item as buttons and records your choices. Applies nothing. |
| `/self-improve:execute-reviewed` | Applies the items you approved, with backup and change log. Asks nothing. |

Claude also picks the skills up from plain requests such as "self improve", "improve the system" or "where did I correct you?".

Each finding lands in one of three buckets:

- **AUTO**: applied in the same run. Only a fixed list of small fixes: broken wiki links, missing INDEX or log lines, relative dates, typos, a "check this" flag, identical sync-conflict copies, backups older than 14 days, and `MEMORY.md` index lines. Never a skill or config file.
- **SIGN-OFF**: written to `review-YYYY-MM-DD.md` and applied only after your `[x]`. Skill edits, new skills, `CLAUDE.md`, hooks, settings, memory content, deletions. At most 10 new items per run.
- **CONTEXT**: a question in `needs-context-YYYY-MM-DD.md`. The next run reads your answer and picks the bucket.

## The guard

The plugin adds a `PreToolUse` hook on Edit, Write, MultiEdit, NotebookEdit and Bash. It does nothing unless a run is open. While a run is open, in every session, it blocks writes to your skill folders, `~/.claude/CLAUDE.md`, `settings.json`, `settings.local.json`, `hooks/`, `output-styles/`, `agents/`, `commands/`, any project's `.claude/` config, the wiki (if you set one) and anything in `extra_protected`. The only exceptions are the exact files the run released: the AUTO targets and the items you approved. It also blocks Bash commands that write there in ways it can't check, such as `python -c`, `sed -i` or a redirect.

A run ends with `finish` or `abort`. The lock never expires by itself. Claude may only abort a run that is at least 2 hours old or whose session is gone. A younger run can only be aborted by you, in your own terminal:

```
python3 ~/.claude/plugins/cache/self-improve/self-improve/0.1.0/skills/improve-system/scripts/improve_system.py abort <run_id>
```

(`status` prints the active run id.)

## Configuration

Everything works without configuration. To change something, create `~/.claude/self-improve/config.json` (a full example is in `config.example.json`). An environment variable `SELF_IMPROVE_<NAME>` overrides the file; list values in the environment are separated by `:`.

| Key | Default | What it does |
|---|---|---|
| `output_dir` | `~/.claude/self-improve/reviews` | Where `review-*.md`, `needs-context-*.md` and `change-log.md` go |
| `skill_roots` | (none) | Extra skill folders. `~/.claude/skills` is always included |
| `project_roots` | (none) | Folders whose `*/CLAUDE.md` and `*/*/CLAUDE.md` count as edit targets. The `CLAUDE.md` of every folder you worked in during the window is included anyway |
| `wiki_dir` | (off) | A markdown wiki to lint and audit (see below) |
| `raw_dir` | (off) | Source files the wiki is built from. Never edited; new files show up as gaps |
| `wiki_auto_dirs` | (none) | Wiki sub-folders with generated pages, skipped by the lint |
| `extra_protected` | (none) | More files or folders the guard protects during a run |
| `launch_command` | (off) | Opens the review and apply steps in a separate session (see below) |
| `notify_command` | (off) | Program called with one line of text at the end of a step |
| `wait_for_lock` | (off) | If this file exists, a run refuses to start (exit code 3), so it doesn't overlap another job |
| `ignore_message_regex` | (off) | Your messages that match are not read (for example text a hook types for you) |
| `max_signoff` | `10` | New SIGN-OFF items per run |

`SELF_IMPROVE_HOME` moves the whole state folder (default `~/.claude/self-improve`). `CLAUDE_CONFIG_DIR` is respected for where sessions, skills and config are read.

## Optional requirements

None of these are needed. Each one adds something, and the plugin falls back when it is missing.

- **A markdown wiki** (`wiki_dir`, Obsidian-style folder with `INDEX.md` and `log.md`). Adds link, index, relative-date and orphan checks plus a wiki analyst that looks for contradictions and stale facts. Without it, those checks are skipped. Without `INDEX.md`, the index check is skipped.
- **A way to open a separate session** (`launch_command`). With it, the end of a run opens a `REVIEW DD-MM` session for your answers, and your answers open an `EXEC DD-MM` session that applies them. `{name}` and `{prompt}` are replaced. A tmux example:

  ```json
  { "launch_command": "tmux new-session -d -s {name} claude --name {name} {prompt}" }
  ```

  Without it, the plugin prints the command to run, and execute-reviewed runs in the current session when you ask ("apply what I approved"). Before opening a session, the plugin checks `claude agents --json` and skips the launch if a session with the same prefix is already open.
- **Notifications** (`notify_command`): any program that sends you a message, called with one line of text. Without it, nothing is sent. If your session has a `PushNotification` tool, the skills may use it too.
- **Scheduling.** The plugin has no scheduler. To run it weekly, start `claude "/self-improve:improve-system"` from cron, launchd or similar.

## How it works

1. `start` reads `~/.claude/projects/*/*.jsonl` since the last finished run. It keeps your own messages, interrupts (Esc) and tool rejections, marks messages that look like corrections (English and Portuguese patterns), groups near-identical messages across sessions and counts friction per skill.
2. It also lists what changed (skills, config, memory, wiki), runs a lint, and splits the sessions into chunks.
3. Claude sends one subagent per chunk to find corrections, repeats, repeated tasks, friction and lost context. Their findings are merged, each target is opened and read, and the plan goes through `render`, which refuses anything outside the bucket rules.
4. Changes are applied with `backup`, `intent` (records the file's hash so a crash never applies twice) and `log`. An approved item with a `patch` is applied by `apply-patch`, which swaps the exact text you saw and refuses if that text is no longer in the file exactly once.
5. `finish` closes the run, carries `[ ]` items and open questions into the new review files, and releases the guard.

## Uninstall

```
claude plugin uninstall self-improve@self-improve
claude plugin marketplace remove self-improve
```

Your reviews, change log, backups and run history stay in `~/.claude/self-improve`. Delete that folder if you don't want them. If you uninstall during a run, the lock file stays there too, but with the plugin gone there is no hook to enforce it.

## Limits

- Tested on macOS with Claude Code 2.1.319 and Python 3.9 and 3.15: install, the test suite, and the scripts on real session files. The three skills were not run end to end inside a live session for this release. Not tested on Linux or Windows. The guard uses `ps` to find the session that owns a run; where that fails, only the 2-hour rule applies.
- The guard was tested by feeding it hook JSON on stdin (see `tests/test_guard.py`), not inside a live session.
- The correction patterns are a hint for the subagents, not the decision. Only English and Portuguese are covered.
- The first run with a lot of history can take a minute: it reads 30 days of sessions to count skill use.

Run the tests with `pytest tests`.

MIT License. Copyright (c) 2026 Lucas Saldanha Werneck.

Built with Claude Code
