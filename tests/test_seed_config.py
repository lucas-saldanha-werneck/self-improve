"""The plugin creates SI_HOME/config.json by itself on first run (installing a plugin runs no hook):
from the SessionStart hook and from improve_system.py. Never overwrites, never blocks a session."""
import json, os, subprocess, sys

import pytest
from conftest import IS, ROOT, SCRIPTS, minimal_env, run

SEED = os.path.join(SCRIPTS, "seed_config.py")
EXAMPLE = os.path.join(ROOT, "config.example.json")
DUMP = (f"import json, sys; sys.path.insert(0, {SCRIPTS!r}); import si_config as C; "
        "print(json.dumps({'opts': {n: C.opt(n) for n in C.DEFAULTS}, 'out': C.OUT, 'wiki': C.WIKI, 'raw': C.RAW, "
        "'skills': C.SKILL_ROOTS, 'zones': C.protected_zones()}, sort_keys=True))")


def read(p):
    with open(p, "rb") as f:
        return f.read()


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    e = minimal_env(h)
    e["USERPROFILE"] = str(h)  # Windows: ~ is USERPROFILE
    e["CLAUDE_CONFIG_DIR"] = str(tmp_path / "claude")
    return {"env": e, "tmp": tmp_path, "cfg": tmp_path / "claude" / "self-improve" / "config.json"}


def seed(env):
    return subprocess.run([sys.executable, SEED], capture_output=True, text=True, env=env, input="{}")


def dump(env):
    r = subprocess.run([sys.executable, "-c", DUMP], capture_output=True, text=True, encoding="utf-8", env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_example_lists_every_option_at_its_default():
    sys.path.insert(0, SCRIPTS)
    import si_config as C
    with open(EXAMPLE, encoding="utf-8") as f:
        example = json.load(f)
    assert {k: v for k, v in example.items() if not k.startswith("_")} == C.DEFAULTS
    assert set(example["_doc"]) - {"about"} == set(C.DEFAULTS), "a short doc line per option"


def test_hook_is_registered_on_session_start():
    with open(os.path.join(ROOT, "hooks", "hooks.json"), encoding="utf-8") as f:
        hooks = json.load(f)["hooks"]["SessionStart"]
    assert any("seed_config.py" in " ".join(h["args"]) for g in hooks for h in g["hooks"])


def test_missing_file_is_created_with_defaults(home):
    r = seed(home["env"])
    assert (r.returncode, r.stdout, r.stderr) == (0, "", ""), "silent"
    assert read(home["cfg"]) == read(EXAMPLE)


def test_first_use_of_the_command_creates_it_too(home):
    r = subprocess.run([sys.executable, IS, "status"], capture_output=True, text=True, env=home["env"])
    assert r.returncode == 0, r.stderr
    assert read(home["cfg"]) == read(EXAMPLE)


def test_existing_file_is_never_touched(home):
    home["cfg"].parent.mkdir(parents=True)
    for body in (b'{"language": "Portuguese"}', b"{not json", b""):
        home["cfg"].write_bytes(body)
        before = os.stat(home["cfg"]).st_mtime_ns
        seed(home["env"])
        subprocess.run([sys.executable, IS, "status"], capture_output=True, env=home["env"])
        assert read(home["cfg"]) == body
        assert os.stat(home["cfg"]).st_mtime_ns == before


def test_env_overrides_are_respected(home):
    e = dict(home["env"], SELF_IMPROVE_HOME=str(home["tmp"] / "si"))
    seed(e)
    assert read(home["tmp"] / "si" / "config.json") == read(EXAMPLE)
    assert not home["cfg"].exists()
    e = dict(home["env"])
    e.pop("CLAUDE_CONFIG_DIR")
    seed(e)
    assert read(home["tmp"] / "home" / ".claude" / "self-improve" / "config.json") == read(EXAMPLE)


def test_unwritable_place_never_crashes(home):
    blocker = home["tmp"] / "a-file"
    blocker.write_text("x")
    e = dict(home["env"], SELF_IMPROVE_HOME=str(blocker / "sub"))  # parent is a file
    r = seed(e)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
    if os.name != "nt" and os.geteuid() != 0:  # read-only folder (root and Windows ignore the mode)
        ro = home["tmp"] / "ro"
        ro.mkdir()
        os.chmod(ro, 0o500)
        try:
            r = seed(dict(home["env"], SELF_IMPROVE_HOME=str(ro)))
            assert (r.returncode, r.stderr) == (0, "")
            assert os.listdir(ro) == []
        finally:
            os.chmod(ro, 0o700)


def test_broken_install_never_crashes(home, tmp_path):
    lone = tmp_path / "lone"
    lone.mkdir()
    (lone / "seed_config.py").write_bytes(read(SEED))  # no si_config.py next to it
    r = subprocess.run([sys.executable, str(lone / "seed_config.py")], capture_output=True, text=True, env=home["env"])
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")


def test_seeded_file_behaves_like_no_file(home):
    without = dump(home["env"])
    assert not home["cfg"].exists()
    seed(home["env"])
    assert read(home["cfg"]) == read(EXAMPLE)
    assert dump(home["env"]) == without
    # environment variables still win over the seeded defaults
    assert dump(dict(home["env"], SELF_IMPROVE_LANGUAGE="German", SELF_IMPROVE_MAX_SIGNOFF="3"))["opts"] \
        == dict(without["opts"], language="German", max_signoff=3)
