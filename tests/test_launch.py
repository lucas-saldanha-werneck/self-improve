import os, stat
from datetime import datetime
from conftest import run, HR

NAME = f"REVIEW {datetime.now():%d-%m}"
EXEC_NAME = f"EXEC {datetime.now():%d-%m}"


def fake_launcher(env):
    log = env["tmp"] / "launcher.args"
    fake = env["tmp"] / "launcher"
    fake.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" > '{log}'\necho 'session opened'\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    env["env"].update(SELF_IMPROVE_LAUNCH_COMMAND=f"{fake} . --tmux --name {{name}} --prompt {{prompt}}",
                      HUMAN_REVIEW_AGENTS_JSON="[]")
    return log


def test_launch_opens_with_name_and_prompt(env):
    log = fake_launcher(env)
    r = run(env, "launch", script=HR)
    assert f"review: opened {NAME} (3 items)" in r.stdout  # 2 open + 1 approved waiting
    assert log.read_text().splitlines() == [".", "--tmux", "--name", NAME, "--prompt",
                                            "/self-improve:human-review answer"]


def test_launch_exec_session(env):
    log = fake_launcher(env)
    r = run(env, "launch", "--exec", script=HR)
    assert f"exec: opened {EXEC_NAME} (1 items)" in r.stdout
    assert log.read_text().splitlines()[-1] == "/self-improve:execute-reviewed"


def test_launch_without_launch_command_prints_how(env):
    r = run(env, "launch", script=HR)
    assert "review: 3 item(s) waiting. No launch_command configured" in r.stdout
    assert "/self-improve:human-review" in r.stdout
    r = run(env, "launch", "--exec", script=HR)
    assert "exec: 1 item(s) waiting" in r.stdout and "apply what I approved" in r.stdout


def test_launch_nothing_pending(env):
    log = fake_launcher(env)
    for p in env["out"].glob("*.md"):
        p.unlink()
    assert "review: nothing pending" in run(env, "launch", script=HR).stdout
    assert not log.exists()


def test_launch_already_open(env):
    log = fake_launcher(env)
    env["env"]["HUMAN_REVIEW_AGENTS_JSON"] = f'[{{"kind": "interactive", "name": "{NAME}"}}]'
    assert f"review: {NAME} already open" in run(env, "launch", script=HR).stdout
    assert not log.exists()


def test_launch_dry_run_passes_flag(env):
    log = fake_launcher(env)
    run(env, "launch", "--dry-run", script=HR)
    assert log.read_text().splitlines()[-1] == "--dry-run"


def test_launch_failure_exits_nonzero(env):
    fake_launcher(env)
    bad = env["tmp"] / "bad-launcher"
    bad.write_text("#!/bin/sh\necho nope >&2\nexit 3\n")
    bad.chmod(0o755)
    env["env"]["SELF_IMPROVE_LAUNCH_COMMAND"] = f"{bad} {{name}}"
    r = run(env, "launch", script=HR, ok=False)
    assert r.returncode == 1 and "review: failed to open" in r.stdout


def test_launch_any_open_review_blocks(env):
    log = fake_launcher(env)
    env["env"]["HUMAN_REVIEW_AGENTS_JSON"] = '[{"kind": "interactive", "name": "REVIEW 01-01"}]'
    assert "review: REVIEW 01-01 already open" in run(env, "launch", script=HR).stdout
    assert not log.exists()
