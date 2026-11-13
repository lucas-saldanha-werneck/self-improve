---
name: human-review
description: Use when the user says "/human-review", "human review", "review the improve-system items", "what is waiting for me to decide?", "I want to approve the review items", or when the session opens with "/self-improve:human-review answer" (opened by the end of an improve-system run). Shows each SIGN-OFF and CONTEXT item as buttons (AskUserQuestion) and records the choices in the review-*.md / needs-context-*.md files. It does NOT apply anything; at the end it hands the approved items to execute-reviewed.
argument-hint: "[answer|open]"
metadata:
  author: Lucas Saldanha Werneck <lucas@2olhares.com>
---

# human-review: decide the improve-system items with buttons

`improve-system` puts what needs the user in `review-*.md` (SIGN-OFF) and `needs-context-*.md` (CONTEXT). The
user may be on a phone and not want to open files. This skill turns each item into a card with buttons and
**only records the decision**. At the end, if something was approved, it hands over to `execute-reviewed`.

Why separate: a session driven from a phone can drop. Applying skill/config changes in the middle of it could
leave the guard lock stuck or apply something twice. A decision recorded on the click is never lost; applying
runs as its own step.

Script: `python3 "${CLAUDE_PLUGIN_ROOT}/skills/human-review/scripts/human_review.py"` (written `HR` below): `items`, `mark`, `launch`.

## Instructions

1. `HR items` lists what is waiting (SIGN-OFF and CONTEXT).
2. Show each item as a short card with buttons (`AskUserQuestion`, up to 4 per call).
3. Record each choice with `HR mark`; apply nothing.
4. If something was approved, hand over to `execute-reviewed` with `HR launch`.

The sections below give the modes and the card rules.

## Modes

- `open` (and the improve-system `finish`, by itself): only `HR launch`. With the option `launch_command`
  configured, it opens a separate session named `REVIEW DD-MM` with `/self-improve:human-review answer`.
  Without it, it prints how to start the review. Pass on the line it printed.
- `answer` (or no argument): the flow below, in this session.

## `answer` flow

1. `HR items`. `open` empty → if `approved` > 0 (approved but not applied yet), go to step 5; otherwise say "nothing waiting for you" and stop.
2. If a `PushNotification` tool is available, send "<N> decisions waiting for you". Optional.
3. **Cards**, up to 4 per `AskUserQuestion`, in the order of `items`:
   - SIGN-OFF: `header` = id (`S03`). `question` = a **short plain-language explanation** (rule below), written by you from the item; never the `change` or `patch` pasted in.
     **Item with `patch`**: the EXACT swap (`replace «<old>» with «<new>» in <file>`) goes in the `preview` of the Apply option, not in the `question`, and also in the Apply option's description in short form, because some clients don't show `preview`. That text, and only that, is what execute-reviewed applies. Options, with the `recommend` one first and " (Recommended)" in its label:
     - `Apply`: description = the why in 1 simple line.
     - `Reject`: description = "won't be proposed again without new evidence".
     - `Later`: description = "comes back in the next review".
     **"Other" (free text) does NOT approve**: record `later` + the note. Free text is a doubt or an adjustment, not consent. If the note clearly says "apply with X", ask the same card again with the note in the Apply description.
   - CONTEXT: `header` = id (`C02`). `question` = the item's `question`, rewritten by the rule below if it is long or full of jargon. Options = up to 4 of the item's `options` (no `options`: `Yes` / `No`). "Other" = free answer.
4. **Record right after each call. The only action after an `AskUserQuestion` is `mark`** (the session may drop):
   - SIGN-OFF: `HR mark --file <file> --ref <ref> --choice apply|reject|later [--note "<text>"]`.
   - CONTEXT: `HR mark --file <file> --ref <ref> --answer "<option or text>"`.
   - `mark` refused with "was already read": a new improve-system run went over it. Run `items` again and continue with the items it returns.
5. **Hand over and reply.** If at least 1 Apply (or `items` still shows `approved` > 0): run `HR launch --exec`.
   - It printed `exec: opened …` → a separate session applies the items now. Reply (1-3 lines): "Recorded: N apply, N reject, N later, N answers. Applying now in the EXEC session."
   - It printed `No launch_command configured` → reply with the counts and "Say *apply what I approved* to apply them here, or run `/self-improve:execute-reviewed` later." If the user then asks, run the execute-reviewed skill in this same session.
   No Apply: "Recorded; nothing to apply." This skill never applies anything by itself.

## How to write the card (plain, short)

The user reads it on a small screen, maybe tired. The card explains the decision; it does not show the raw change.

- **At most 3 short sentences, about 45 words.** One idea per sentence. Simple words, active voice.
- **Sentence 1 = what changes for the user, in practice.** "New rule: …", "Fix a number in …", "Delete …". Never start with a file name or with "replace «…»".
- **Sentence 2 = why**, with the fact behind the proposal (the user's correction, the wrong number, the date).
- **No jargon or code in the `question`:** no tool names, paths, `**`, «» quotes, pasted text. Say where in words ("in the global rules", "in the team page of the wiki").
- An unavoidable technical term → explain it right after in 3-5 words.
- The exact text goes in the Apply `preview`; the `question` never depends on it to make sense.

Example:

- Bad: "CLAUDE.md: replace «- Before you ask me …» with «(same line) + - Session cleanup: archive, rename … mcp__…»"
  Good: "New rule: when an app session needs to be archived or renamed, I do it myself instead of asking you. You corrected me on this twice last week."

## Notes

- `launch_command` is any program that opens a Claude Code session; `{name}` and `{prompt}` in it are replaced (example in the README). It should open the session without stealing focus.
- `launch` does not open a second `REVIEW …` (or `EXEC …`) session if `claude agents --json` lists one already.
- CONTEXT answers don't become changes here nor in execute-reviewed: the next improve-system run picks the bucket.

## Examples

- The user types `/human-review` (or "what is waiting for me to decide?"): each waiting item appears as a card with Apply, Reject or Later; choices are written to the review files.
- The session opens by itself with `/self-improve:human-review answer` after an improve-system run: the same flow, in that session.
- Nothing is waiting: Claude says "nothing waiting for you" and stops.
