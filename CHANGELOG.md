# Changelog

All notable changes to this plugin. Versions follow `.claude-plugin/plugin.json`.

## 0.4.7 (2026-11-13)

- Skill schema and score: version and tags in the frontmatter, third-person descriptions, Purpose, Prerequisites, Limitations, Troubleshooting and script tables in the skills; skill score badges.

## 0.4.6 (2026-11-13)

- Add author metadata, Instructions and Examples sections to the skills.

## 0.4.5 (2026-11-12)

- Audit round 2: approval integrity, apply-patch safety, guard gaps.

## 0.4.4 (2026-11-11)

- Guard: judge pipes and loops as a whole; `uniq OUT` and `cp -t` count as writes.

## 0.4.3 (2026-11-11)

- Maintenance: point to the catalog repo's new name, lucas-tools.

## 0.4.2 (2026-11-11)

- Maintenance: rename the marketplace to lucas-tools.

## 0.4.1 (2026-11-09)

- Docs: one-line description, catalog install, settings and license sections.

## 0.4.0 (2026-11-08)

- `labels` option: your own words for the fixed text written into the review files.

## 0.3.0 (2026-11-08)

- Config options: pre-run command, wait lock, extra lint command, language, analyst notes, claude command.
- Config in `~/.config/self-improve` (the old file is still read), created on first run.

## 0.2.1 (2026-11-08)

- Docs: Windows and Linux test status; credit for the idea.

## 0.2.0 (2026-11-08)

- Bind approval to the reviewed content, block inline code during a run, validate patches.
- CI runs the tests on Linux, macOS and Windows; Windows path and encoding fixes.

## 0.1.0 (2026-11-08)

- First release: find repeated corrections, review them with buttons, apply only what you approved.
