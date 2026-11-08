"""Shared settings for self-improve (improve_system.py, guard.py, human_review.py). Standard library only.

Where things live:
  CLAUDE_HOME   $CLAUDE_CONFIG_DIR, else ~/.claude (sessions are read from CLAUDE_HOME/projects/*/*.jsonl)
  SI_HOME       $SELF_IMPROVE_HOME, else CLAUDE_HOME/self-improve (run state, history, backups)
  CONFIG_FILE   $SELF_IMPROVE_HOME/config.json when SELF_IMPROVE_HOME is set, else
                $XDG_CONFIG_HOME/self-improve/config.json (default ~/.config/self-improve/config.json).
                If that file is missing but the old CLAUDE_HOME/self-improve/config.json exists, the old one
                is read (and nothing new is created over it); when both exist, the new one wins.
  OUT           review-*.md, needs-context-*.md, change-log.md (option output_dir, default SI_HOME/reviews)

Options come from CONFIG_FILE; an environment variable SELF_IMPROVE_<NAME> wins over the file.
That file lives outside the plugin folder, so your settings survive plugin updates. seed_config() creates
it from config.example.json on first run (every option at its default); an existing file is never touched.
List options read from the environment are separated by os.pathsep (":" on macOS/Linux).
Every option is optional. Without any config the tool works on CLAUDE.md files, skills and memory files.
"""
import json
import os
import re
import shlex

HOME = os.path.expanduser("~")
CLAUDE_HOME = os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude"))
SI_HOME = os.path.expanduser(os.environ.get("SELF_IMPROVE_HOME") or os.path.join(CLAUDE_HOME, "self-improve"))


def _config_files():
    """(file to read, file to create when missing or None). See the module docstring."""
    if (os.environ.get("SELF_IMPROVE_HOME") or "").strip():
        f = os.path.join(SI_HOME, "config.json")
        return f, f
    xdg = os.path.expanduser((os.environ.get("XDG_CONFIG_HOME") or "").strip())
    if not os.path.isabs(xdg):  # unset, or relative (the XDG spec says to ignore that)
        xdg = os.path.join(HOME, ".config")
    new = os.path.join(xdg, "self-improve", "config.json")
    legacy = os.path.join(CLAUDE_HOME, "self-improve", "config.json")
    if not os.path.lexists(new) and os.path.isfile(legacy):
        return legacy, None  # the old place still works; never seed a second file over it
    return new, new


CONFIG_FILE, _SEED_TARGET = _config_files()
# Starter copied to CONFIG_FILE on first run: every option at its default (same behavior as no file).
EXAMPLE_CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "config.example.json")

DEFAULTS = {
    "output_dir": "",          # empty = SI_HOME/reviews
    "wiki_dir": "",            # optional markdown wiki (INDEX.md + log.md + pages). Empty = wiki features off
    "raw_dir": "",             # optional folder of immutable sources the wiki is built from. Never edited
    "wiki_auto_dirs": [],      # sub-folders of the wiki with generated pages: skipped by lint and the wiki analyst
    "skill_roots": [],         # extra skill folders; CLAUDE_HOME/skills is always included
    "project_roots": [],       # folders whose */CLAUDE.md and */*/CLAUDE.md are edit targets
    "extra_protected": [],     # more files/folders the guard protects during a run
    "allow_inline_code": 0,    # 1 = during a run, let `python3 -c`, `node -e` and heredocs into interpreters run
                               # unless their text names a protected path (weaker: built-up paths pass)
    "launch_command": "",      # opens a separate session: "{name}" and "{prompt}" are replaced. Empty = no launch
    "notify_command": "",      # program called with one line of text. Empty = no notifications
    "wait_for_lock": "",       # if this file exists, `start` exits with code 3 (another job is writing)
    "ignore_message_regex": "",  # user messages matching this are not read (e.g. text a hook injects)
    "max_signoff": 10,
    "lock_wait_minutes": 180,  # `wait-lock` gives up after this long (exit 3)
    "pre_run_command": "",     # runs at `start` before anything is read; its output goes into changes.md.
                               # Exit 3 = "busy, wait" (start exits 3, like wait_for_lock)
    "extra_lint_command": "",  # runs during the lint; each output line is a finding (JSON or kind<TAB>target<TAB>detail)
    "language": "",            # language for item titles, questions and the chat summary (e.g. "Portuguese")
    "analyst_notes": "",       # your own instructions for the analysts and the plan: text, or the path of a file
    "claude_command": "",      # the `claude` program human-review asks for open sessions. Empty = claude on PATH
}


def _load_file():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


_FILE = _load_file()


def seed_config():
    """Create CONFIG_FILE from config.example.json if it does not exist yet. Never overwrites or changes
    an existing file (O_EXCL), never raises, prints nothing. True if it created the file.
    Run by the SessionStart hook (seed_config.py) and by improve_system.py, since installing a plugin
    runs no hook."""
    target = _SEED_TARGET
    try:
        # no target: the old config file is in use. Relative: it would land in the project folder.
        if not target or not os.path.isabs(target) or os.path.lexists(target):
            return False
        with open(EXAMPLE_CONFIG, "rb") as f:
            data = f.read()
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    except Exception:
        return False
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return True
    except Exception:
        try:  # only the half-written file this call just created
            os.remove(target)
        except Exception:
            pass
        return False


def opt(name):
    default = DEFAULTS[name]
    env = os.environ.get("SELF_IMPROVE_" + name.upper())
    if env is not None and env.strip():
        if isinstance(default, list):
            return [x for x in env.split(os.pathsep) if x.strip()]
        if isinstance(default, int):
            try:
                v = float(env)
                return int(v) if v.is_integer() else v
            except ValueError:
                return default
        return env.strip()
    v = _FILE.get(name, default)
    if isinstance(default, list) and isinstance(v, str):
        v = [v] if v else []
    if isinstance(default, int) and not isinstance(v, (int, float)):  # "5" or "1.5" in the file
        try:
            v = float(v)
            v = int(v) if v.is_integer() else v
        except (TypeError, ValueError):
            v = default
    return v if v is not None else default


def path_opt(name):
    v = opt(name)
    return os.path.abspath(os.path.expanduser(v)) if v else ""


def path_list(name):
    return [os.path.abspath(os.path.expanduser(x)) for x in opt(name) if x]


OUT = path_opt("output_dir") or os.path.join(SI_HOME, "reviews")
WIKI = path_opt("wiki_dir")
RAW = path_opt("raw_dir")
SESSIONS = os.path.join(CLAUDE_HOME, "projects")
USER_SKILLS = os.path.join(CLAUDE_HOME, "skills")
SKILL_ROOTS = [USER_SKILLS] + [p for p in path_list("skill_roots") if p != USER_SKILLS]
CONFIG_PATHS = [os.path.join(CLAUDE_HOME, n) for n in
                ("CLAUDE.md", "settings.json", "settings.local.json", "hooks", "output-styles", "agents", "commands")]
TRASH = os.path.join(SI_HOME, "trash")
OLD_BACKUPS = os.path.join(SI_HOME, "backups", "old")

# A project's own .claude folder holds config too (skills, hooks, settings). Protected anywhere.
PROJECT_CONFIG_RE = re.compile(r"/\.claude/(skills|hooks|agents|commands|output-styles)(/|$)|"
                               r"/\.claude/(settings(\.local)?\.json|CLAUDE\.md)$", re.I if os.name == "nt" else 0)


WINDOWS = os.name == "nt"
if WINDOWS:  # output is UTF-8 on every OS (Windows pipes default to a legacy code page and fail on "⚑")
    import sys
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def rp(p):
    """Real path with "/" as the separator on every OS (Windows accepts "/"), so one set of rules fits all."""
    r = os.path.realpath(os.path.expanduser(p))
    return r.replace("\\", "/") if WINDOWS else r


def path_key(p):
    """rp() for comparing paths: Windows paths are case-insensitive."""
    r = rp(p)
    return r.lower() if WINDOWS else r


def in_zone(path, roots):
    r = path_key(path)
    return any(r == path_key(z) or r.startswith(path_key(z).rstrip("/") + "/") for z in roots if z)


def read_allowed(path):
    """allowed-paths.txt as a set of path_key() values (empty set if the file is missing)."""
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as f:
        return {path_key(l.strip()) for l in f if l.strip()}


def lock_exclusive(f):
    """Block until this process holds an exclusive lock on the open file f (fcntl, or msvcrt on Windows)."""
    try:
        import fcntl
        fcntl.flock(f, fcntl.LOCK_EX)
    except ImportError:
        import msvcrt
        while True:
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)  # retries for about 10 s, then raises
                return
            except OSError:
                continue


def split_command(cmd):
    """A configured command line as a list. On Windows, backslashes in paths are kept (shlex would eat them)."""
    if not WINDOWS:
        return shlex.split(cmd)
    return [t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'" else t for t in shlex.split(cmd, posix=False)]


def text_or_file(value):
    """An option that holds text, or the path of a file with the text."""
    if not value:
        return ""
    path = os.path.expanduser(value)
    if len(value) < 1024 and "\n" not in value and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""
    return value.strip()


def run_user_command(name, event, extra_env=None, timeout=120):
    """Run the configured command <name> with SELF_IMPROVE_EVENT=<event> and extra_env.
    Returns (exit code, stdout, stderr); (None, "", why) when it is not set or could not start."""
    import subprocess
    cmd = opt(name)
    if not cmd:
        return None, "", ""
    env = dict(os.environ)
    env["SELF_IMPROVE_EVENT"] = event
    env.update({k: str(v) for k, v in (extra_env or {}).items() if v is not None})
    try:
        r = subprocess.run(split_command(cmd), env=env, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
    except Exception as e:
        return None, "", f"{name} could not run: {e}"
    return r.returncode, r.stdout or "", r.stderr or ""


def protected_zones(include_wiki=True):
    # CONFIG_FILE too: it names programs the audited scripts run (pre_run_command, ...)
    z = SKILL_ROOTS + CONFIG_PATHS + [CONFIG_FILE] + path_list("extra_protected")
    if include_wiki and WIKI:
        z.append(WIKI)
    return z


def is_protected(path, include_wiki=True):
    """Skill, config or wiki: only an exact path from allowed-paths.txt may be written during a run."""
    if in_zone(path, protected_zones(include_wiki)):
        return True
    return bool(PROJECT_CONFIG_RE.search(rp(path)))


def path_markers():
    """Text fragments that reveal a protected path inside a command, even when the path can't be resolved."""
    frags = [r"\.claude/skills", r"\.claude/CLAUDE\.md", r"\.claude/settings(\.local)?\.json(?![\w.-])",
             r"\.claude/hooks", r"\.claude/output-styles", r"\.claude/agents", r"\.claude/commands",
             r"self-improve/(active-run|runs/)", r"allowed-paths\.txt"]
    for z in protected_zones():
        parts = [p for p in rp(z).split("/") if p][-2:]
        if len(parts) == 2:
            frags.append(re.escape("/".join(parts)))
    return re.compile("(" + "|".join(frags) + ")", re.I if WINDOWS else 0)
