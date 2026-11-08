"""Shared fixture: an isolated Claude home, state folder, review folder and wiki under tmp_path.
Nothing here reads or writes your real ~/.claude."""
import json, os, subprocess, sys, textwrap
import pytest

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS = os.path.join(ROOT, "skills", "improve-system", "scripts")
IS = os.path.join(SCRIPTS, "improve_system.py")
GUARD = os.path.join(SCRIPTS, "guard.py")
HR = os.path.join(ROOT, "skills", "human-review", "scripts", "human_review.py")


def minimal_env(home):
    """The child's whole environment. Never a copy of the real shell: a failing test prints this dict,
    and CI logs must not show real secrets. Tests add only the variables they need."""
    e = {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(home), "LANG": "C.UTF-8"}
    if os.name == "nt":  # Windows needs these to start Python at all
        for k in ("SYSTEMROOT", "TEMP", "TMP"):
            if k in os.environ:
                e[k] = os.environ[k]
    return e


@pytest.fixture
def env(tmp_path):
    claude, state, out, wiki = tmp_path / "claude", tmp_path / "state", tmp_path / "out", tmp_path / "vault" / "wiki"
    for d in (claude / "projects", claude / "skills", state / "runs" / "R0", out, wiki):
        d.mkdir(parents=True)
    target = tmp_path / "target.txt"
    target.write_text("before\n", encoding="utf-8")
    plan = [
        {"id": "S01", "bucket": "signoff", "kind": "skill-edit", "title": "change a", "target": str(target),
         "change": "replace before with after", "evidence": "3x", "risk": "low"},
        {"id": "S02", "bucket": "signoff", "kind": "skill-edit", "title": "change b", "target": str(target)},
        {"id": "C01", "bucket": "context", "kind": "gap", "title": "which source", "question": "Which one is right?",
         "options": ["A", "B"]},
        {"id": "C02", "bucket": "context", "kind": "gap", "title": "on purpose?", "question": "Is it on purpose?"},
    ]
    (state / "runs" / "R0" / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    hist = [{"event": "start", "run_id": "R0", "covered_from": "2026-09-13T00:00:00Z",
             "covered_until": "2026-09-20T00:00:00Z"},
            {"event": "finish", "run_id": "R0", "consumed": []}]
    (state / "history.jsonl").write_text("\n".join(json.dumps(h) for h in hist) + "\n", encoding="utf-8")
    (out / "review-2026-09-20.md").write_text(textwrap.dedent("""\
        # improve-system review, 2026-09-20

        ## New

        - [x] **S01 · change a** <!-- R0/S01 -->
            - Where: `target.txt` (skill-edit)
        - [ ] **S02 · change b** <!-- R0/S02 -->
            - Where: `target.txt` (skill-edit)
        """), encoding="utf-8")
    (out / "needs-context-2026-09-20.md").write_text(textwrap.dedent("""\
        # Context needed, 2026-09-20

        ### C01 · which source <!-- R0/C01 -->

        Which one is right?

        **Answer:** A

        ---

        ### C02 · on purpose? <!-- R0/C02 -->

        Is it on purpose?

        **Answer:**

        ---
        """), encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    e = minimal_env(home)
    e.update(CLAUDE_CONFIG_DIR=str(claude), SELF_IMPROVE_HOME=str(state), SELF_IMPROVE_OUTPUT_DIR=str(out),
             SELF_IMPROVE_WIKI_DIR=str(wiki))
    return {"state": state, "out": out, "target": target, "env": e, "tmp": tmp_path, "claude": claude, "wiki": wiki}


def run(env, *args, script=IS, ok=True, stdin=None):
    r = subprocess.run([sys.executable, script, *args], capture_output=True, text=True, encoding="utf-8",
                       env=env["env"], input=stdin)
    if ok:
        assert r.returncode == 0, r.stdout + r.stderr
    return r


def pyeval(env, code):
    r = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {SCRIPTS!r}); {code}"],
                       capture_output=True, text=True, encoding="utf-8", env=env["env"])
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def py_command(script):
    """A command line that runs a Python script, on every OS (Windows can't run a #!/bin/sh file)."""
    return f'"{sys.executable}" "{script}"'


def last_run_dir(env):
    runs = sorted(p for p in (env["state"] / "runs").iterdir() if p.name not in ("R0", "R1"))
    return runs[-1]
