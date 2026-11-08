"""A real `start` on a plain install: fake transcripts in an isolated CLAUDE_CONFIG_DIR, no wiki."""
import json
from datetime import datetime, timedelta, timezone
from conftest import run, last_run_dir, py_command


def ts(minutes_ago):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def write_session(env, name, lines):
    d = env["claude"] / "projects" / "-home-me-project"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")


def user(text, m, **kw):
    return {"type": "user", "timestamp": ts(m), "cwd": "/home/me/project", "origin": {"kind": "human"},
            "message": {"role": "user", "content": text}, **kw}


def assistant(text, m):
    return {"type": "assistant", "timestamp": ts(m), "message": {"content": [{"type": "text", "text": text}]}}


def plain_env(env):
    env["env"].pop("SELF_IMPROVE_WIKI_DIR")
    for p in env["out"].glob("*.md"):
        p.unlink()
    (env["state"] / "history.jsonl").unlink()


def test_start_plain_install_reads_sessions(env):
    plain_env(env)
    write_session(env, "aaaaaaaa-1111", [
        user("please run the tests before you say it works", 50),
        assistant("Done, it works.", 49),
        user("I told you to run the tests first, again you did not", 48),
        {"type": "user", "timestamp": ts(47), "isMeta": True, "message": {"content": "meta text"}},
    ])
    write_session(env, "bbbbbbbb-2222", [
        user("please run the tests before you say it works", 30),
    ])
    r = run(env, "start")
    meta = json.loads(r.stdout)
    assert meta["sessions"] == 2 and meta["messages"] == 3 and meta["hints"] >= 1
    assert meta["wiki_enabled"] is False and meta["wiki_chunks"] == 0
    rd = last_run_dir(env)
    chunk = (rd / "chunk-01.md").read_text()
    assert "I told you to run the tests first" in chunk and "meta text" not in chunk
    assert "2x in 2 sessions" in (rd / "repeats.md").read_text()
    assert (env["state"] / "active-run.json").exists()
    # an empty plan still renders and closes; the lock goes away
    (rd / "plan.json").write_text("[]")
    run(env, "render", rd.name)
    out = run(env, "finish", rd.name).stdout
    assert "review: nothing pending" in out
    assert not (env["state"] / "active-run.json").exists()


def test_transcript_without_origin_field_still_read(env):
    plain_env(env)
    line = user("never push without asking me", 20)
    del line["origin"]
    write_session(env, "cccccccc-3333", [line])
    meta = json.loads(run(env, "start").stdout)
    assert meta["messages"] == 1


def test_render_refuses_auto_on_skill_and_too_many_signoffs(env):
    plain_env(env)
    meta = json.loads(run(env, "start").stdout)
    rd = last_run_dir(env)
    skill = env["claude"] / "skills" / "demo" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: demo\ndescription: d\n---\n")
    (rd / "plan.json").write_text(json.dumps([{"bucket": "auto", "kind": "typo", "title": "t", "target": str(skill)}]))
    r = run(env, "render", rd.name, ok=False)
    assert "never AUTO" in r.stderr
    many = [{"bucket": "signoff", "kind": "skill-edit", "title": f"t{i}", "target": str(skill)} for i in range(11)]
    (rd / "plan.json").write_text(json.dumps(many))
    assert "the maximum is 10" in run(env, "render", rd.name, ok=False).stderr
    run(env, "abort", meta["run_id"])


def test_wait_for_lock_exits_3(env):
    other = env["tmp"] / "other-job.lock"
    other.write_text("{}")
    env["env"]["SELF_IMPROVE_WAIT_FOR_LOCK"] = str(other)
    assert run(env, "start", ok=False).returncode == 3


def test_notify_not_configured_is_a_noop(env):
    assert "notify: not configured" in run(env, "notify", "hello").stdout


def test_notify_runs_command(env):
    log = env["tmp"] / "notify.txt"
    script = env["tmp"] / "notify.py"
    script.write_text(f"import sys\nopen({str(log)!r}, 'w', encoding='utf-8').write(sys.argv[1])\n")
    env["env"]["SELF_IMPROVE_NOTIFY_COMMAND"] = py_command(script)
    assert "notify: sent" in run(env, "notify", "hello there").stdout
    assert log.read_text() == "hello there"
