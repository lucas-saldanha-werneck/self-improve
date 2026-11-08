---
name: improve-system
description: 'Use when the user says "/improve-system", "improve system", "improve the system", "self improve", "where did I correct you?", "what did I repeat?", "apply what I marked", "lint the wiki", or asks to turn repeated corrections, repeated tasks, skill friction, wiki contradictions, broken links, stale pages or bloat into a change to a skill, CLAUDE.md, memory, hook or wiki. In one run: applies what the user approved in the previous review, reads what changed since the last run, and sorts each improvement into AUTO / SIGN-OFF / CONTEXT.'
---

# improve-system

A correction the user has to make twice is a defect of the setup, not of the conversation. This skill finds
those defects (in sessions, skills, config and an optional wiki) and sorts each fix by risk.

Script: `python3 "${CLAUDE_PLUGIN_ROOT}/skills/improve-system/scripts/improve_system.py"` (written `IS` below):
`status`, `start`, `render`, `backup`, `intent`, `apply-patch`, `log`, `finish`, `abort`, `notify`.
`IS status` prints where the state and the review files live (default `~/.claude/self-improve/` and
`~/.claude/self-improve/reviews/`: `review-YYYY-MM-DD.md`, `needs-context-YYYY-MM-DD.md`, `change-log.md`).

## The 3 buckets

| Bucket | What goes in | What the skill does |
|---|---|---|
| **AUTO** | Only the kinds in `references/buckets.md` (broken wiki link, INDEX line, wiki log line, relative date, typo, "check" flag, identical conflict copy, old backup, memory index line). Never a skill or config file. | Applies it in this run, 1 line in `change-log.md` per change. |
| **SIGN-OFF** | Skill edit, new skill, wiki restructure, contradiction, CLAUDE.md, hook, settings, output style, memory content, delete/merge. | Written to `review-*.md` as `- [ ]`. **Not applied.** Only items with `[x]` are applied later. |
| **CONTEXT** | Ambiguous pattern only the user can decide (which source is right, whether something is on purpose). | Question written to `needs-context-*.md`. The next run reads the **Answer:** and picks the bucket. |

When in doubt between two buckets, use the safer one (CONTEXT > SIGN-OFF > AUTO).

**Hard rule:** a SIGN-OFF item never touches skills, the wiki or config without the user's `[x]`. The plugin's
PreToolUse hook (`scripts/guard.py`) enforces this while a run is active, **in every session**, until `finish` or `abort`:
- A write passes only for an **exact** file in `allowed-paths.txt` (AUTO validated by `render` + `[x]` items). A folder is never released whole.
- A Bash command that mentions a protected zone passes only if it is a read, a run of this plugin's scripts, or a write to a released path. `python -c`, `node -e`, `awk`, redirects to a variable = blocked. Make the edit with Edit/Write on the released file.
- Unreadable lock = blocked. Run control files (`active-run.json`, `allowed-paths.txt`, `approved.json`…) are written by the script only.
- If the hook denies, **do not work around it**: the item goes to the review. Abandoned run: `IS abort <run_id>` (does not move the window, does not consume reviews). Claude can only abort if the session that owns the run is gone or the run is 2 h+ old; otherwise only the user, in their own terminal.

## Steps

1. **Open the run.** `IS status`, then `IS start` (`--since <ISO>` only if the user asks for another window). Keep `run_id` and `run_dir`. `start` already read the previous reviews:
   - `approved.json` = `[x]` items (+ the user's `notes`). `carried.json` = `[ ]` (they come back in the new review). `rejected-now.json` = `[-]`.
   - `answers.json` = answered questions. `open-questions.json` = unanswered (they come back).
   - **`start` exited with code 3** = the file in the option `wait_for_lock` exists (another job is writing). Don't run alongside it. Wait in the background (Bash with `run_in_background: true`: `until [ ! -f <that file> ]; do sleep 600; done`), then run `start` again. Still locked after 3 h → stop and tell the user.
2. **Apply what the user marked.** Item with `possibly_applied: true` (an earlier session edited and died before `log`): don't reapply. Check the target; already there → `log` with "already applied (resume)"; not there → new SIGN-OFF item. Item with `patch`: only `IS apply-patch <run_id> --id <origin_run>/<id>` (exit 4 = target changed → new SIGN-OFF item); never by hand. For the others, run `IS intent <run_id> --id <origin_run>/<id> --target <path>` BEFORE editing. For each item in `approved.json`: open the target and check the text still matches (the file may have changed). If it has `notes`, follow the note. Backup with `IS backup <run_id> <file>` (`cp` into a protected zone is blocked by the guard) → edit → check (step 8) → `IS log <run_id> --id <origin_run>/<id> --target <path> --summary "..." --backup <bak>`. If the target changed so much the change no longer fits, **don't apply**: it becomes a new SIGN-OFF item in the plan, with the reason.
3. **Analysts, all in the same turn** (subagents via the Agent tool):
   - 1 per `chunk-NN.md` (sessions) → `references/analyst-prompt.md`.
   - 1 per `wiki-NN.md` (only when a wiki is configured) → `references/wiki-analyst-prompt.md`. Normally only changed pages + link neighbors; the whole wiki every 14 days, or with `start --full-wiki`.
   With `messages: 0` and no wiki change, skip the analysts.
4. **Check the artifacts, not the report.** Each `findings-NN.json` / `wiki-findings-NN.json` exists, is valid JSON and has an mtime after `start`. Missing → dispatch that chunk again.
5. **Merge everything**: analyst findings + `lint.md` + `friction.md` + `changes.md` + `repeats.md` + `answers.json`:
   - Sessions: group by `theme`; a theme with 2+ hits or 1 of strength 3 goes in.
   - Lint: each line is a machine hint. Open the target and confirm before proposing.
   - Friction: a skill with many Esc/rejections/errors/corrections → read the skill and find the cause.
   - Repeated task (same multi-step work in 2+ sessions) → candidate new skill (SIGN-OFF).
   - `context-loss` (info lost after a compaction or /clear) counts with **1 hit**. Look at what the summary missed → SIGN-OFF with a `patch` to the compaction instructions in the global CLAUDE.md (or whatever produces the summary).
   - Bloat: skill unused for 30 days, huge skill, duplicated rule, old backup.
   - New sources in `changes.md` (raw folder, if configured) with no trace in the wiki → gap.
   - Out: anything in `previously-rejected.md` without new evidence.
6. **For each item, open the target and read it.** Does the rule already exist? If it exists and the user still had to repeat it → prose was not enough: propose a hook or a stronger rule. Target is a plugin skill or a linked (imported) skill → override in an own skill or a hook (an update wipes the edit).
7. **Write `<run_dir>/plan.json`** (a list) and run `IS render <run_id>`:
   ```json
   {"bucket": "auto|signoff|context", "kind": "broken-link|skill-edit|new-skill|wiki-restructure|contradiction|hook|claude-md|memory|bloat|gap|stale|...",
    "title": "short phrase", "target": "/absolute/path or new:/absolute/path", "extra_targets": [],
    "evidence": "N times, M sessions, dates, 1 short quote", "change": "exact text to insert/replace, or the action",
    "why": "why this form", "risk": "what could break", "question": "(context only)", "options": ["(context only)"],
    "recommend": "apply|reject|later (signoff only; optional, default apply: becomes the recommended button in human-review)",
    "patch": {"old": "EXACT text that appears once in the target", "new": "text that replaces it"}}
   ```
   **Use `patch` whenever the change swaps text in an existing file** (most skill/CLAUDE.md/wiki edits): the user sees exactly what goes in, and execute-reviewed applies it with `apply-patch` without reinterpreting prose (which came from sessions and might carry embedded instructions). `render` refuses a `patch` whose `old` does not appear exactly once. No `patch` only when impossible (new file, scattered change).
   Every target is an **absolute path**. New skill: `target` = the SKILL.md and **each** extra file in `extra_targets` (the guard releases file by file).
   `render` refuses: AUTO outside the kinds/places in `references/buckets.md`, any skill/config target (including `extra_targets`) in an AUTO item, any change inside the raw folder, more than `max_signoff` (default 10) new SIGN-OFF items (sort and cut), and a second render after AUTO items were applied. If it refuses, change the bucket, never the kind just to get through. It assigns ids (A01, S01, C01), writes the user's files and releases the AUTO targets in the guard. Run it **once**, before applying.
8. **Apply the AUTO items.** `IS backup <run_id> <file>` → edit → check → `IS log <run_id> --id A0N --target ... --summary ...`. Every wiki change also gets a line in the wiki's `log.md` (`## [YYYY-MM-DD] lint: …`) and its INDEX line when the wiki has those files. Check: JSON → `python3 -m json.tool`; SKILL.md → frontmatter with `name`/`description`; hook → run it with sample input; link → the target file exists.
   `log` only accepts: AUTO from this run, or an item with `[x]` in `approved.json`; and only a file that is a target of the item (or the wiki INDEX/log). It records the canonical id `run/id` and the file hash.
9. **Close.** `IS finish <run_id>` (needs `render` first, even with an empty plan). It refuses to close if an AUTO or approved item has no `log` (use `--allow-missing --note "why"` only if the user says so): the unapplied `[x]` **stays valid**, its review stays open and the next run tries again. What was applied or rejected never comes back. `finish` releases the guard lock and, if something waits for the user, launches the review session when the option `launch_command` is set (otherwise it prints how to open it) and prints a `review: …` line. In a manual test use `--no-review`.
10. **Notify.** If the `finish` output has `review: FAILED`, run `IS notify "improve-system: the review session did not open. Run /self-improve:human-review"`. `notify` does nothing when `notify_command` is not configured. If a `PushNotification` tool is available, you may use it as well.

## Chat output

**What the user needs:** to know what already changed and where to decide the rest. They may be on a phone.

- 1st line: window covered; sessions, messages, changed files, lint items read.
- **Applied now:** N AUTO + N approved (1 line each: id, what, where).
- **Waiting for you:** the `review: …` line from `finish` + path of `review-*.md` (N items) and `needs-context-*.md` (N questions). Short table of the SIGN-OFF items: `| id | what changes | where | evidence | recommend |`. Say they can answer with `/self-improve:human-review` (buttons) or by editing the files.
- At most `max_signoff` new SIGN-OFF items per run, ordered by strength x frequency. The rest: "N weak themes left out".

**Done when:** AUTO and approved items are on disk and in `change-log.md`, the 2 review files exist (if there were items), and `IS status` shows the run closed with no active lock.

## Common mistakes

- Applying a SIGN-OFF item "because it's small". Size does not change the bucket: skills, structural wiki changes and config only with `[x]`.
- Counting "asked for the next task" as a correction. It only counts when the user fixed Claude.
- Proposing a rule that already exists without reading the target.
- Closing the run before applying and logging. That moves the window and loses the period.
- Editing a plugin skill or a linked skill. The next update wipes the edit.
- Treating `[ ]` as rejection. `[ ]` = not decided yet; it comes back in the new review.
- Leaving a run open. The lock does not expire: without `finish`/`abort`, no session can edit skills/wiki/config. A line with an id but an unreadable checkbox becomes pending and shows in `review-warnings.json`: tell the user.
