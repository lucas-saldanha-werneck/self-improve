# Changelog

All notable changes to this plugin. Versions follow `.claude-plugin/plugin.json`.

## 0.4.7 (2026-11-13)

- Skills: version and tags in the frontmatter, third-person descriptions, Purpose, Prerequisites, Limitations, Troubleshooting and script tables; skill score badges.
- README: demo GIF and badges; add SECURITY.md and this CHANGELOG.

## 0.4.6 (2026-11-13)

- Skills: add author metadata, Instructions and Examples sections (docs only).

## 0.4.5 (2026-11-12)

- apply-patch refuses an item with a user note or one already applied, and keeps file mode and symlinks.
- The approved copy wins over later plan.json edits; render flattens plan text so no checkbox or id can be forged.
- Guard: closes gaps (python options, `ln` into a zone, review files, project CLAUDE.md and memory, Monitor/PowerShell).
- history.jsonl tolerates a broken line; change-log.md is written atomically.

## 0.4.4 (2026-11-11)

- Guard: judge pipes and loops as a whole; `uniq OUT` and `cp -t` count as writes.

## 0.4.3 (2026-11-11)

- Point to the catalog repo's new name, `lucas-tools`.

## 0.4.2 (2026-11-11)

- Catalog renamed to `lucas-tools`.

## 0.4.1 (2026-11-09)

- One-line description, catalog install steps, settings and license sections.

## 0.4.0 (2026-11-08)

- `labels` option: your own words for the fixed text written into the review files.
- `labels`: parse each file with the labels it was written with; stricter clash checks.

## 0.3.0 (2026-11-08)

- Config options: pre-run command, wait lock, extra lint command, language, analyst notes, claude command.
- Config in `~/.config/self-improve` (the old file is still read), created on first run from `config.example.json`.
- `start --force` no longer drops the active run on a busy answer.
- README: credit for the idea.

## 0.2.1 (2026-11-08)

- Windows: path comparison, UTF-8 files, CRLF patches; README shows Windows and Linux test status.
- CI runs the tests on Linux, macOS and Windows.

## 0.2.0 (2026-11-08)

- Bind approval to the reviewed content, block inline code during a run, validate patches.

## 0.1.0 (2026-11-08)

- First release: find repeated corrections, review them with buttons, apply only what you approved.
