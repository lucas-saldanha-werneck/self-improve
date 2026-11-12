"""guard.py fed with real PreToolUse hook JSON on stdin. Output = a deny decision, or nothing (allow)."""
import json, os, time
from conftest import run, GUARD, IS


def hook(env, tool, tool_input, cwd=None):
    payload = {"session_id": "test", "hook_event_name": "PreToolUse", "tool_name": tool,
               "tool_input": tool_input, "cwd": cwd or str(env["tmp"])}
    r = run(env, script=GUARD, stdin=json.dumps(payload))
    assert r.returncode == 0, r.stderr
    if not r.stdout.strip():
        return None
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    return out["permissionDecisionReason"]


def lock(env, allowed=(), age_s=0):
    rd = env["state"] / "runs" / "RX"
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "allowed-paths.txt").write_text("".join(os.path.realpath(p) + "\n" for p in allowed), encoding="utf-8")
    (env["state"] / "active-run.json").write_text(json.dumps(
        {"run_id": "RX", "run_dir": str(rd), "started_epoch": time.time() - age_s}), encoding="utf-8")


def skill_file(env):
    p = env["claude"] / "skills" / "demo" / "SKILL.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\nname: demo\ndescription: demo\n---\n", encoding="utf-8")
    return str(p)


def test_no_run_allows_everything(env):
    assert hook(env, "Write", {"file_path": skill_file(env), "content": "x"}) is None


def test_run_blocks_skill_write(env):
    lock(env)
    reason = hook(env, "Edit", {"file_path": skill_file(env), "old_string": "a", "new_string": "b"})
    assert reason and "protected zone" in reason and "RX" in reason


def test_run_allows_released_exact_file(env):
    p = skill_file(env)
    lock(env, allowed=[p])
    assert hook(env, "Write", {"file_path": p, "content": "x"}) is None


def test_run_blocks_global_claude_md_wiki_and_project_settings(env):
    lock(env)
    assert hook(env, "Write", {"file_path": str(env["claude"] / "CLAUDE.md"), "content": "x"})
    assert hook(env, "Write", {"file_path": str(env["wiki"] / "page.md"), "content": "x"})
    proj = env["tmp"] / "proj" / ".claude" / "settings.json"
    assert hook(env, "Write", {"file_path": str(proj), "content": "{}"})


def test_run_allows_unrelated_file(env):
    lock(env)
    assert hook(env, "Write", {"file_path": str(env["tmp"] / "notes.txt"), "content": "x"}) is None


def test_bash_read_ok_write_blocked(env):
    p = skill_file(env)
    lock(env)
    assert hook(env, "Bash", {"command": f"cat {p} | grep name"}) is None
    assert "redirect" in hook(env, "Bash", {"command": f"echo hi >> {p}"})
    assert hook(env, "Bash", {"command": f"sed -i '' 's/a/b/' {p}"})
    assert hook(env, "Bash", {"command": f"python3 -c \"open('{p}','w').write('x')\""})


def test_bash_cp_into_zone_blocked(env):
    p = skill_file(env)
    lock(env)
    assert hook(env, "Bash", {"command": f"cp /etc/hosts {p}"})


def test_control_files_never_writable(env):
    lock(env)
    reason = hook(env, "Write", {"file_path": str(env["state"] / "runs" / "RX" / "allowed-paths.txt"), "content": "/x"})
    assert reason


def test_claude_cannot_abort_young_run(env):
    lock(env, age_s=60)
    reason = hook(env, "Bash", {"command": f"python3 {IS} abort RX"})
    assert reason and "Only the user aborts" in reason


def test_claude_can_abort_old_run(env):
    lock(env, age_s=3 * 3600)
    assert hook(env, "Bash", {"command": f"python3 {IS} abort RX"}) is None


def test_unreadable_lock_blocks(env):
    (env["state"] / "active-run.json").write_text("{not json", encoding="utf-8")
    assert hook(env, "Write", {"file_path": skill_file(env), "content": "x"})


def test_start_records_session(env):
    hook(env, "Bash", {"command": f"python3 {IS} start"})
    s = json.loads((env["state"] / "starting-session.json").read_text(encoding="utf-8"))
    assert s["session_id"] == "test"


def test_uniq_output_operand_is_a_write(env):
    p = skill_file(env)
    md = str(env["claude"] / "CLAUDE.md")
    lock(env)
    assert hook(env, "Bash", {"command": f"uniq /dev/null {md}"})  # uniq IN OUT empties OUT
    assert hook(env, "Bash", {"command": f"uniq {p}"}) is None
    assert hook(env, "Bash", {"command": f"sort {p} | uniq -c"}) is None
    assert hook(env, "Bash", {"command": f"uniq {p} {env['tmp'] / 'out.txt'}"}) is None


def test_cp_install_target_directory_option(env):
    skill_file(env)
    d = str(env["claude"] / "skills" / "demo")
    lock(env)
    src = str(env["tmp"] / "evil.md")
    assert hook(env, "Bash", {"command": f"cp -t {d} {src}"})
    assert hook(env, "Bash", {"command": f"cp --target-directory={d} {src}"})
    assert hook(env, "Bash", {"command": f"install -t {d} {src}"})
    assert hook(env, "Bash", {"command": f"cp -rt {d} {src}"})
    # copying OUT of a zone with -t is still fine
    assert hook(env, "Bash", {"command": f"cp -t {env['tmp']} {d}/SKILL.md"}) is None


def test_pipe_and_loop_writes_into_zone_blocked(env):
    skill_file(env)
    skills, md = str(env["claude"] / "skills"), str(env["claude"] / "CLAUDE.md")
    lock(env)
    assert hook(env, "Bash", {"command": f"grep -rl hi {skills} | xargs sed -i '' s/hi/yo/"})
    assert hook(env, "Bash", {"command": f"echo {md} | xargs rm"})
    assert hook(env, "Bash", {"command": f"find {skills} -name SKILL.md | xargs perl -pi -e s/a/b/"})
    assert hook(env, "Bash", {"command": f"for f in {skills}/*/SKILL.md; do sed -i '' s/a/b/ \"$f\"; done"})
    assert hook(env, "Bash", {"command": f"rm $(grep -rl hi {skills})"})
    assert hook(env, "Bash", {"command": f"for f in {skills}/*/SKILL.md; do mytool \"$f\"; done"})


def test_pipe_and_loop_reads_still_pass(env):
    p = skill_file(env)
    skills = str(env["claude"] / "skills")
    out = str(env["tmp"] / "out.txt")
    lock(env)
    for cmd in [f"cat {p} | grep name",
                f"grep -rl hi {skills} | head -5",
                f"find {skills} -name SKILL.md | wc -l",
                f"grep -rl hi {skills} | xargs wc -l",
                f"find {skills} -name '*.md' | xargs grep -n name",
                f"ls {skills} | sort | uniq",
                f"for f in {skills}/*/SKILL.md; do head -3 \"$f\"; done",
                f"grep -h name {skills}/*/SKILL.md | sort > {out}",
                f"git -C {skills} status && git -C {skills} log -1"]:
        assert hook(env, "Bash", {"command": cmd}) is None, cmd
