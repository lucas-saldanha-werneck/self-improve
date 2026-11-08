import json, os
from conftest import run

from conftest import HR


def items(env):
    return json.loads(run(env, "items", script=HR).stdout)


def review(env):
    return env["out"] / "review-2026-09-20.md"


def ctx(env):
    return env["out"] / "needs-context-2026-09-20.md"


def test_items_lists_open_only(env):
    d = items(env)
    assert [(c["kind"], c["ref"]) for c in d["open"]] == [("signoff", "R0/S02"), ("context", "R0/C02")]
    s02 = d["open"][0]
    assert s02["title"] == "change b" and s02["recommend"] == "apply" and s02["file"] == str(review(env))


def test_items_counts_approved_waiting(env):
    assert items(env)["approved"] == 1  # S01 marked [x] and not applied yet


def test_mark_signoff_with_note(env):
    run(env, "mark", "--file", str(review(env)), "--ref", "R0/S02", "--choice", "apply",
        "--note", "only on line 3", script=HR)
    t = review(env).read_text()
    assert "- [x] **S02 · change b** <!-- R0/S02 -->" in t and "    - Note: only on line 3" in t
    d = items(env)
    assert d["approved"] == 2 and [c["ref"] for c in d["open"]] == ["R0/C02"]


def test_mark_twice_replaces_note_and_choice(env):
    for choice, note in (("apply", "one"), ("later", "two")):
        run(env, "mark", "--file", str(review(env)), "--ref", "R0/S02", "--choice", choice, "--note", note, script=HR)
    t = review(env).read_text()
    assert "- [ ] **S02" in t and t.count("Note:") == 1 and "Note: two" in t


def test_mark_recusar(env):
    run(env, "mark", "--file", str(review(env)), "--ref", "R0/S02", "--choice", "reject", script=HR)
    assert "- [-] **S02" in review(env).read_text()


def test_mark_answer(env):
    run(env, "mark", "--file", str(ctx(env)), "--ref", "R0/C02", "--answer", "yes, on purpose", script=HR)
    assert "**Answer:** yes, on purpose" in ctx(env).read_text()
    assert "R0/C02" not in [c["ref"] for c in items(env)["open"]]


def test_mark_answer_replaces_old_answer(env):
    run(env, "mark", "--file", str(ctx(env)), "--ref", "R0/C01", "--answer", "B", script=HR)
    t = ctx(env).read_text()
    assert "**Answer:** B" in t and "**Answer:** A" not in t


def test_mark_note_is_single_line(env):
    run(env, "mark", "--file", str(review(env)), "--ref", "R0/S02", "--choice", "apply",
        "--note", "line 1\nline 2 -->", script=HR)
    assert "    - Note: line 1 line 2 --&gt;" in review(env).read_text()
    assert items(env)["approved"] == 2


def test_mark_tolerant_format(env):
    # phone/notes app: no bold, "*" instead of "-", empty checkbox "[]"
    t = review(env).read_text().replace("- [ ] **S02 · change b**", "* [] S02 change b")
    review(env).write_text(t)
    run(env, "mark", "--file", str(review(env)), "--ref", "R0/S02", "--choice", "apply", script=HR)
    assert "* [x] S02 change b <!-- R0/S02 -->" in review(env).read_text()
    assert items(env)["approved"] == 2


def test_mark_errors(env):
    assert run(env, "mark", "--file", str(review(env)), "--ref", "R0/S99", "--choice", "apply",
               script=HR, ok=False).returncode != 0
    assert run(env, "mark", "--file", str(review(env)), "--ref", "R0/S02", "--answer", "x",
               script=HR, ok=False).returncode != 0
    fora = env["tmp"] / "outside.md"
    fora.write_text(review(env).read_text())
    assert run(env, "mark", "--file", str(fora), "--ref", "R0/S02", "--choice", "apply",
               script=HR, ok=False).returncode != 0


def test_mark_refuses_consumed_review(env):
    import json as _j
    with open(env["state"] / "history.jsonl", "a") as f:
        f.write(_j.dumps({"event": "finish", "run_id": "RX", "consumed": [str(review(env))]}) + "\n")
    r = run(env, "mark", "--file", str(review(env)), "--ref", "R0/S02", "--choice", "apply", script=HR, ok=False)
    assert r.returncode != 0 and "already read" in (r.stdout + r.stderr)
    assert "- [ ] **S02" in review(env).read_text()


def test_mark_leaves_no_temp_files(env):
    run(env, "mark", "--file", str(review(env)), "--ref", "R0/S02", "--choice", "apply", script=HR)
    assert sorted(p.name for p in env["out"].iterdir()) == ["needs-context-2026-09-20.md", "review-2026-09-20.md"]


def test_items_carries_patch(env):
    import json as _j
    p = env["state"] / "runs" / "R0" / "plan.json"
    plan = _j.loads(p.read_text())
    plan[1]["patch"] = {"old": "before", "new": "after"}
    p.write_text(_j.dumps(plan))
    s02 = [c for c in items(env)["open"] if c["ref"] == "R0/S02"][0]
    assert s02["patch"] == {"old": "before", "new": "after"}
