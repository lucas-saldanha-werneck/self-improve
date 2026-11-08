# Buckets: what goes where

The script's `render` enforces the AUTO part (the `AUTO_KINDS` list in `scripts/improve_system.py`). The rest is judgment.

## AUTO-APPROVE (applied in the run, logged in change-log.md)

Only these kinds, each only in its own place (`render` checks `target` AND `extra_targets`), nothing in the
raw folder, and never with a target that is a skill or config file (your skill folders, the global `CLAUDE.md`,
`settings.json`, hooks, output styles, agents, commands, any project's `.claude/` config).

| kind | When | Limit |
|---|---|---|
| broken-link | a wiki `[[x]]` or `(x.md)` points to a page that does not exist, and the right page is obvious (renamed, typo) | No obvious target → CONTEXT |
| index-entry | a page exists but is missing from the wiki `INDEX.md`, or INDEX points to a deleted page | 1-line summary taken from the page itself |
| log-entry | a wiki change without a line in the wiki `log.md` | Records only; does not change a page |
| relative-date | "yesterday", "last week" → absolute date | Only if the date is in the source or the log |
| typo | spelling/formatting error | Without changing meaning, a number or a name |
| stale-flag | dated fact contradicted by a newer source | Only adds `(check: reason, YYYY-MM-DD)`; rewriting = SIGN-OFF |
| conflicted-copy | a sync "conflicted copy" identical to the original (`cmp` = 0) | Move to the trash folder (`<state>/trash`), never `rm`. Different → CONTEXT |
| backup-cleanup | `*.bak-*` older than 14 days | Move to `<state>/backups/old/`, never `rm` |
| memory-index | a `MEMORY.md` line for a missing file, or a memory file with no line | Index only; memory content = SIGN-OFF |

The wiki kinds only exist when the option `wiki_dir` is set.

## NEEDS SIGN-OFF (review-*.md, applied only with [x])

- Any edit to your own skill (SKILL.md, references, scripts) and any new skill.
- Structural wiki rewrite: create/merge/split/rename a page, move a fact between pages, rewrite a section.
- Contradiction (page vs page, wiki vs memory, wiki vs CLAUDE.md, skill vs skill): the proposal says which side stays and why.
- Global or project `CLAUDE.md`, hooks, settings, output styles, memory content.
- Bloat: delete/disable a skill, cut a section, merge duplicated rules.
- New automation (cron, launchd, a script on your PATH).

## MORE CONTEXT REQUIRED (needs-context-*.md)

- Two sources disagree and there is no way to know which one is right.
- A pattern that may be on purpose (a skill unused for 30 days may be seasonal).
- A finding that depends on a business decision or on something outside the disk.
A good question: 1 sentence, with 2-3 options and what changes with each.
