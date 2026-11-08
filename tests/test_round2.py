"""Regression tests for the second review round: approval bound to content, inline interpreter code
during a run, config validation in apply-patch, decisions recorded during a run, notes on carried items."""
import json
from conftest import run, last_run_dir, HR
from test_guard import hook, lock


def plan_p(env, rid="R0"):
    return env["state"] / "runs" / rid / "plan.json"


def edit_item(env, iid, rid="R0", **fields):
    p = plan_p(env, rid)
    plan = json.loads(p.read_text(encoding="utf-8"))
    for it in plan:
        if it["id"] == iid:
            it.update(fields)
    p.write_text(json.dumps(plan), encoding="utf-8")


def start_apply_only(env):
    run(env, "start", "--apply-only")
    return last_run_dir(env).name


def review(env):
    return env["out"] / "review-2026-09-20.md"


# ---- approval is bound to the reviewed content, not to a mutable id ----

def test_plan_edited_after_approval_does_not_change_patch(env):
    edit_item(env, "S01", patch={"old": "before", "new": "after"})
    rid = start_apply_only(env)
    edit_item(env, "S01", patch={"old": "before", "new": "UNAPPROVED"})  # plan changed after start
    run(env, "apply-patch", rid, "--id", "R0/S01")
    assert env["target"].read_text(encoding="utf-8") == "after\n"


def test_plan_edited_between_render_and_approval_is_not_approved(env):
    rid = start_apply_only(env)
    rd = env["state"] / "runs" / rid
    (rd / "plan.json").write_text(json.dumps([{"bucket": "signoff", "kind": "skill-edit", "title": "p",
                                               "target": str(env["target"]),
                                               "patch": {"old": "before", "new": "shown"}}]), encoding="utf-8")
    run(env, "render", rid)
    new_review = json.loads((rd / "render.json").read_text(encoding="utf-8"))["review"]
    run(env, "mark", "--file", new_review, "--ref", f"{rid}/S01", "--choice", "apply", script=HR)
    edit_item(env, "S01", rid=rid, patch={"old": "before", "new": "UNAPPROVED"})  # not what was shown
    run(env, "log", rid, "--id", "R0/S01", "--target", str(env["target"]), "--summary", "t")
    run(env, "finish", rid, "--no-review")
    rid2 = start_apply_only(env)
    rd2 = env["state"] / "runs" / rid2
    approved = [f"{x['run_id']}/{x['id']}" for x in json.loads((rd2 / "approved.json").read_text(encoding="utf-8"))]
    assert f"{rid}/S01" not in approved
    assert any("changed after it was shown" in w for w in json.loads((rd2 / "review-warnings.json").read_text(encoding="utf-8")))


# ---- apply-patch validates the result before replacing the file ----

def test_apply_patch_refuses_invalid_json(env):
    cfg = env["tmp"] / "settings.json"
    cfg.write_text('{"a": 1}\n', encoding="utf-8")
    edit_item(env, "S01", target=str(cfg), patch={"old": "1}", "new": "1,}"})
    rid = start_apply_only(env)
    r = run(env, "apply-patch", rid, "--id", "R0/S01", ok=False)
    assert r.returncode != 0 and "JSON" in (r.stdout + r.stderr)
    assert cfg.read_text(encoding="utf-8") == '{"a": 1}\n'
    assert not (env["state"] / "runs" / rid / "applied.jsonl").exists()


def test_apply_patch_refuses_skill_without_frontmatter(env):
    sk = env["claude"] / "skills" / "demo" / "SKILL.md"
    sk.parent.mkdir(parents=True)
    sk.write_text("---\nname: demo\ndescription: d\n---\nbody\n", encoding="utf-8")
    edit_item(env, "S01", target=str(sk), patch={"old": "name: demo\n", "new": ""})
    rid = start_apply_only(env)
    r = run(env, "apply-patch", rid, "--id", "R0/S01", ok=False)
    assert r.returncode != 0 and "frontmatter" in (r.stdout + r.stderr)
    assert "name: demo" in sk.read_text(encoding="utf-8")


# ---- a decision recorded while a run is open is not lost ----

def test_mark_during_run_is_not_consumed(env):
    rid = start_apply_only(env)
    run(env, "mark", "--file", str(review(env)), "--ref", "R0/S02", "--choice", "apply", script=HR)
    run(env, "log", rid, "--id", "R0/S01", "--target", str(env["target"]), "--summary", "t")
    run(env, "render", rid)
    run(env, "finish", rid, "--no-review")
    rid2 = start_apply_only(env)
    approved = json.loads((env["state"] / "runs" / rid2 / "approved.json").read_text(encoding="utf-8"))
    assert "S02" in [x["id"] for x in approved]


def test_answer_during_run_is_not_consumed(env):
    run(env, "start")
    rid = last_run_dir(env).name
    ctx = env["out"] / "needs-context-2026-09-20.md"
    run(env, "mark", "--file", str(ctx), "--ref", "R0/C02", "--answer", "yes, on purpose", script=HR)
    (env["state"] / "runs" / rid / "plan.json").write_text("[]", encoding="utf-8")
    run(env, "log", rid, "--id", "R0/S01", "--target", str(env["target"]), "--summary", "t")
    run(env, "render", rid)
    run(env, "finish", rid, "--no-review")
    run(env, "start")
    rd2 = last_run_dir(env)
    assert "R0/C02" in [f"{x['run_id']}/{x['id']}" for x in json.loads((rd2 / "answers.json").read_text(encoding="utf-8"))]
    assert "C02" not in [x["id"] for x in json.loads((rd2 / "open-questions.json").read_text(encoding="utf-8"))]


# ---- a note on a pending item survives the carry ----

def test_carried_item_keeps_note(env):
    t = review(env).read_text(encoding="utf-8").replace("- [ ] **S02 · change b** <!-- R0/S02 -->\n    - Where: `target.txt` (skill-edit)\n",
                                        "- [ ] **S02 · change b** <!-- R0/S02 -->\n    - Where: `target.txt` (skill-edit)\n"
                                        "    - Note: only on line 3\n")
    review(env).write_text(t, encoding="utf-8")
    rid = start_apply_only(env)
    run(env, "render", rid)
    new = json.loads((env["state"] / "runs" / rid / "render.json").read_text(encoding="utf-8"))["review"]
    assert "Note: only on line 3" in open(new).read()


# ---- guard: inline interpreter code is blocked during a run ----

def test_guard_blocks_constructed_path_in_inline_python(env):
    lock(env)
    cmd = "python3 -c 'from pathlib import Path; (Path.home()/\".claude\"/\"CLAUDE.md\").write_text(\"x\")'"
    assert hook(env, "Bash", {"command": cmd})


def test_guard_blocks_interpreter_heredoc(env):
    lock(env)
    target = env["claude"] / "CLAUDE.md"
    cmd = f"python3 <<'PY'\nopen({str(target)!r}, 'w').write('x')\nPY"
    assert hook(env, "Bash", {"command": cmd})


def test_guard_still_allows_plain_reads_and_cat_heredoc(env):
    lock(env)
    assert hook(env, "Bash", {"command": "python3 --version"}) is None
    assert hook(env, "Bash", {"command": f"cat <<'EOF' > {env['tmp']}/notes.txt\nhello\nEOF"}) is None
    assert hook(env, "Bash", {"command": f"python3 {env['tmp']}/script.py"}) is None


def test_guard_inline_code_opt_out(env):
    lock(env)
    env["env"]["SELF_IMPROVE_ALLOW_INLINE_CODE"] = "1"
    assert hook(env, "Bash", {"command": "python3 -c 'print(1)'"}) is None
    target = env["claude"] / "CLAUDE.md"
    assert hook(env, "Bash", {"command": f"python3 -c \"open('{target}','w').write('x')\""})


# ---- apply-patch keeps the file's own line endings (Windows text mode would change them) ----

def test_apply_patch_keeps_lf_file_lf(env):
    env["target"].write_bytes(b"one\ntwo\n")
    edit_item(env, "S01", patch={"old": "one\ntwo\n", "new": "one\nTWO\n"})
    rid = start_apply_only(env)
    run(env, "apply-patch", rid, "--id", "R0/S01")
    assert env["target"].read_bytes() == b"one\nTWO\n"


def test_apply_patch_matches_lf_patch_in_crlf_file(env):
    env["target"].write_bytes(b"one\r\ntwo\r\n")
    edit_item(env, "S01", patch={"old": "one\ntwo\n", "new": "one\nTWO\n"})
    rid = start_apply_only(env)
    run(env, "apply-patch", rid, "--id", "R0/S01")
    assert env["target"].read_bytes() == b"one\r\nTWO\r\n"
