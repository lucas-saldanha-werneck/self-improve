import json, os
from conftest import run, pyeval, last_run_dir


def test_apply_only_reads_marks_without_scanning(env):
    r = run(env, "start", "--apply-only")
    meta = json.loads(r.stdout)
    assert meta["apply_only"] is True
    assert meta["covered_from"] == meta["covered_until"]
    rd = last_run_dir(env)
    assert [i["id"] for i in json.loads((rd / "approved.json").read_text())] == ["S01"]
    assert [i["id"] for i in json.loads((rd / "carried.json").read_text())] == ["S02"]
    assert json.loads((rd / "answers.json").read_text()) == []
    assert json.loads((rd / "open-questions.json").read_text()) == []
    files = json.loads((rd / "review-files.json").read_text())
    assert len(files) == 1 and os.path.basename(files[0]) == "review-2026-09-20.md"
    assert json.loads((rd / "plan.json").read_text()) == []
    assert not (rd / "events.jsonl").exists()  # did not read sessions
    assert str(env["target"].resolve()) in (rd / "allowed-paths.txt").read_text()


def test_apply_only_keeps_window_and_needs_context(env):
    run(env, "start", "--apply-only")
    rid = last_run_dir(env).name
    run(env, "log", rid, "--id", "R0/S01", "--target", str(env["target"]), "--summary", "test")
    run(env, "render", rid)
    run(env, "finish", rid, "--no-review")
    assert pyeval(env, "import improve_system as s; print(s.last_finished(s.read_history())['run_id'])") == "R0"
    consumed = pyeval(env, "import improve_system as s; print(sorted(s.consumed_files(s.read_history())))")
    assert "review-2026-09-20.md" in consumed and "needs-context" not in consumed
    new = [p for p in env["out"].glob("review-*.md") if p.name != "review-2026-09-20.md"]
    assert len(new) == 1 and "<!-- R0/S02 -->" in new[0].read_text()  # "Later" is not lost
    assert [p.name for p in env["out"].glob("needs-context-*.md")] == ["needs-context-2026-09-20.md"]


def test_apply_only_render_new_signoff(env):
    run(env, "start", "--apply-only")
    rd = last_run_dir(env)
    (rd / "plan.json").write_text(json.dumps([{"bucket": "signoff", "kind": "skill-edit",
                                               "title": "S01 again: target changed", "target": str(env["target"])}]))
    run(env, "render", rd.name)
    r = run(env, "finish", rd.name, "--no-review", ok=False)
    assert r.returncode != 0 and "R0/S01" in (r.stdout + r.stderr)
    run(env, "finish", rd.name, "--no-review", "--allow-missing", "--note", "target changed")


def fake_hr(env):
    log = env["tmp"] / "hr.args"
    hr = env["tmp"] / "hr.py"
    hr.write_text(f"import sys\nopen({str(log)!r}, 'w').write(' '.join(sys.argv[1:]))\n"
                  "print('review: opened X (1 itens)')\n")
    env["env"].update(HUMAN_REVIEW_SCRIPT=str(hr), HUMAN_REVIEW_DRY_RUN="1")
    return log


def normal_run(env):
    """Minimal normal run, without scanning sessions."""
    rd = env["state"] / "runs" / "R1"
    rd.mkdir()
    (rd / "plan.json").write_text("[]")
    (rd / "render.json").write_text("{}")
    with open(env["state"] / "history.jsonl", "a") as f:
        f.write(json.dumps({"event": "start", "run_id": "R1", "covered_from": "x", "covered_until": "y"}) + "\n")
    return "R1"


def test_finish_launches_review(env):
    log = fake_hr(env)
    r = run(env, "finish", normal_run(env))
    assert "review: opened X" in r.stdout and log.read_text() == "launch --dry-run"


def test_finish_no_review_flag(env):
    log = fake_hr(env)
    run(env, "finish", normal_run(env), "--no-review")
    assert not log.exists()


def test_finish_apply_only_never_launches(env):
    log = fake_hr(env)
    run(env, "start", "--apply-only")
    rid = last_run_dir(env).name
    run(env, "log", rid, "--id", "R0/S01", "--target", str(env["target"]), "--summary", "t")
    run(env, "render", rid)
    run(env, "finish", rid)
    assert not log.exists()
