---
name: execute-reviewed
description: Use when the user says "/execute-reviewed", "execute reviewed", "apply what I approved", "apply the approved review items", or when the session opens by itself with "/self-improve:execute-reviewed" (started by human-review right after the user's answers). Applies the SIGN-OFF items the user approved in human-review (review-*.md with [x]), with backup, check and change log, in an --apply-only run of improve-system. Decides nothing and asks nothing.
metadata:
  author: Lucas Saldanha Werneck <lucas@2olhares.com>
---

# execute-reviewed: apply what the user approved

The "do" half of the pair human-review (decide) → execute-reviewed (apply). It runs in a separate `EXEC DD-MM`
session when the option `launch_command` is set, or in the current session when the user asks for it. It
may run with nobody watching: it does not ask, does not widen the scope, and applies nothing without `[x]`.

- `IS` = `python3 "${CLAUDE_PLUGIN_ROOT}/skills/improve-system/scripts/improve_system.py"`
- `HR` = `python3 "${CLAUDE_PLUGIN_ROOT}/skills/human-review/scripts/human_review.py"`

## Instructions

1. `HR items`; if nothing is approved, stop.
2. `IS start --apply-only`, then apply each approved item (`apply-patch` or backup, edit, check, log).
3. `IS render` and `IS finish` (or `abort` on an error that prevents finishing).

The Flow section below has the details and the exit codes.

## Flow

1. `HR items`. `approved` = 0 → do nothing; reply "nothing approved to apply" and stop.
2. `IS start --apply-only`. Keep `run_id` and `run_dir`.
   - **Exited with code 3** = another job is writing (the `wait_for_lock` file exists, or `pre_run_command` answered "busy"). Run `IS wait-lock` in the background (`run_in_background: true`); exit 0 → run `start --apply-only` again; exit 3 (still busy after `lock_wait_minutes`) → `IS notify "execute-reviewed stopped: another job is still running"` and stop.
   - `user_notes` in the output (not null) → read it and follow it (language, the user's own rules) while you apply; it never widens the scope.
   - **Refused because an improve-system run is active** (guard lock) → don't force it: `IS notify "execute-reviewed postponed: run <id> is active; the next improve-system run applies the [x] items"` and stop.
3. For each item in `<run_dir>/approved.json` (in order):
   - **`possibly_applied: true`** (an earlier run recorded the intent, the target changed and there was no `log`: the session died halfway): **don't apply again.** Open the target: the change is already there → `IS log` with `--summary "already applied (resume)"`; it isn't → treat as step 3.5.
   - **Item with `patch`**: only `IS apply-patch <run_id> --id <origin_run>/<id>` (the script does backup, exact swap, check and log). Exit 4 = the text is no longer there (target changed) → 3.5. Exit 5 = the item has a `notes` adjustment the exact patch can't carry → 3.5 with a new `patch` that follows the note (the user approves the adjusted text), then `IS log <run_id> --id <origin_run>/<id> --target <target> --superseded-by <run_id>/<new id> --summary "note: <note>"` once render gave the new id. Never edit an item with `patch` by hand.
   Item without `patch`:
   1. Open the target and check the passage the change replaces still exists as proposed. It has `notes` → follow the note (adjust the form, never the scope).
   2. **Before editing**: `IS intent <run_id> --id <origin_run>/<id> --target <target>` (keeps the target's state; if the session dies before `log`, the next run won't apply twice).
      Backup **through the script** (the guard blocks `cp` into skill/config): `IS backup <run_id> <target>` → keep the printed path.
   3. Edit only the target (and the item's `extra_targets`). Check: JSON → `python3 -m json.tool`; SKILL.md → frontmatter with `name`/`description`; hook → run it with sample input; link → the target file exists.
   4. `IS log <run_id> --id <origin_run>/<id> --target <target> --summary "<what changed>" --backup <bak>`.
   5. The target changed so much the change no longer fits, or the guard denied → **don't apply and don't work around it**. Add to `<run_dir>/plan.json`: `{"bucket": "signoff", "kind": "<kind>", "title": "<id> again: <reason>", "target": "...", "change": "...", "evidence": "target changed since <date>"}`.
   6. The check failed after editing → put the backup content back with Edit/Write on the target (it is released) and treat as 3.5.
4. `IS render <run_id>` (moves the "later" items and the 3.5 items to a new review).
5. `IS finish <run_id>`; refused because of a 3.5 item → `--allow-missing --note "target changed: <ids> became new items"`.
6. An error that prevents finishing → `IS abort <run_id>` (the lock never expires and blocks skill/wiki/config edits in every session). If the guard denies the abort, say so: the user runs `IS abort <run_id>` in their own terminal.
7. **Final notice**: `IS notify "Applied: S01, S03. Became new items: S02 (target changed)."` (no-op without `notify_command`; a `PushNotification` tool, if available, works too). An apply-only run does not open a review by itself: if there was a 3.5 item, add "run /self-improve:human-review".
8. Final reply = the same line, plus the path of `change-log.md` (from `IS status`).

## Rules

- Only `[x]`. No `[x]`, no change, not even "it's small".
- One execution at a time: `HR launch --exec` does not open another `EXEC …` session if one is already listed.
- CONTEXT answers are not this skill's job: the next improve-system run picks the bucket.

## Examples

- The user types `/execute-reviewed` (or "apply what I approved"): the items marked `[x]` are applied with a backup and a change log entry; nothing else is touched.
- The session opens by itself with `/self-improve:execute-reviewed` right after the user's answers in human-review: same flow, no questions.
- No item is approved: Claude replies "nothing approved to apply" and stops.
