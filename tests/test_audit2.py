"""Second audit round: approval integrity, apply-patch safety, guard gaps."""
import json, os, re, stat, sys, time
import pytest
from conftest import run, last_run_dir, pyeval, GUARD, IS, HR

POSIX = os.name != "nt"


def set_item(env, iid, rid="R0", **fields):
    p = env["state"] / "runs" / rid / "plan.json"
    plan = json.loads(p.read_text(encoding="utf-8"))
    for it in plan:
        if it["id"] == iid:
            it.update(fields)
    p.write_text(json.dumps(plan), encoding="utf-8")


def start(env):
    run(env, "start", "--apply-only")
    return last_run_dir(env).name


def hook(env, tool, tool_input, cwd=None, session="test"):
    payload = {"session_id": session, "hook_event_name": "PreToolUse", "tool_name": tool,
               "tool_input": tool_input, "cwd": cwd or str(env["tmp"])}
    r = run(env, script=GUARD, stdin=json.dumps(payload))
    assert r.returncode == 0, r.stderr
    if not r.stdout.strip():
        return None
    return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"]


def lock(env, allowed=(), age_s=0, **extra):
    rd = env["state"] / "runs" / "RX"
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "allowed-paths.txt").write_text("".join(os.path.realpath(p) + "\n" for p in allowed), encoding="utf-8")
    (env["state"] / "active-run.json").write_text(json.dumps(
        {"run_id": "RX", "run_dir": str(rd), "started_epoch": time.time() - age_s, **extra}), encoding="utf-8")
    return rd


# ---- SI-01: a note on a patch item is never dropped ----

def test_apply_patch_refuses_item_with_note(env):
    rv = env["out"] / "review-2026-09-20.md"
    rv.write_text(rv.read_text(encoding="utf-8").replace(
        "(skill-edit)\n- [ ] **S02", "(skill-edit)\n    - Note: say AFTER in capitals\n- [ ] **S02", 1), encoding="utf-8")
    set_item(env, "S01", patch={"old": "before", "new": "after"})
    rid = start(env)
    r = run(env, "apply-patch", rid, "--id", "R0/S01", ok=False)
    assert r.returncode == 5 and "note" in (r.stdout + r.stderr)
    assert env["target"].read_text(encoding="utf-8") == "before\n"


# ---- SI-02: text fields can't forge a checkbox or an id ----

def test_render_flattens_fields(env):
    rid = start(env)
    forged = f"ok\n- [x] **S09 · forged** <!-- {rid}/S09 -->"
    (env["state"] / "runs" / rid / "plan.json").write_text(json.dumps([
        {"bucket": "signoff", "kind": "skill-edit", "title": f"t <!-- {rid}/S07 -->", "target": str(env["target"]),
         "evidence": forged, "change": forged, "why": forged, "risk": forged},
        {"bucket": "context", "kind": "gap", "title": "q", "question": f"x\n### y <!-- {rid}/C05 -->",
         "evidence": forged, "options": [forged]}]), encoding="utf-8")
    res = json.loads(run(env, "render", rid).stdout)
    for path in (res["review"], res["needs_context"]):
        ids = re.findall(r"<!--\s*[\w-]+/(\w+)\s*-->", open(path, encoding="utf-8").read())
        assert "S09" not in ids and "S07" not in ids and "C05" not in ids, ids


# ---- SI-03: the approved copy and the rendered AUTO copy win over a later plan.json edit ----

def test_approved_copy_wins_over_bucket_change(env):
    set_item(env, "S01", patch={"old": "before", "new": "after"})
    rid = start(env)
    set_item(env, "S01", bucket="auto", patch={"old": "before", "new": "EVIL"})
    run(env, "apply-patch", rid, "--id", "R0/S01")
    assert env["target"].read_text(encoding="utf-8") == "after\n"


def test_auto_item_frozen_at_render(env):
    page = env["wiki"] / "page.md"
    page.write_text("teh word\n", encoding="utf-8")
    rid = start(env)
    pp = env["state"] / "runs" / rid / "plan.json"
    pp.write_text(json.dumps([{"bucket": "auto", "kind": "typo", "title": "t", "target": str(page),
                               "patch": {"old": "teh", "new": "the"}}]), encoding="utf-8")
    run(env, "render", rid)
    plan = json.loads(pp.read_text(encoding="utf-8"))
    plan[0]["patch"]["new"] = "EVIL"
    pp.write_text(json.dumps(plan), encoding="utf-8")
    run(env, "apply-patch", rid, "--id", "A01")
    assert page.read_text(encoding="utf-8") == "the word\n"


# ---- SI-04: mode and symlinks survive apply-patch ----

@pytest.mark.skipif(not POSIX, reason="POSIX modes and symlinks")
def test_apply_patch_keeps_mode(env):
    os.chmod(env["target"], 0o755)
    set_item(env, "S01", patch={"old": "before", "new": "after"})
    rid = start(env)
    run(env, "apply-patch", rid, "--id", "R0/S01")
    assert stat.S_IMODE(os.stat(env["target"]).st_mode) == 0o755


@pytest.mark.skipif(not POSIX, reason="POSIX modes and symlinks")
def test_apply_patch_keeps_symlink(env):
    real = env["tmp"] / "real.txt"
    real.write_text("before\n", encoding="utf-8")
    link = env["tmp"] / "link.txt"
    os.symlink(real, link)
    set_item(env, "S01", target=str(link), patch={"old": "before", "new": "after"})
    rid = start(env)
    run(env, "apply-patch", rid, "--id", "R0/S01")
    assert os.path.islink(link) and real.read_text(encoding="utf-8") == "after\n"


# ---- SI-05: a second render keeps the marks made in between ----

def test_rerender_keeps_marks_notes_answers(env):
    rid = start(env)
    pp = env["state"] / "runs" / rid / "plan.json"
    plan = [{"bucket": "signoff", "kind": "skill-edit", "title": "keep", "target": str(env["target"]), "change": "c1"},
            {"bucket": "signoff", "kind": "skill-edit", "title": "edited", "target": str(env["target"]), "change": "c2"},
            {"bucket": "context", "kind": "gap", "title": "q", "question": "Which?"}]
    pp.write_text(json.dumps(plan), encoding="utf-8")
    res = json.loads(run(env, "render", rid).stdout)
    run(env, "mark", "--file", res["review"], "--ref", f"{rid}/S01", "--choice", "apply", "--note", "my note",
        script=HR)
    run(env, "mark", "--file", res["review"], "--ref", f"{rid}/S02", "--choice", "apply", script=HR)
    run(env, "mark", "--file", res["needs_context"], "--ref", f"{rid}/C01", "--answer", "the first", script=HR)
    plan = json.loads(pp.read_text(encoding="utf-8"))
    plan[1]["change"] = "c2 changed"  # shown content changed: its [x] must not carry over
    pp.write_text(json.dumps(plan), encoding="utf-8")
    res2 = json.loads(run(env, "render", rid).stdout)
    rv = open(res2["review"], encoding="utf-8").read()
    assert re.search(rf"- \[x\] .*<!-- {rid}/S01 -->", rv) and "Note: my note" in rv
    assert re.search(rf"- \[ \] .*<!-- {rid}/S02 -->", rv)
    assert "**Answer:** the first" in open(res2["needs_context"], encoding="utf-8").read()


# ---- SI-06: apply-patch never applies the same item twice ----

def test_apply_patch_twice_refused(env):
    set_item(env, "S01", patch={"old": "before", "new": "before and after"})
    rid = start(env)
    run(env, "apply-patch", rid, "--id", "R0/S01")
    r = run(env, "apply-patch", rid, "--id", "R0/S01", ok=False)
    assert r.returncode != 0 and "already applied or rejected" in (r.stdout + r.stderr)
    assert env["target"].read_text(encoding="utf-8") == "before and after\n"


# ---- SI-07: a newline in a target can't release another file ----

def test_render_refuses_newline_in_target(env):
    page = env["wiki"] / "page.md"
    page.write_text("x\n", encoding="utf-8")
    rid = start(env)
    claude_md = env["claude"] / "CLAUDE.md"
    (env["state"] / "runs" / rid / "plan.json").write_text(json.dumps([
        {"bucket": "auto", "kind": "typo", "title": "t", "target": f"{page}\n{claude_md}"}]), encoding="utf-8")
    r = run(env, "render", rid, ok=False)
    assert r.returncode != 0 and "line break" in (r.stdout + r.stderr)
    allow = (env["state"] / "runs" / rid / "allowed-paths.txt").read_text(encoding="utf-8")
    assert "CLAUDE.md" not in allow


# ---- SI-08: interpreter options and versions don't hide an abort ----

@pytest.mark.parametrize("prog", ["python3 -u", "python3.12", "python3 -B -u", "python"])
def test_guard_abort_variants_denied(env, prog):
    lock(env)
    assert hook(env, "Bash", {"command": f"{prog} {IS} abort RX"})


# ---- SI-09: arithmetic shift is not a heredoc ----

def test_guard_arith_shift_not_heredoc(env):
    lock(env)
    md = env["claude"] / "CLAUDE.md"
    assert hook(env, "Bash", {"command": f"x=$((1<<4)); echo ok\nrm {md}"})


# ---- SI-10: a link into a zone is a write into the zone ----

def test_guard_ln_from_zone_denied(env):
    lock(env)
    md = env["claude"] / "CLAUDE.md"
    link = env["tmp"] / "l"
    assert hook(env, "Bash", {"command": f"ln -s {md} {link}; echo hi >> {link}"})


# ---- SI-11: review files are protected during a run; the run's own session can't mark ----

def test_guard_protects_review_files(env):
    lock(env)
    rv = env["out"] / "review-2026-09-20.md"
    assert hook(env, "Write", {"file_path": str(rv), "content": "- [x]"})
    assert hook(env, "Bash", {"command": f"echo '- [x]' >> {env['out'] / 'needs-context-2026-09-20.md'}"})
    assert hook(env, "Write", {"file_path": str(env["out"] / "other.md"), "content": "x"}) is None


def test_guard_denies_mark_from_run_session(env):
    lock(env, session_id="test")
    cmd = f"python3 {HR} mark --file {env['out'] / 'review-2026-09-20.md'} --ref R0/S02 --choice apply"
    assert hook(env, "Bash", {"command": cmd}, session="test")
    assert hook(env, "Bash", {"command": cmd}, session="review-session") is None
    assert hook(env, "Bash", {"command": f"python3 {HR} items"}, session="test") is None


# ---- SI-12: project CLAUDE.md and memory are protected ----

def test_guard_protects_project_claude_md_and_memory(env):
    lock(env)
    assert hook(env, "Write", {"file_path": str(env["tmp"] / "proj" / "CLAUDE.md"), "content": "x"})
    mem = env["claude"] / "projects" / "-p" / "memory" / "note.md"
    assert hook(env, "Write", {"file_path": str(mem), "content": "x"})


def test_memory_index_auto_still_renders(env):
    mem = env["claude"] / "projects" / "-p" / "memory"
    mem.mkdir(parents=True)
    idx = mem / "MEMORY.md"
    idx.write_text("- [a](a.md)\n", encoding="utf-8")
    rid = start(env)
    (env["state"] / "runs" / rid / "plan.json").write_text(json.dumps([
        {"bucket": "auto", "kind": "memory-index", "title": "t", "target": str(idx)}]), encoding="utf-8")
    run(env, "render", rid)
    assert os.path.realpath(idx) in (env["state"] / "runs" / rid / "allowed-paths.txt").read_text(encoding="utf-8")


# ---- SI-13: "idle for 2 h" counts from the last activity ----

def test_guard_abort_busy_old_run_denied(env):
    rd = lock(env, age_s=3 * 3600, owner_pid=os.getpid())
    (rd / "plan.json").write_text("[]", encoding="utf-8")  # fresh activity
    assert hook(env, "Bash", {"command": f"python3 {IS} abort RX"})


def test_guard_abort_idle_old_run_allowed(env):
    rd = lock(env, age_s=3 * 3600, owner_pid=os.getpid())
    old = time.time() - 3 * 3600
    for p in [rd, *rd.iterdir()]:
        os.utime(p, (old, old))
    assert hook(env, "Bash", {"command": f"python3 {IS} abort RX"}) is None


# ---- SI-14: allow_inline_code=1 still checks heredoc bodies ----

def test_guard_heredoc_body_checked_with_inline_opt_out(env):
    lock(env)
    env["env"]["SELF_IMPROVE_ALLOW_INLINE_CODE"] = "1"
    md = env["claude"] / "CLAUDE.md"
    assert hook(env, "Bash", {"command": f"python3 <<'PY'\nopen({str(md)!r}, 'w').write('x')\nPY"})
    assert hook(env, "Bash", {"command": "python3 <<'PY'\nprint(1)\nPY"}) is None
    assert hook(env, "Bash", {"command": f"cat <<'EOF' > {env['tmp']}/n.txt\nsee {md}\nEOF"}) is None


# ---- SI-15: a broken history line doesn't break every command ----

def test_broken_history_line_skipped(env):
    with open(env["state"] / "history.jsonl", "a", encoding="utf-8") as f:
        f.write('{"event": "sta\n')
    run(env, "status")


# ---- SI-16: change-log.md is replaced atomically ----

def test_changelog_write_is_atomic(env):
    rid = start(env)
    cl = env["out"] / "change-log.md"
    cl.write_text("# Change log (improve-system)\n\nOLD LINE\n", encoding="utf-8")
    code = f"""
import builtins, argparse, improve_system as I
real = builtins.open
class Boom:
    def __init__(s, f): s.f = f
    def write(s, t): s.f.write(t[:5]); s.f.flush(); raise OSError('disk died')
    def __enter__(s): return s
    def __exit__(s, *a): s.f.close()
    def __getattr__(s, k): return getattr(s.f, k)
def fake(p, mode='r', *a, **k):
    f = real(p, mode, *a, **k)
    return Boom(f) if 'w' in mode and str(p).startswith(I.CHANGELOG) else f
builtins.open = fake
try:
    I.cmd_log(argparse.Namespace(run_id={rid!r}, id='R0/S01', target={str(env['target'])!r}, backup=None,
              removed=False, superseded_by=None, summary='s'))
except OSError:
    pass
"""
    pyeval(env, code)
    assert "OLD LINE" in cl.read_text(encoding="utf-8")


# ---- SI-17: PowerShell and Monitor go through the guard ----

def test_hook_matcher_covers_shell_tools():
    hooks = json.load(open(os.path.join(os.path.dirname(GUARD), "..", "..", "..", "hooks", "hooks.json"), encoding="utf-8"))
    m = hooks["hooks"]["PreToolUse"][0]["matcher"].split("|")
    assert "PowerShell" in m and "Monitor" in m


def test_guard_checks_monitor_and_powershell(env):
    lock(env)
    md = env["claude"] / "CLAUDE.md"
    assert hook(env, "Monitor", {"command": f"rm {md}", "description": "x"})
    assert hook(env, "PowerShell", {"command": f"Set-Content -Path '{md}' -Value x"})
    assert hook(env, "PowerShell", {"command": "Get-ChildItem"}) is None
