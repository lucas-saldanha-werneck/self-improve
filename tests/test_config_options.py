"""Options that let a user plug personal behavior in without editing the plugin: pre_run_command,
wait-lock, extra_lint_command, language, analyst_notes, claude_command, and the config file itself."""
import json
from conftest import run, pyeval, last_run_dir, py_command, HR
from test_start import plain_env

# A command a user could plug in: records the self-improve variables it got, prints the text in
# HOOK_OUT_FILE and exits with HOOK_EXIT.
SCRIPT = """import json, os, sys
names = ("SELF_IMPROVE_EVENT", "SELF_IMPROVE_MODE", "SELF_IMPROVE_SINCE", "SELF_IMPROVE_UNTIL",
         "SELF_IMPROVE_WIKI_DIR", "SELF_IMPROVE_RAW_DIR")
with open(os.environ["HOOK_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({k: os.environ[k] for k in names if k in os.environ}) + "\\n")
out = os.environ.get("HOOK_OUT_FILE")
if out and os.path.exists(out):
    sys.stdout.write(open(out, encoding="utf-8").read())
sys.stderr.write(os.environ.get("HOOK_ERR", ""))
sys.exit(int(os.environ.get("HOOK_EXIT", "0")))
"""


def hook(env, option, output="", exit_code=0):
    script = env["tmp"] / "user hook.py"  # a space in the path on purpose
    script.write_text(SCRIPT, encoding="utf-8")
    out = env["tmp"] / f"{option}.out"
    out.write_text(output, encoding="utf-8")
    log = env["tmp"] / "hook.log"
    env["env"].update({f"SELF_IMPROVE_{option.upper()}": py_command(script), "HOOK_LOG": str(log),
                       "HOOK_OUT_FILE": str(out), "HOOK_EXIT": str(exit_code)})
    return log


def calls(log):
    return [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


def test_config_file_outside_the_plugin_is_read_and_env_wins(env):
    (env["state"] / "config.json").write_text(json.dumps({"language": "Portuguese", "max_signoff": 4}),
                                              encoding="utf-8")
    assert pyeval(env, "import si_config as C; print(C.opt('language'), C.opt('max_signoff'))") == "Portuguese 4"
    env["env"]["SELF_IMPROVE_LANGUAGE"] = "German"
    assert pyeval(env, "import si_config as C; print(C.opt('language'))") == "German"


def test_pre_run_output_goes_into_changes_and_gets_the_window(env):
    plain_env(env)
    log = hook(env, "pre_run_command", "- 2026-11-01 sync-x: ok (12 new)\n")
    meta = json.loads(run(env, "start").stdout)
    rd = last_run_dir(env)
    changes = (rd / "changes.md").read_text(encoding="utf-8")
    assert "## From your pre_run_command" in changes and "sync-x: ok (12 new)" in changes
    assert (rd / "pre-run.md").exists() and meta["pre_run"] is None
    [c] = calls(log)
    assert c["SELF_IMPROVE_EVENT"] == "pre_run" and c["SELF_IMPROVE_MODE"] == "full"
    assert c["SELF_IMPROVE_SINCE"] == meta["covered_from"] and c["SELF_IMPROVE_UNTIL"] == meta["covered_until"]


def test_pre_run_busy_exits_3_and_other_failures_only_warn(env):
    plain_env(env)
    hook(env, "pre_run_command", exit_code=3)
    env["env"]["HOOK_ERR"] = "ingestion running"
    r = run(env, "start", ok=False)
    assert r.returncode == 3 and "ingestion running" in r.stderr
    assert not (env["state"] / "active-run.json").exists(), "no run opened"
    env["env"]["HOOK_EXIT"] = "1"
    meta = json.loads(run(env, "start").stdout)
    assert meta["pre_run"].startswith("exit 1")


def test_pre_run_in_apply_only_mode(env):
    log = hook(env, "pre_run_command", "context line\n")
    meta = json.loads(run(env, "start", "--apply-only").stdout)
    assert meta["apply_only"] is True
    assert calls(log)[0]["SELF_IMPROVE_MODE"] == "apply-only"


def test_wait_lock_returns_when_free_and_gives_up_when_busy(env):
    lock = env["tmp"] / "other.lock"
    env["env"]["SELF_IMPROVE_WAIT_FOR_LOCK"] = str(lock)
    assert "free" in run(env, "wait-lock", "--max-minutes", "0").stdout
    lock.write_text("{}", encoding="utf-8")
    r = run(env, "wait-lock", "--max-minutes", "0.01", "--every", "0.2", ok=False)
    assert r.returncode == 3 and "other.lock exists" in r.stderr


def test_wait_lock_asks_pre_run_in_check_mode(env):
    log = hook(env, "pre_run_command", exit_code=3)
    r = run(env, "wait-lock", "--max-minutes", "0", ok=False)
    assert r.returncode == 3 and "busy" in r.stderr
    assert calls(log)[-1]["SELF_IMPROVE_MODE"] == "check"
    env["env"]["HOOK_EXIT"] = "0"
    assert run(env, "wait-lock", "--max-minutes", "0").returncode == 0


def test_extra_lint_findings_json_and_tab_lines(env):
    lines = [json.dumps({"kind": "contact-stale", "target": "/w/contacts/a.md", "detail": "no source in 90 days"}),
             json.dumps({"kind": "broken-link", "target": "/w/b.md", "detail": "x", "auto_ok": True}),
             "vault-orphan\t/w/c.md\tno inbound link",
             "not a finding", ""]
    log = hook(env, "extra_lint_command", "\n".join(lines) + "\n")
    out = json.loads(pyeval(env, "import json, improve_system as m; print(json.dumps(m.lint({})))"))
    got = {(i["kind"], i["target"], i["detail"], i["auto_ok"]) for i in out}
    assert ("contact-stale", "/w/contacts/a.md", "no source in 90 days", False) in got
    assert ("broken-link", "/w/b.md", "x", True) in got
    assert ("vault-orphan", "/w/c.md", "no inbound link", False) in got
    assert not any(i["detail"] == "not a finding" for i in out)
    [c] = calls(log)
    assert c["SELF_IMPROVE_EVENT"] == "lint" and c["SELF_IMPROVE_WIKI_DIR"] == str(env["wiki"])


def test_extra_lint_failure_is_a_finding_not_a_crash(env):
    hook(env, "extra_lint_command", "kind\t/x\tdetail\n", exit_code=2)
    out = json.loads(pyeval(env, "import json, improve_system as m; print(json.dumps(m.lint({})))"))
    kinds = [i["kind"] for i in out]
    assert "extra-lint-failed" in kinds and "kind" not in kinds


def test_language_and_analyst_notes_file_reach_the_run(env):
    plain_env(env)
    notes = env["tmp"] / "notes.md"
    notes.write_text("Pages under contacts/ are generated: never call them orphans.\n", encoding="utf-8")
    env["env"].update(SELF_IMPROVE_LANGUAGE="Portuguese", SELF_IMPROVE_ANALYST_NOTES=str(notes))
    meta = json.loads(run(env, "start").stdout)
    assert meta["language"] == "Portuguese"
    text = open(meta["user_notes"], encoding="utf-8").read()
    assert "in Portuguese" in text and "never call them orphans" in text


def test_no_notes_without_options(env):
    plain_env(env)
    meta = json.loads(run(env, "start").stdout)
    assert meta["user_notes"] is None and meta["language"] is None


def test_claude_command_is_used_to_find_open_sessions(env):
    fake = env["tmp"] / "claude-stable.py"
    fake.write_text('import json\nprint(json.dumps([{"kind": "interactive", "name": "REVIEW 01-01"}]))\n',
                    encoding="utf-8")
    launcher = env["tmp"] / "launcher.py"
    launcher.write_text("print('opened')\n", encoding="utf-8")
    env["env"].update(SELF_IMPROVE_CLAUDE_COMMAND=py_command(fake),
                      SELF_IMPROVE_LAUNCH_COMMAND=f"{py_command(launcher)} {{name}} {{prompt}}")
    assert "REVIEW 01-01 already open" in run(env, "launch", script=HR).stdout


def test_force_with_a_busy_pre_run_keeps_the_active_run(env):
    plain_env(env)
    first = json.loads(run(env, "start").stdout)
    lock = env["state"] / "active-run.json"
    before = lock.read_text(encoding="utf-8")
    hook(env, "pre_run_command", exit_code=3)
    r = run(env, "start", "--force", ok=False)
    assert r.returncode == 3
    assert lock.exists() and lock.read_text(encoding="utf-8") == before, f"run {first['run_id']} was torn down"
    env["env"]["HOOK_EXIT"] = "0"
    second = json.loads(run(env, "start", "--force").stdout)
    assert second["run_id"] != first["run_id"]


def test_wait_lock_check_gets_the_same_variables_as_a_run(env):
    log = hook(env, "pre_run_command")
    run(env, "wait-lock", "--max-minutes", "0")
    c = calls(log)[-1]
    assert c["SELF_IMPROVE_MODE"] == "check"
    assert {"SELF_IMPROVE_WIKI_DIR", "SELF_IMPROVE_RAW_DIR", "SELF_IMPROVE_SINCE", "SELF_IMPROVE_UNTIL"} <= set(c)
    assert c["SELF_IMPROVE_WIKI_DIR"] == str(env["wiki"])
