# Analyst prompt (1 subagent per chunk)

Replace `<RUN_DIR>` and `<NN>`. Dispatch them all in the same turn.

---

You read an excerpt of the user's conversations with Claude Code and find where they had to correct Claude or
repeat something. You do NOT edit any file other than your report.

Read:
1. `<RUN_DIR>/chunk-<NN>.md`: the user's messages, in order, per session. `CLAUDE BEFORE` = end of Claude's last reply. `⚑` = flagged by a regex (a hint only; check the text). INTERRUPT = the user pressed Esc. REJECTION = the user refused a tool.
2. `<RUN_DIR>/repeats.md`: near-identical messages across sessions.
3. `<RUN_DIR>/inventory.md` and `<RUN_DIR>/friction.md`: skill/config files that exist and who owns them.
4. `<RUN_DIR>/previously-rejected.md`: what the user already refused.

Counts as a finding:
- **correction**: the user says Claude got it wrong, did what was not asked, forgot something, claimed without checking, asked too much, talked too much, used the wrong tool, or had to say again something already said.
- **repetition**: the same request/instruction shows up 2+ times (in this window, or it is already written in a skill/CLAUDE.md and the user still had to ask).
- **interrupt/rejection**: only if what comes right after shows the reason.
- **repeated-task**: the same multi-step work (not the same sentence) done in 2+ sessions without a skill: candidate new skill.
- **friction**: the active skill got stuck, used the wrong tool, asked for a manual step or went in circles. See also `<RUN_DIR>/friction.md`.
- **gap**: the user asked something the wiki or memory should answer and Claude had to research again.
- **context-loss**: right after a compaction or a /clear that resumed from a summary, the user had to repeat something already said, or Claude redid/rediscovered a finding, a decision or a file from the earlier part. Use `theme: "lost-after-compact"` and say in `claude_did` WHICH kind of information was lost (finding, decision, plan, file, next step, other). `target_guess`: the global CLAUDE.md (compaction instructions) or the skill that writes the summary.

Does NOT count: giving new information, answering Claude's question, picking an option, changing topic, asking for the next task, text pasted from third parties.

Write `<RUN_DIR>/findings-<NN>.json` (a JSON list; an empty list is valid):

```json
[{
  "kind": "correction | repetition | interrupt | rejection | repeated-task | friction | gap | context-loss",
  "theme": "short stable kebab-case (e.g. claims-without-checking, asks-too-much, manual-resume)",
  "quote": "the user's literal words, up to 200 characters",
  "session": "first 8 characters",
  "ts": "timestamp from the chunk",
  "claude_did": "what Claude did, 1 sentence",
  "user_wanted": "what the user wanted, 1 sentence",
  "skill": "active skill or -",
  "target_guess": "path from inventory.md that should have prevented this, or new",
  "strength": 1
}]
```

`strength`: 3 = explicit rule ("always", "never", "I already told you") or clear irritation; 2 = clear correction; 1 = probable.
**Explicit tag:** if the user wrote "tag for improve", "mark for improve" or similar, that case is a finding with `strength: 3`, even without any other hint. In `claude_did`, describe the mistake they tagged (the earlier messages), not the tag sentence.

After writing, read the file again and check it is valid JSON. Reply only: file path + number of findings.
