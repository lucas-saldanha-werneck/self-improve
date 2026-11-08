import json
from conftest import run, last_run_dir


def set_patch(env, iid, patch):
    p = env["state"] / "runs" / "R0" / "plan.json"
    plan = json.loads(p.read_text())
    for it in plan:
        if it["id"] == iid:
            it["patch"] = patch
    p.write_text(json.dumps(plan))


def start(env):
    run(env, "start", "--apply-only")
    return last_run_dir(env).name


# ---- exact patch, applied by the script ----

def test_apply_patch_exact(env):
    set_patch(env, "S01", {"old": "before", "new": "after"})
    rid = start(env)
    r = run(env, "apply-patch", rid, "--id", "R0/S01")
    assert env["target"].read_text() == "after\n"
    assert list(env["target"].parent.glob("target.txt.bak-*")), "sem backup"
    applied = [json.loads(l) for l in (env["state"] / "runs" / rid / "applied.jsonl").read_text().splitlines()]
    assert [a["id"] for a in applied] == ["R0/S01"]
    assert "R0/S01" in (env["out"] / "change-log.md").read_text() and "exact patch" in r.stdout


def test_apply_patch_target_changed_refuses(env):
    set_patch(env, "S01", {"old": "NOT THERE", "new": "x"})
    rid = start(env)
    r = run(env, "apply-patch", rid, "--id", "R0/S01", ok=False)
    assert r.returncode == 4 and env["target"].read_text() == "before\n"
    assert not (env["state"] / "runs" / rid / "applied.jsonl").exists()


def test_apply_patch_ambiguous_refuses(env):
    env["target"].write_text("before before\n")
    set_patch(env, "S01", {"old": "before", "new": "after"})
    rid = start(env)
    assert run(env, "apply-patch", rid, "--id", "R0/S01", ok=False).returncode == 4
    assert env["target"].read_text() == "before before\n"


def test_apply_patch_not_approved_refuses(env):
    set_patch(env, "S02", {"old": "before", "new": "after"})  # S02 is [ ] (not approved)
    rid = start(env)
    r = run(env, "apply-patch", rid, "--id", "R0/S02", ok=False)
    assert r.returncode != 0 and "not approved" in (r.stdout + r.stderr)
    assert env["target"].read_text() == "before\n"


def test_apply_patch_without_patch_refuses(env):
    rid = start(env)
    r = run(env, "apply-patch", rid, "--id", "R0/S01", ok=False)
    assert r.returncode != 0 and "no 'patch'" in (r.stdout + r.stderr)


def test_apply_patch_needs_active_run(env):
    set_patch(env, "S01", {"old": "before", "new": "after"})
    r = run(env, "apply-patch", "R9", "--id", "R0/S01", ok=False)
    assert r.returncode != 0 and env["target"].read_text() == "before\n"


def test_render_validates_patch(env):
    rid = start(env)
    rd = env["state"] / "runs" / rid
    (rd / "plan.json").write_text(json.dumps([{"bucket": "signoff", "kind": "skill-edit", "title": "p",
                                               "target": str(env["target"]),
                                               "patch": {"old": "NOT THERE", "new": "x"}}]))
    r = run(env, "render", rid, ok=False)
    assert r.returncode != 0 and "patch" in (r.stdout + r.stderr)


# ---- intent before editing: never apply twice ----

def test_intent_refuses_foreign_target(env):
    rid = start(env)
    other = env["tmp"] / "outro.txt"
    other.write_text("x")
    r = run(env, "intent", rid, "--id", "R0/S01", "--target", str(other), ok=False)
    assert r.returncode != 0 and "not a target" in (r.stdout + r.stderr)


def test_crash_between_edit_and_log_flags_possibly_applied(env):
    rid = start(env)
    run(env, "intent", rid, "--id", "R0/S01", "--target", str(env["target"]))
    env["target"].write_text("after\n")          # edited…
    run(env, "abort", rid, "--note", "crashed")      # …and crashed before log
    rid2 = start(env)
    appr = json.loads((env["state"] / "runs" / rid2 / "approved.json").read_text())
    s01 = [x for x in appr if x["id"] == "S01"][0]
    assert s01.get("possibly_applied") is True


def test_intent_without_edit_is_not_flagged(env):
    rid = start(env)
    run(env, "intent", rid, "--id", "R0/S01", "--target", str(env["target"]))
    run(env, "abort", rid, "--note", "crashed before editing")
    rid2 = start(env)
    appr = json.loads((env["state"] / "runs" / rid2 / "approved.json").read_text())
    assert not [x for x in appr if x["id"] == "S01"][0].get("possibly_applied")


# ---- a failed review launch is visible ----

def test_finish_reports_launch_failure(env):
    hr = env["tmp"] / "hr-fail.py"
    hr.write_text("import sys\nprint('review: failed to open REVIEW 01-01: tmux is gone')\nsys.exit(1)\n")
    env["env"].update(HUMAN_REVIEW_SCRIPT=str(hr))
    rd = env["state"] / "runs" / "R1"
    rd.mkdir()
    (rd / "plan.json").write_text("[]")
    (rd / "render.json").write_text("{}")
    with open(env["state"] / "history.jsonl", "a") as f:
        f.write(json.dumps({"event": "start", "run_id": "R1", "covered_from": "x", "covered_until": "y"}) + "\n")
    r = run(env, "finish", "R1")
    assert "review: FAILED" in r.stdout
