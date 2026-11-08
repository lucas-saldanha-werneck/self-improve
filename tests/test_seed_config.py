"""The plugin creates ~/.config/self-improve/config.json by itself on first run (installing a plugin runs no
hook): from the SessionStart hook and from improve_system.py. Never overwrites, never blocks a session.
The old ~/.claude/self-improve/config.json is still read when the new file is missing."""
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
    return {"env": e, "tmp": tmp_path, "cfg": h / ".config" / "self-improve" / "config.json",
            "legacy": tmp_path / "claude" / "self-improve" / "config.json"}


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
    assert read(home["tmp"] / "si" / "config.json") == read(EXAMPLE), "SELF_IMPROVE_HOME still holds the config"
    assert not home["cfg"].exists()
    e = dict(home["env"], XDG_CONFIG_HOME=str(home["tmp"] / "xdg"))
    seed(e)
    assert read(home["tmp"] / "xdg" / "self-improve" / "config.json") == read(EXAMPLE)
    assert not home["cfg"].exists()
    e = dict(home["env"], XDG_CONFIG_HOME="rel-xdg")  # relative: ignored, as the XDG spec says
    seed(e)
    assert read(home["cfg"]) == read(EXAMPLE)
    assert not home["legacy"].exists() and not (home["tmp"] / "home" / ".claude").exists(), "nothing in the old place"


def opts_of(env):
    return dump(env)["opts"]


def test_old_file_only_is_read_and_nothing_new_is_seeded(home):
    home["legacy"].parent.mkdir(parents=True)
    home["legacy"].write_text('{"language": "Portuguese", "max_signoff": 4}', encoding="utf-8")
    before = (read(home["legacy"]), os.stat(home["legacy"]).st_mtime_ns)
    seed(home["env"])
    subprocess.run([sys.executable, IS, "status"], capture_output=True, env=home["env"])
    assert not home["cfg"].exists(), "no new file seeded over the old one"
    assert (read(home["legacy"]), os.stat(home["legacy"]).st_mtime_ns) == before, "old file left alone"
    o = opts_of(home["env"])
    assert (o["language"], o["max_signoff"]) == ("Portuguese", 4)
    # CLAUDE_CONFIG_DIR unset: the old place is ~/.claude/self-improve
    e = dict(home["env"])
    e.pop("CLAUDE_CONFIG_DIR")
    old = home["tmp"] / "home" / ".claude" / "self-improve" / "config.json"
    old.parent.mkdir(parents=True)
    old.write_text('{"language": "German"}', encoding="utf-8")
    seed(e)
    assert not home["cfg"].exists()
    assert opts_of(e)["language"] == "German"


def test_new_file_only_is_read(home):
    home["cfg"].parent.mkdir(parents=True)
    home["cfg"].write_text('{"language": "French"}', encoding="utf-8")
    assert opts_of(home["env"])["language"] == "French"
    assert not home["legacy"].exists()


def test_both_files_the_new_one_wins(home):
    for f, lang in ((home["legacy"], "Portuguese"), (home["cfg"], "French")):
        f.parent.mkdir(parents=True)
        f.write_text(json.dumps({"language": lang}), encoding="utf-8")
    seed(home["env"])
    assert opts_of(home["env"])["language"] == "French"
    assert json.loads(read(home["legacy"]))["language"] == "Portuguese", "old file untouched"


def test_neither_file_seeds_the_new_place(home):
    seed(home["env"])
    assert read(home["cfg"]) == read(EXAMPLE)
    assert not home["legacy"].exists()


def test_config_file_is_protected_during_a_run(home):
    zones = dump(home["env"])["zones"]
    assert str(home["cfg"]) in zones, "it names programs the audited scripts run"


def test_relative_home_is_not_seeded_into_the_project(home):
    proj = home["tmp"] / "project"
    proj.mkdir()
    r = subprocess.run([sys.executable, SEED], capture_output=True, text=True, cwd=str(proj),
                       env=dict(home["env"], SELF_IMPROVE_HOME="rel-state"))
    assert r.returncode == 0
    assert os.listdir(proj) == []


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


def test_number_options_from_the_file(home):
    home["cfg"].parent.mkdir(parents=True)
    home["cfg"].write_text('{"lock_wait_minutes": "1.5", "max_signoff": "4"}', encoding="utf-8")
    o = opts_of(home["env"])
    assert (o["lock_wait_minutes"], o["max_signoff"]) == (1.5, 4)
    home["cfg"].write_text('{"lock_wait_minutes": 0}', encoding="utf-8")
    assert opts_of(home["env"])["lock_wait_minutes"] == 0, "0 = don't wait, not the 180 default"
    home["cfg"].write_text('{"lock_wait_minutes": "soon"}', encoding="utf-8")
    assert opts_of(home["env"])["lock_wait_minutes"] == 180


def test_seeded_file_behaves_like_no_file(home):
    without = dump(home["env"])
    assert not home["cfg"].exists()
    seed(home["env"])
    assert read(home["cfg"]) == read(EXAMPLE)
    assert dump(home["env"]) == without
    # environment variables still win over the seeded defaults
    assert dump(dict(home["env"], SELF_IMPROVE_LANGUAGE="German", SELF_IMPROVE_MAX_SIGNOFF="3"))["opts"] \
        == dict(without["opts"], language="German", max_signoff=3)
