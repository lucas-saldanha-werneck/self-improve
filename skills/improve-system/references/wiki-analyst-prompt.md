# Wiki analyst prompt (1 subagent per wiki-NN.md)

Only used when the option `wiki_dir` is set. Replace `<RUN_DIR>` and `<NN>`. Dispatch together with the session analysts.

---

You audit a piece of the user's markdown wiki (topic pages hold facts, hub pages only point, every claim cites
a source). You do NOT edit any file other than your report.

Read:
1. `<RUN_DIR>/wiki-<NN>.md`: whole pages, each after `===== FILE: …`.
2. `<RUN_DIR>/changes.md`: what came in or changed in the window (raw sources, wiki, memory, skills).
3. `<RUN_DIR>/lint.md`: machine findings (links, orphans, INDEX). Don't repeat them; use them as hints only.
4. The wiki's own rules file, if there is one (`CLAUDE.md` in the wiki folder or its parent).
5. `<RUN_DIR>/previously-rejected.md`.
You may open raw sources or project files mentioned, to confirm.

Find:
- **contradiction**: two passages (on this page or another you open) say incompatible things. Quote both.
- **stale**: a dated fact that a newer source (in `changes.md`, the log, raw) contradicts or updates.
- **gap**: a new raw source (in the window) with no trace in the wiki; or a topic mentioned on several pages without its own page.
- **structure**: a fact repeated on 2+ pages (it should live on 1), a hub holding facts, a page too big to navigate, a claim without a source.
- **small-fix**: typo, relative date, link with an obvious target.

Write `<RUN_DIR>/wiki-findings-<NN>.json` (a JSON list; empty is valid):

```json
[{
  "kind": "contradiction | stale | gap | structure | small-fix",
  "page": "page path",
  "quote": "literal passage, up to 200 characters",
  "other": "the other passage/source (contradiction/stale), with path",
  "proposal": "what to change, concretely",
  "bucket_guess": "auto | signoff | context",
  "strength": 1
}]
```

`bucket_guess`: see `references/buckets.md` of the improve-system skill. Only `small-fix` may be `auto`. Contradictions and rewrites are `signoff`; if you can't tell which side is right, `context`.
`strength`: 3 = certain with both sources read; 2 = probable; 1 = suspicion.

After writing, read the file again and check it is valid JSON. Reply only: file path + number of findings.
