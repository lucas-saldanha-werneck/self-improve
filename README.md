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

The plugin adds a `PreToolUse` hook on Edit, Write, MultiEdit, NotebookEdit and Bash. It does nothing unless a run is open. While a run is open, in every session, it blocks writes to your skill folders, `~/.claude/CLAUDE.md`, `settings.json`, `settings.local.json`, `hooks/`, `output-styles/`, `agents/`, `commands/`, any project's `.claude/` config, the wiki (if you set one) and anything in `extra_protected`. The only exceptions are the exact files the run released: the AUTO targets and the items you approved. It also blocks Bash commands that write there in ways it can't check, such as `sed -i` or a redirect. During a run it blocks all code typed into a command, in any folder: `python3 -c`, `node -e`, `bash -c`, and a heredoc or pipe into an interpreter. Such code can build a path at run time, so the guard can't see where it writes. Set `allow_inline_code` to `1` to allow it again; then only code whose text names a protected path is blocked.

What the guard is and is not: it reads each tool call before it runs and stops the writes it can recognize. It is a guardrail against mistakes, not a sandbox. A script file run with `python3 script.py`, or a program it doesn't know, can still write anywhere your user account can. For a hard boundary, run Claude Code in a container or as a user that can't write to `~/.claude`.

A run ends with `finish` or `abort`. The lock never expires by itself. Claude may only abort a run that is at least 2 hours old or whose session is gone. A younger run can only be aborted by you, in your own terminal:

```
python3 ~/.claude/plugins/cache/self-improve/self-improve/0.2.0/skills/improve-system/scripts/improve_system.py abort <run_id>
```

(`status` prints the active run id.)

## Configuration

Everything works without configuration. To change something, edit `~/.config/self-improve/config.json` (see *Your settings* below). An environment variable `SELF_IMPROVE_<NAME>` overrides the file; list values in the environment are separated by `:`.

**Your settings survive plugin updates.** The config file lives in `~/.config/self-improve/`, and the run state and the reviews in `~/.claude/self-improve/`, both outside the plugin folder that an update replaces. Personal behavior goes in small scripts you point the `*_command` options at, not in the plugin's code.

### Your settings

Your file is `~/.config/self-improve/config.json` (`$XDG_CONFIG_HOME/self-improve/config.json` when `XDG_CONFIG_HOME` is set; `$SELF_IMPROVE_HOME/config.json` if you set `SELF_IMPROVE_HOME`; on Windows `~` is `%USERPROFILE%`). Older versions kept it in `~/.claude/self-improve/config.json` (`$CLAUDE_CONFIG_DIR` replaces `~/.claude`): while the new file does not exist, that old file is still read and nothing new is created next to it; once the new file exists, it wins. Move the old file to the new place whenever you like. You don't have to create it: the plugin writes it the first time a session starts (or the first time `improve_system.py` runs), from [`config.example.json`](config.example.json), with every option set to `null` and a one-line note per option under `_doc`. `null` means "use the built-in default" (the table below), so that file behaves exactly like having no file, and a later version that changes a default reaches you too. Replace a `null` to change that setting. Plugin updates never touch it, and an existing file is never overwritten, so edit it freely.

| Key | Default | What it does |
|---|---|---|
| `output_dir` | `~/.claude/self-improve/reviews` | Where `review-*.md`, `needs-context-*.md` and `change-log.md` go |
| `skill_roots` | (none) | Extra skill folders. `~/.claude/skills` is always included |
| `project_roots` | (none) | Folders whose `*/CLAUDE.md` and `*/*/CLAUDE.md` count as edit targets. The `CLAUDE.md` of every folder you worked in during the window is included anyway |
| `wiki_dir` | (off) | A markdown wiki to lint and audit (see below) |
| `raw_dir` | (off) | Source files the wiki is built from. Never edited; new files show up as gaps |
| `wiki_auto_dirs` | (none) | Wiki sub-folders with generated pages, skipped by the lint |
| `extra_protected` | (none) | More files or folders the guard protects during a run |
| `allow_inline_code` | `0` | `1` lets `python3 -c`, `node -e` and heredocs into interpreters run during a run, unless their text names a protected path |
| `launch_command` | (off) | Opens the review and apply steps in a separate session (see below) |
| `notify_command` | (off) | Program called with one line of text at the end of a step |
| `wait_for_lock` | (off) | If this file exists, a run refuses to start (exit code 3), so it doesn't overlap another job. `wait-lock` waits for it |
| `lock_wait_minutes` | `180` | How long `wait-lock` waits before it gives up (exit 3) |
| `pre_run_command` | (off) | Runs at the start of every run, before anything is read (see below). Its output goes into `changes.md`; exit 3 = "busy, wait" |
| `extra_lint_command` | (off) | Runs during the lint; each line it prints is one more finding (see below) |
| `language` | (off) | Language for item titles, questions, evidence and the chat summary (for example `Portuguese`). Keys, kinds and buckets stay in English |
| `analyst_notes` | (off) | Your own rules for the analysts and the plan, as text or the path of a file. Written to `user-notes.md` in the run folder; the skills read it and follow it, but it never overrides the guard or the bucket limits |
| `claude_command` | `claude` on `PATH` | The `claude` program asked for open sessions (`claude agents --json`) before a launch, for example a pinned copy |
| `ignore_message_regex` | (off) | Your messages that match are not read (for example text a hook types for you) |
| `max_signoff` | `10` | New SIGN-OFF items per run |

`SELF_IMPROVE_HOME` moves the whole state folder (default `~/.claude/self-improve`), and the config file with it. `CLAUDE_CONFIG_DIR` is respected for where sessions, skills and Claude Code's own config are read.

### Commands you plug in

Both are split like a shell line (no shell runs them; put pipes in a script), get `SELF_IMPROVE_EVENT`, and never stop a run by failing:

- `pre_run_command` gets `SELF_IMPROVE_MODE` (`full`, `apply-only`, or `check` when `wait-lock` asks), `SELF_IMPROVE_SINCE` and `SELF_IMPROVE_UNTIL` (the run's window, ISO UTC), `SELF_IMPROVE_WIKI_DIR`, `SELF_IMPROVE_RAW_DIR`. Exit 0: what it prints is added to `changes.md` (for example "the data jobs that ran in this window"). Exit 3: another job is busy, so `start` exits 3 and the skill waits with `wait-lock`. Any other exit: the run goes on and the chat output mentions it. Keep it quick and safe to run several times.
- `extra_lint_command` gets `SELF_IMPROVE_WIKI_DIR` and `SELF_IMPROVE_RAW_DIR`. Print one finding per line, either JSON `{"kind": "...", "target": "/abs/path", "detail": "...", "auto_ok": false}` or `kind<TAB>target<TAB>detail`. Findings are hints the run checks before proposing; `auto_ok` defaults to false, and AUTO stays limited to the kinds in `references/buckets.md`. A failing command becomes one `extra-lint-failed` finding.

`improve_system.py wait-lock [--max-minutes N] [--every S]` blocks until `start` may run: the `wait_for_lock` file is gone and `pre_run_command` (in `check` mode) no longer answers 3. Exit 0 = go, 3 = gave up.

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
4. Changes are applied with `backup`, `intent` (records the file's hash so a crash never applies twice) and `log`. An approved item with a `patch` is applied by `apply-patch`, which swaps the exact text you saw and refuses if that text is no longer in the file exactly once. It also refuses, and leaves the file as it was, if the result would be invalid JSON (for a `.json` file) or a `SKILL.md` without its `name` and `description` frontmatter. Your `[x]` counts for the item as the review showed it: if the plan behind it changes after the review was written, the item is not applied and comes back as pending with the new text. After `start`, the run works from its own copy of what you approved.
5. `finish` closes the run, carries `[ ]` items (with their `Note:` lines) and open questions into the new review files, and releases the guard. A review file you marked or answered after `start` read it is left open, so the next run reads your new decision.

## Uninstall

```
claude plugin uninstall self-improve@self-improve
claude plugin marketplace remove self-improve
```

Your reviews, change log, backups and run history stay in `~/.claude/self-improve`, and your settings in `~/.config/self-improve`. Delete those folders if you don't want them. If you uninstall during a run, the lock file stays there too, but with the plugin gone there is no hook to enforce it.

## Limits

- Tested on macOS with Claude Code 2.1.319 and Python 3.9 and 3.15: install, the test suite, and the scripts on real session files. The three skills were not run end to end inside a live session for this release.
- The test suite also passes on Linux and Windows in GitHub Actions (Python 3.12). The plugin was not run inside Claude Code on Linux or Windows.
- The hook runs `python3`. On Windows, check that this command starts Python 3. Some installs only add `python` and `py`.
- The guard uses `ps` to find the session that owns a run. Where that fails, and always on Windows, only the 2-hour rule applies.
- The guard was tested by feeding it hook JSON on stdin (see `tests/test_guard.py`), not inside a live session.
- The correction patterns are a hint for the subagents, not the decision. Only English and Portuguese are covered.
- The first run with a lot of history can take a minute: it reads 30 days of sessions to count skill use.

Run the tests with `pytest tests`.

## Credits

The idea, the skill name and the three buckets (auto-approve, sign-off, needs context) come from Austin Marchese's video [How to Build A Self-Improving System with Claude](https://www.youtube.com/watch?v=2fc0NX9vIJ8). This plugin is my own build of that idea for Claude Code.

MIT License. Copyright (c) 2026 Lucas Saldanha Werneck.

Built with Claude Code
