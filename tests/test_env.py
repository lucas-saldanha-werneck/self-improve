"""The test children never see the real shell environment (a failing test must not print secrets)."""
import json, os
import pytest
from conftest import run


@pytest.fixture
def fake_secret(monkeypatch):
    monkeypatch.setenv("FAKE_SECRET_123", "do-not-leak")  # set before the env fixture is built


def test_child_env_has_no_parent_secret(fake_secret, env, tmp_path):
    assert os.environ["FAKE_SECRET_123"] == "do-not-leak"
    assert "FAKE_SECRET_123" not in env["env"]
    dump = tmp_path / "dump.py"
    dump.write_text("import json, os; print(json.dumps(dict(os.environ)))\n", encoding="utf-8")
    child = json.loads(run(env, script=str(dump)).stdout)
    assert "FAKE_SECRET_123" not in child and "do-not-leak" not in json.dumps(child)
    assert child["HOME"] == str(tmp_path / "home")
    assert set(env["env"]) <= {"PATH", "HOME", "LANG", "SYSTEMROOT", "TEMP", "TMP", "CLAUDE_CONFIG_DIR",
                               "SELF_IMPROVE_HOME", "SELF_IMPROVE_OUTPUT_DIR", "SELF_IMPROVE_WIKI_DIR"}
