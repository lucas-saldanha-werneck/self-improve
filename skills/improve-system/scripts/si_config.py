"""Shared settings for self-improve (improve_system.py, guard.py, human_review.py). Standard library only.

Where things live:
  CLAUDE_HOME   $CLAUDE_CONFIG_DIR, else ~/.claude (sessions are read from CLAUDE_HOME/projects/*/*.jsonl)
  SI_HOME       $SELF_IMPROVE_HOME, else CLAUDE_HOME/self-improve (run state, history, backups, config.json)
  OUT           review-*.md, needs-context-*.md, change-log.md (option output_dir, default SI_HOME/reviews)

Options come from SI_HOME/config.json; an environment variable SELF_IMPROVE_<NAME> wins over the file.
List options read from the environment are separated by os.pathsep (":" on macOS/Linux).
Every option is optional. Without any config the tool works on CLAUDE.md files, skills and memory files.
"""
import json
import os
import re

HOME = os.path.expanduser("~")
CLAUDE_HOME = os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude"))
SI_HOME = os.path.expanduser(os.environ.get("SELF_IMPROVE_HOME") or os.path.join(CLAUDE_HOME, "self-improve"))
CONFIG_FILE = os.path.join(SI_HOME, "config.json")

DEFAULTS = {
    "output_dir": "",          # empty = SI_HOME/reviews
    "wiki_dir": "",            # optional markdown wiki (INDEX.md + log.md + pages). Empty = wiki features off
    "raw_dir": "",             # optional folder of immutable sources the wiki is built from. Never edited
    "wiki_auto_dirs": [],      # sub-folders of the wiki with generated pages: skipped by lint and the wiki analyst
    "skill_roots": [],         # extra skill folders; CLAUDE_HOME/skills is always included
    "project_roots": [],       # folders whose */CLAUDE.md and */*/CLAUDE.md are edit targets
    "extra_protected": [],     # more files/folders the guard protects during a run
    "launch_command": "",      # opens a separate session: "{name}" and "{prompt}" are replaced. Empty = no launch
    "notify_command": "",      # program called with one line of text. Empty = no notifications
    "wait_for_lock": "",       # if this file exists, `start` exits with code 3 (another job is writing)
    "ignore_message_regex": "",  # user messages matching this are not read (e.g. text a hook injects)
    "max_signoff": 10,
}


def _load_file():
    try:
        with open(CONFIG_FILE) as f:
            v = json.load(f)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


_FILE = _load_file()


def opt(name):
    default = DEFAULTS[name]
    env = os.environ.get("SELF_IMPROVE_" + name.upper())
    if env is not None and env.strip():
        if isinstance(default, list):
            return [x for x in env.split(os.pathsep) if x.strip()]
        if isinstance(default, int):
            try:
                return int(env)
            except ValueError:
                return default
        return env.strip()
    v = _FILE.get(name, default)
    if isinstance(default, list) and isinstance(v, str):
        v = [v] if v else []
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
                               r"/\.claude/(settings(\.local)?\.json|CLAUDE\.md)$")


def rp(p):
    return os.path.realpath(os.path.expanduser(p))


def in_zone(path, roots):
    r = rp(path)
    return any(r == rp(z) or r.startswith(rp(z).rstrip("/") + "/") for z in roots if z)


def protected_zones(include_wiki=True):
    z = SKILL_ROOTS + CONFIG_PATHS + path_list("extra_protected")
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
    return re.compile("(" + "|".join(frags) + ")")
