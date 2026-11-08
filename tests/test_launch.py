from datetime import datetime
from conftest import run, HR, py_command

NAME = f"REVIEW {datetime.now():%d-%m}"
EXEC_NAME = f"EXEC {datetime.now():%d-%m}"


def fake_launcher(env):
    log = env["tmp"] / "launcher.args"
    fake = env["tmp"] / "launcher.py"
    fake.write_text(f"import sys\nopen({str(log)!r}, 'w', encoding='utf-8').write(''.join(a + '\\n' for a in sys.argv[1:]))\n"
                    "print('session opened')\n")
    env["env"].update(SELF_IMPROVE_LAUNCH_COMMAND=f"{py_command(fake)} . --tmux --name {{name}} --prompt {{prompt}}",
                      HUMAN_REVIEW_AGENTS_JSON="[]")
    return log


def test_launch_opens_with_name_and_prompt(env):
    log = fake_launcher(env)
    r = run(env, "launch", script=HR)
    assert f"review: opened {NAME} (3 items)" in r.stdout  # 2 open + 1 approved waiting
    assert log.read_text(encoding="utf-8").splitlines() == [".", "--tmux", "--name", NAME, "--prompt",
                                            "/self-improve:human-review answer"]


def test_launch_exec_session(env):
    log = fake_launcher(env)
    r = run(env, "launch", "--exec", script=HR)
    assert f"exec: opened {EXEC_NAME} (1 items)" in r.stdout
    assert log.read_text(encoding="utf-8").splitlines()[-1] == "/self-improve:execute-reviewed"


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
    assert log.read_text(encoding="utf-8").splitlines()[-1] == "--dry-run"


def test_launch_failure_exits_nonzero(env):
    fake_launcher(env)
    bad = env["tmp"] / "bad-launcher.py"
    bad.write_text("import sys\nsys.stderr.write('nope')\nsys.exit(3)\n", encoding="utf-8")
    env["env"]["SELF_IMPROVE_LAUNCH_COMMAND"] = f"{py_command(bad)} {{name}}"
    r = run(env, "launch", script=HR, ok=False)
    assert r.returncode == 1 and "review: failed to open" in r.stdout


def test_launch_any_open_review_blocks(env):
    log = fake_launcher(env)
    env["env"]["HUMAN_REVIEW_AGENTS_JSON"] = '[{"kind": "interactive", "name": "REVIEW 01-01"}]'
    assert "review: REVIEW 01-01 already open" in run(env, "launch", script=HR).stdout
    assert not log.exists()
