"""Option `labels`: your words for the fixed text `render` writes. Absent/null = today's English files;
set = your words, and the next run still reads notes and answers back; bad values = warnings, never a crash."""
import json
import os
import sys
from conftest import run, last_run_dir, HR, ROOT, SCRIPTS

FR = {"review_title": "Revue", "new": "Nouveaux", "still_pending": "En attente", "where": "Où",
      "evidence": "Preuve", "change": "Changement", "why": "Pourquoi", "risk": "Risque",
      "exact_change": "Changement exact", "replace": "remplacer", "with": "par", "pending_since": "Depuis",
      "note": "Remarque", "context_title": "Contexte", "why_i_ask": "Je demande car",
      "answer": "Réponse", "review_intro": "Run {run}: cochez [x]. Ajustez avec {note}.",
      "context_intro": "Run {run}: écrivez sur la ligne {answer}.", "review_footer": "Rien n'est appliqué."}


def config(env, value):
    (env["state"] / "config.json").write_text(json.dumps({"labels": value}), encoding="utf-8")


def plan(env, rid):
    items = [{"bucket": "signoff", "kind": "skill-edit", "title": "swap", "target": str(env["target"]),
              "evidence": "3 sessions", "change": "before -> after", "why": "clearer", "risk": "low",
              "patch": {"old": "before", "new": "after"}},
             {"bucket": "context", "kind": "gap", "title": "which", "question": "A or B?", "options": ["A", "B"],
              "evidence": "two pages disagree"}]
    (env["state"] / "runs" / rid / "plan.json").write_text(json.dumps(items), encoding="utf-8")


def render(env, rid, ok=True):
    plan(env, rid)
    r = run(env, "render", rid, ok=ok)
    res = json.loads(r.stdout) if r.returncode == 0 else {}
    review = open(res["review"], encoding="utf-8").read() if res.get("review") else ""
    ctx = open(res["needs_context"], encoding="utf-8").read() if res.get("needs_context") else ""
    return r, res, review, ctx


def new_run(env):
    run(env, "start", "--apply-only")
    return last_run_dir(env).name


def test_absent_null_and_empty_are_todays_english_files(env):
    rid = new_run(env)
    _, res, review, ctx = render(env, rid)
    assert "label_warnings" not in res
    assert review.startswith("# improve-system review, ")
    for line in ("## New", "    - Where: ", "    - Evidence: 3 sessions", "    - Change: before -> after",
                 "    - Why: clearer", "    - Risk: low", '    - Exact change: replace "before" with "after"',
                 "    - Pending since R0", "To adjust an item, write a line `Note: ...` right below it.",
                 "Nothing here is applied before the next run (or execute-reviewed)."):
        assert line in review, line
    assert ctx.startswith("# Context needed, ")
    assert "Answer on the **Answer:** line of each question." in ctx and "Why I ask: two pages disagree" in ctx
    assert "**Answer:** " in ctx
    for value in (None, {}, ""):
        config(env, value)
        r, res2, review2, ctx2 = render(env, rid)
        assert (review2, ctx2) == (review, ctx), value
        assert "label_warnings" not in res2 and "warning" not in r.stderr


def test_labels_replace_the_fixed_words(env):
    config(env, FR)
    rid = new_run(env)
    r, res, review, ctx = render(env, rid)
    assert "label_warnings" not in res, res
    assert review.startswith("# Revue, ")
    assert f"Run {rid}: cochez [x]. Ajustez avec Remarque." in review and "Rien n'est appliqué." in review
    for line in ("## Nouveaux", "## En attente", "    - Où: ", "    - Preuve: 3 sessions", "    - Pourquoi: clearer",
                 "    - Risque: low", '    - Changement exact: remplacer "before" par "after"', "    - Depuis R0"):
        assert line in review, line
    for english in ("## New", "Where:", "Evidence:", "Exact change", "Pending since"):
        assert english not in review, english
    assert ctx.startswith("# Contexte, ") and f"Run {rid}: écrivez sur la ligne Réponse." in ctx
    assert "Je demande car: two pages disagree" in ctx and "**Réponse:** " in ctx and "Answer" not in ctx


def test_partial_labels_keep_english_for_the_rest(env):
    config(env, {"new": "Novos"})
    _, _, review, _ = render(env, new_run(env))
    assert "## Novos" in review and "    - Where: " in review and "## Still pending" in review


def test_notes_and_answers_with_custom_labels_are_read_back(env):
    config(env, FR)
    rid = new_run(env)
    _, res, review, ctx = render(env, rid)
    run(env, "mark", "--file", res["review"], "--ref", f"{rid}/S01", "--choice", "later", "--note", "use B",
        script=HR)
    run(env, "mark", "--file", res["needs_context"], "--ref", f"{rid}/C01", "--answer", "B", script=HR)
    text = open(res["review"], encoding="utf-8").read()
    assert "    - Remarque: use B" in text and "Note:" not in text
    assert "**Réponse:** B" in open(res["needs_context"], encoding="utf-8").read()
    items = json.loads(run(env, "items", script=HR).stdout)["open"]
    s01 = next(i for i in items if i["ref"] == f"{rid}/S01")
    assert s01["notes"] == ["use B"]
    assert f"{rid}/C01" not in [i["ref"] for i in items], "the custom answer label was read as an answer"
    # a note typed by hand with the label, and the English word, both count
    text = text.replace("    - Remarque: use B", "    - Remarque: use B\n    - Note: and keep A")
    open(res["review"], "w", encoding="utf-8").write(text)
    s01 = next(i for i in json.loads(run(env, "items", script=HR).stdout)["open"] if i["ref"] == f"{rid}/S01")
    assert s01["notes"] == ["use B", "and keep A"]


def test_english_files_still_parse_after_labels_change(env):
    # conftest wrote English review files (R0); a label change must not hide their answers or notes
    config(env, FR)
    items = json.loads(run(env, "items", script=HR).stdout)
    refs = [i["ref"] for i in items["open"]]
    assert "R0/C01" not in refs and "R0/C02" in refs and "R0/S02" in refs
    assert items["approved"] == 1


def test_env_var_wins_and_takes_json(env):
    config(env, {"new": "Novos"})
    env["env"]["SELF_IMPROVE_LABELS"] = json.dumps({"new": "Neu"})
    _, _, review, _ = render(env, new_run(env))
    assert "## Neu" in review and "## Novos" not in review


def test_bad_values_warn_and_fall_back_without_crashing(env):
    rid = new_run(env)
    _, _, english, english_ctx = render(env, rid)
    for value in ("not json", "[1, 2]", 7, ["new"], True):
        config(env, value)
        r, res, review, ctx = render(env, rid)
        assert r.returncode == 0
        assert (review, ctx) == (english, english_ctx), value
        assert res["label_warnings"] and "warning: labels:" in r.stderr, value
    env["env"]["SELF_IMPROVE_LABELS"] = "{broken"
    r, res, review, _ = render(env, rid)
    assert review == english and "not valid JSON" in res["label_warnings"][0]
    del env["env"]["SELF_IMPROVE_LABELS"]


def test_bad_keys_and_values_are_ignored_one_by_one(env):
    config(env, {"new": "Novos", "colour": "x", "where": "", "why": 5, "risk": "two\nlines", "_comment": "ok"})
    rid = new_run(env)
    r, res, review, _ = render(env, rid)
    w = " ".join(res["label_warnings"])
    assert "unknown key 'colour'" in w and "'where' must be one line" in w and "'why'" in w and "'risk'" in w
    assert "_comment" not in w
    assert "## Novos" in review and "    - Where: " in review and "    - Why: " in review and "    - Risk: " in review


def test_labels_that_would_break_the_parser_fall_back(env):
    # a note label equal to a field label would turn "Where" lines into notes; ':' breaks the split
    config(env, {"note": "where", "answer": "A: B"})
    rid = new_run(env)
    r, res, review, ctx = render(env, rid)
    w = " ".join(res["label_warnings"])
    assert "'note' is the same word as the where field" in w and "'answer' can't contain ':'" in w
    assert "**Answer:** " in ctx
    run(env, "mark", "--file", res["review"], "--ref", f"{rid}/S01", "--choice", "later", script=HR)
    s01 = next(i for i in json.loads(run(env, "items", script=HR).stdout)["open"] if i["ref"] == f"{rid}/S01")
    assert s01["notes"] == [], "the Where line was not read as a note"


def test_field_label_that_is_a_note_word_falls_back(env):
    # "Nota" is always read as a note: an evidence line labelled "Nota" would become a note
    config(env, {"evidence": "Nota", "note": "Obs"})
    rid = new_run(env)
    r, res, review, _ = render(env, rid)
    assert "'evidence' = 'Nota' is a word the parser reads as a note" in " ".join(res["label_warnings"])
    assert "    - Evidence: 3 sessions" in review and "Nota:" not in review
    run(env, "mark", "--file", res["review"], "--ref", f"{rid}/S01", "--choice", "later", script=HR)
    assert "    - Evidence: 3 sessions" in open(res["review"], encoding="utf-8").read(), "mark kept the line"
    s01 = next(i for i in json.loads(run(env, "items", script=HR).stdout)["open"] if i["ref"] == f"{rid}/S01")
    assert s01["notes"] == []


def test_note_label_equal_to_an_english_field_word_falls_back(env):
    # older English files still hold "Where:" lines, even after where is relabelled
    config(env, {"where": "Onde", "note": "Where"})
    r = run(env, "items", script=HR)
    s02 = next(i for i in json.loads(r.stdout)["open"] if i["ref"] == "R0/S02")
    assert s02["notes"] == [], "the English Where line of an old file was not read as a note"
    rid = new_run(env)
    _, res, review, _ = render(env, rid)
    assert "'note' is the same word as the where field" in " ".join(res["label_warnings"])
    assert "    - Onde: " in review


def test_answers_and_notes_survive_a_second_label_change(env):
    config(env, FR)
    rid = new_run(env)
    _, res, _, _ = render(env, rid)
    run(env, "mark", "--file", res["review"], "--ref", f"{rid}/S01", "--choice", "later", "--note", "use B",
        script=HR)
    run(env, "mark", "--file", res["needs_context"], "--ref", f"{rid}/C01", "--answer", "B", script=HR)
    config(env, {"note": "Obs", "answer": "Resp"})  # labels change again before the next run
    items = json.loads(run(env, "items", script=HR).stdout)["open"]
    assert f"{rid}/C01" not in [i["ref"] for i in items], "the Réponse answer is still read"
    assert next(i for i in items if i["ref"] == f"{rid}/S01")["notes"] == ["use B"], "the Remarque note too"
    # the question left open in that file can still be answered, and a new note replaces the old one
    run(env, "mark", "--file", res["needs_context"], "--ref", f"{rid}/C01", "--answer", "A", script=HR)
    ctx = open(res["needs_context"], encoding="utf-8").read()
    assert "**Resp:** A" in ctx and "Réponse:** B" not in ctx
    run(env, "mark", "--file", res["review"], "--ref", f"{rid}/S01", "--choice", "later", "--note", "x", script=HR)
    text = open(res["review"], encoding="utf-8").read()
    assert "    - Obs: x" in text and "- Remarque:" not in text
    status = run(env, "status").stdout
    assert "answers=1" in status, status


def test_mark_says_why_a_label_fell_back(env):
    config(env, {"note": "Obs: "})
    r = run(env, "mark", "--file", str(env["out"] / "review-2026-09-20.md"), "--ref", "R0/S02", "--choice", "later",
            "--note", "n", script=HR)
    assert "warning: labels: 'note' can't contain ':'" in r.stderr


def test_every_label_key_and_default_is_documented():
    sys.path.insert(0, SCRIPTS)
    import si_config as C
    readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    doc = json.load(open(os.path.join(ROOT, "config.example.json"), encoding="utf-8"))["_doc"]["labels"]
    for k, v in C.LABELS.items():
        assert f"| `{k}` | {v.replace('|', '&#124;')} |" in readme, k
        assert f"{k} = {v!r}" in doc, k
