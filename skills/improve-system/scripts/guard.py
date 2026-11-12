#!/usr/bin/env python3
"""PreToolUse hook (Edit|Write|MultiEdit|NotebookEdit|Bash): the self-improve lock.

Rule: a NEEDS SIGN-OFF item never touches a skill, the wiki or config without your checkbox.
While a run is active (SI_HOME/active-run.json), a write into a protected zone only passes if the
EXACT path is in the run's allowed-paths.txt (AUTO items validated by `render` + items approved with [x]).

Protected zones (see si_config.py): your skill folders, the optional wiki, CLAUDE_HOME/CLAUDE.md,
settings(.local).json, hooks, output-styles, agents, commands, any project's .claude/ config, and the
option extra_protected.

Fails closed:
- applies to EVERY session and never expires; ends with `finish` or `abort`;
- unreadable lock = block;
- a Bash command that mentions a protected zone only passes if it is a read, the run of an audited
  self-improve script, or a write to a released path. Inline interpreters (python -c, node -e, awk,
  perl...), redirects to a variable and paths that can't be resolved = block.
- the command is judged as a whole too: when one part names a protected zone and ANOTHER part writes
  (xargs with a writer, sed -i, perl -i, rm, mv, cp, tee, dd, truncate..., or a variable that can't be
  resolved), the whole command is blocked: `grep -rl x ZONE | xargs sed -i`, `for f in ZONE; do rm "$f"`.
- code typed into a command (python3 -c, node -e, bash -c, heredoc or pipe into an interpreter) is blocked
  during a run even without a protected path in it: it can build the path at run time. Option
  allow_inline_code = 1 switches this off.
Limit: a guardrail, not a sandbox. A script file or an unknown program can still write anywhere.
Outside a run it does nothing.
"""
import json, os, re, shlex, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import si_config as C  # noqa: E402

HOME = C.HOME
STATE = C.SI_HOME
LOCK = os.path.join(STATE, "active-run.json")
SCRIPT = os.path.join(HERE, "improve_system.py")
# the only skill scripts allowed to run during a run (audited; they never write into a zone on their own)
AUDITED = [SCRIPT, os.path.join(os.path.dirname(os.path.dirname(HERE)), "human-review", "scripts", "human_review.py")]
ABORT_MIN_AGE_H = 2  # Claude may only abort a run idle for 2 h+; before that, only the user (in a terminal)
# run control files: only the script writes them
CONTROL = re.compile(r"(active-run\.json|allowed-paths\.txt|approved\.json|render\.json|applied\.jsonl|history\.jsonl|"
                     r"review-hashes\.json)$")
# text that gives away a zone even without a resolvable path (python -c "…/.claude/skills/…")
MARKERS = C.path_markers()
READONLY = {"ls", "cat", "head", "tail", "grep", "egrep", "fgrep", "rg", "wc", "file", "stat", "jq", "diff", "cmp",
            "readlink", "realpath", "du", "tree", "shasum", "md5", "sha256sum", "less", "more", "echo", "printf",
            "test", "[", "basename", "dirname", "pwd", "which", "type", "sort", "uniq", "cut", "tr", "column",
            "true", "false", "sleep", "date", "cd", "zcat", "open", "ps", "whoami", "id", "uname",
            "hostname", "df", "env", "printenv", "lsof", "pgrep"}
# option that makes a "read" program write a file (sort -o, tree -o, less -o, …)
OUT_OPT = {"sort": re.compile(r"^(-o|--output)(=|$)|^-o\S"), "tree": re.compile(r"^-o(=|$|\S)"),
           "less": re.compile(r"^(-o|-O|--log-file|--LOG-FILE)(=|$|\S)"), "more": re.compile(r"^(-o|-O)(=|$|\S)")}
GIT_RO = {"status", "diff", "log", "show", "blame", "ls-files", "rev-parse"}
# programs that write/delete files. Used across segments: `grep -rl x ZONE | xargs sed -i`, `for f in ZONE; do rm "$f"`
WRITERS = {"rm", "rmdir", "unlink", "mv", "cp", "tee", "dd", "truncate", "ln", "install", "rsync", "ditto", "scp",
           "shred", "touch", "chmod", "chown", "patch"}
# options of xargs/parallel that take a value as the next word
XARGS_VAL = {"-I", "-J", "-n", "-P", "-L", "-s", "-d", "-E", "-a", "-R", "-S", "--delimiter", "--arg-file",
             "--max-args", "--max-procs", "--max-lines", "--replace"}
# programs whose destination is one argument (the others are sources): only it is checked
DEST_ONLY = ("cp", "rsync", "install", "ln", "ditto", "scp", "uniq")
INTERP = {"python", "python3", "bash", "sh", "zsh", "node", "ruby", "perl", "osascript"}
PREFIX = {"sudo", "env", "command", "time", "nohup", "exec", "caffeinate"}


rp = C.rp


def in_zone(p):
    r = rp(p)
    if CONTROL.search(r) and C.path_key(r).startswith(C.path_key(STATE) + "/"):
        return True
    return C.is_protected(r)


def allowed(p, allow):
    """allow holds C.path_key() values (C.read_allowed)."""
    r = rp(p)
    if CONTROL.search(r):
        return False  # a control file is never released to the agent
    return C.path_key(r) in allow


def deny(msg):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": msg}}))
    sys.exit(0)


def expand(t):
    return t.replace("${HOME}", HOME).replace("$HOME", HOME)


def resolve(t, cwd):
    t = expand(t)
    return t if t.startswith(("/", "~")) or os.path.isabs(t) else os.path.join(cwd, t)


def runs_start(cmd):
    """True only if some segment RUNS improve_system.py start (text quoted in a prompt does not count)."""
    for seg in re.split(r"&&|\|\||[;|\n]", strip_heredocs(cmd)):
        try:
            toks = shlex.split(seg)
        except ValueError:
            continue
        while toks and (re.match(r"^[A-Za-z_]\w*=", toks[0]) or toks[0] in PREFIX):
            toks = toks[1:]
        if toks and os.path.basename(toks[0]) in INTERP:
            toks = toks[1:]
        if len(toks) >= 2 and toks[0].endswith("improve_system.py") and toks[1] == "start":
            return True
    return False


def strip_heredocs(cmd):
    """Drop heredoc bodies (<<EOF … EOF): they are data for the program, not commands or paths."""
    out, lines, i = [], cmd.split("\n"), 0
    while i < len(lines):
        line = lines[i]
        m = re.search(r"<<-?\s*(['\"]?)(\w+)\1", line)
        out.append(re.sub(r"<<-?\s*(['\"]?)\w+\1", " ", line) if m else line)
        i += 1
        if m:
            end = m.group(2)
            while i < len(lines) and lines[i].strip() != end:
                i += 1
            i += 1
    return "\n".join(out)


def split_segments(cmd):
    """Split on && || ; | newline $( ` outside single quotes. Inside double quotes only $( and ` split
    (the shell still runs them there); | ; and && inside quotes are text (grep 'a\\|b', jq '.x | .y')."""
    out, cur, q, i, n = [], [], None, 0, len(cmd)
    while i < n:
        c = cmd[i]
        if q == "'":
            cur.append(c)
            if c == "'":
                q = None
        elif c == "\\" and i + 1 < n:
            cur.append(cmd[i:i + 2])
            i += 2
            continue
        elif cmd.startswith("$(", i) or c == "`":
            out.append("".join(cur))
            cur = []
            i += 2 if c == "$" else 1
            continue
        elif q == '"':
            cur.append(c)
            if c == '"':
                q = None
        elif c in "'\"":
            q = c
            cur.append(c)
        elif cmd.startswith(("&&", "||"), i):
            out.append("".join(cur))
            cur = []
            i += 2
            continue
        elif c in ";|\n":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    out.append("".join(cur))
    return out


def claude_pid():
    """pid of the `claude` process that owns this session (walks up the tree from the hook)."""
    import subprocess
    pid = os.getppid()
    for _ in range(8):
        try:
            out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)], capture_output=True, text=True,
                                 timeout=2).stdout.strip()
        except Exception:
            return None
        if not out:
            return None
        ppid, comm = out.split(None, 1)
        if os.path.basename(comm).startswith("claude"):
            return pid
        pid = int(ppid)
        if pid <= 1:
            return None
    return None


def pid_alive(pid):
    if os.name == "nt":
        return True  # os.kill(pid, 0) would END the process on Windows; unknown = assume it is alive
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:
        return True  # no permission = it exists


def abort_check(args):
    """abort/--force coming from Claude only works on a run idle for ABORT_MIN_AGE_H h (otherwise only the user)."""
    try:
        with open(LOCK, encoding="utf-8") as f:
            lk = json.load(f)
        age_h = (time.time() - lk.get("started_epoch", 0)) / 3600
    except Exception:
        return "unreadable lock: only the user can unlock it (in a terminal: `python3 … abort RUN_ID --corrupt-lock`)"
    owner = lk.get("owner_pid")
    if owner and not pid_alive(owner):
        return None  # the session that owned the run is gone: unlocking is fine
    if age_h < ABORT_MIN_AGE_H:
        return (f"run active for {age_h:.1f} h: aborting now would switch off the lock to write without [x]. "
                f"Only the user aborts a run younger than {ABORT_MIN_AGE_H} h (in a terminal: `python3 … abort {lk.get('run_id')}`)")
    if "--corrupt-lock" in args:
        return "--corrupt-lock is for the user only"
    return None


INLINE_INTERP = re.compile(r"^(python[\d.]*|pypy[\d.]*|node|nodejs|deno|bun|ruby|perl|php|bash|sh|zsh|dash|ksh|"
                           r"fish|osascript)$")
# per program: the short options that take code from the command line
INLINE_FLAGS = {"python": "c", "pypy": "c", "node": "ep", "nodejs": "ep", "deno": "", "bun": "e", "ruby": "e",
                "perl": "eE", "php": "r", "osascript": "e", "sh": "c"}
INFO_ONLY = {"--version", "-V", "--help", "-h"}


def inline_code(prog, args):
    """'inline code' / 'code from stdin' when an interpreter runs code that is not a script file, else None."""
    if not INLINE_INTERP.match(prog):
        return None
    fam = re.sub(r"[\d.]+$", "", prog)
    fam = fam if fam in INLINE_FLAGS else "sh"
    letters = INLINE_FLAGS[fam]
    for x in args:
        if x in ("--eval", "--print", "--command", "--execute") or x.startswith(("--eval=", "--print=")):
            return "inline code"
        if fam == "deno" and x == "eval":
            return "inline code"
        if re.match(r"^-[A-Za-z]+$", x) and any(ch in x[1:] for ch in letters):
            return "inline code"
    if fam == "python" and "-m" in args:
        return None  # a module (python3 -m json.tool FILE): checked like any other program
    if not [x for x in args if x != "-" and not x.startswith("-")] and not (args and set(args) <= INFO_ONLY):
        return "code from stdin (heredoc or pipe)"
    return None


def uniq_operands(args):
    """Positional operands of uniq (INPUT [OUTPUT]); skips the values of -f/-s/-w."""
    out, skip = [], False
    for a in args:
        if skip:
            skip = False
        elif a in ("-f", "-s", "-w"):
            skip = True
        elif a == "-" or not a.startswith("-"):
            out.append(a)
    return out


def dest_args(prog, args):
    """(destinations, sources) for a program in DEST_ONLY."""
    if prog == "uniq":
        ops = uniq_operands(args)
        return [o for o in ops[1:2] if o != "-"], ops[:1]
    if prog in ("cp", "install", "ln", "mv"):  # -t DIR / --target-directory=DIR (GNU): DIR is the destination
        tgt, rest, i = None, [], 0
        while i < len(args):
            a = args[i]
            if a.startswith("--target-directory="):
                tgt = a.split("=", 1)[1]
            elif a == "--target-directory" or re.match(r"^-[A-Za-z]*t$", a):
                tgt = args[i + 1] if i + 1 < len(args) else ""
                i += 1
            elif re.match(r"^-t.+", a):
                tgt = a[2:]
            elif not a.startswith("-"):
                rest.append(a)
            i += 1
        if tgt is not None:
            return [tgt], rest
    plain = [a for a in args if not a.startswith("-")]
    return plain[-1:], plain[:-1]


def ro_prog(prog, args):
    """True when this program call only reads (the read-only forms the guard lets through)."""
    if prog in READONLY:
        if prog == "uniq":
            return len(uniq_operands(args)) < 2
        return not (prog in OUT_OPT and any(OUT_OPT[prog].match(a) for a in args))
    if prog == "sed":
        return not any(a.startswith("-i") or a == "--in-place" for a in args) and \
            not any(re.search(r"(^|[;{}\s/])[wWe](\s|$)", a) for a in args)
    if prog == "find":
        return not any(a in ("-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf", "-fls")
                       for a in args)
    if prog == "git":
        ga = list(args)
        while ga and ga[0] in ("-C", "-c", "--git-dir", "--work-tree"):
            ga = ga[2:]
        return bool(ga and ga[0] in GIT_RO)
    return False


def seg_writes(prog, args):
    """Why this segment can write files whose names come from ANOTHER segment (stdin, loop variable,
    $(...)), or None. A pipe/loop that names a zone is denied when another segment writes."""
    if prog in ("xargs", "parallel"):
        i = 0
        while i < len(args) and args[i].startswith("-"):
            i += 2 if args[i] in XARGS_VAL else 1
        inner = args[i:]
        while inner and (re.match(r"^[A-Za-z_]\w*=", inner[0]) or inner[0] in PREFIX):
            inner = inner[1:]
        if not inner:
            return None  # bare xargs = echo
        ip = os.path.basename(inner[0])
        return None if ro_prog(ip, inner[1:]) else f"`{prog} {ip}`"
    if prog in WRITERS:
        return f"`{prog}`"
    if prog == "sed" and not ro_prog(prog, args):
        return "`sed -i`"
    if prog == "perl" and any(re.match(r"^-\w*i", a) for a in args):
        return "`perl -i`"
    if not ro_prog(prog, args) and any("$" in a or "`" in a for a in args):
        return f"`{prog}` with an unresolved variable"
    return None


def check_bash(cmd, cwd, allow):
    """Devolve motivo de bloqueio, ou None."""
    # no shortcut: every command is read during a run (variables and symlinks do not escape)
    env = {}
    for m in re.finditer(r"(?:^|[;&|\n\s])([A-Za-z_]\w*)=(\"[^\"]*\"|'[^']*'|[^\s;&|]+)", cmd):
        env[m.group(1)] = m.group(2).strip("'\"")

    def sub(t):
        t = expand(t)
        for _ in range(3):
            t2 = re.sub(r"\$\{?([A-Za-z_]\w*)\}?", lambda m: env.get(m.group(1), os.environ.get(m.group(1), m.group(0))), t)
            if t2 == t:
                break
            t = t2
        return t
    cmd = strip_heredocs(cmd)
    if C.WINDOWS:
        # a backslash is a path separator on Windows: C:\\x\\SKILL.md must be read as a path, not as escapes
        cmd = cmd.replace("\\", "/")
    def names_zone(text, words):
        """True when this segment names a protected zone that is not an exact released path."""
        left = text
        for v in words:
            if v and looks_path(v) and in_zone(resolve(v, cwd)):
                if not allowed(resolve(v, cwd), allow):
                    return True
                left = left.replace(v, "")
        return bool(MARKERS.search(left))

    def looks_path(v):
        return v.startswith(("/", "~")) or "/" in v or os.path.exists(resolve(v, cwd)) or \
               bool(re.search(r"\.\w{1,5}$", v))

    # whole command: a segment that names a zone + ANOTHER segment that writes = the write can get its
    # path from the first one (pipe into xargs, for-loop variable, $(...)). Checked after the loop.
    zone_segs, writer_segs = set(), {}
    for idx, seg in enumerate(split_segments(cmd)):
        seg_s = seg.strip().rstrip(")")
        # loop/condition keywords are not programs: `do python3 x`, `then cp a b`
        seg_s = re.sub(r"^(?:(?:do|then|else|elif|if|while|until|!|\{|\()\s+)+", "", seg_s)
        if re.match(r"^(for|select)\s", seg_s):
            # `for f in a b c` only lists words; the loop body is checked in its own segment
            try:
                words = [sub(t) for t in shlex.split(seg_s, comments=False)]
            except ValueError:
                words = [sub(t) for t in seg_s.split()]
            if names_zone(seg_s, words):
                zone_segs.add(idx)
            continue
        if seg_s in ("done", "fi", "}", "{"):
            continue
        if not seg_s:
            continue
        # redirects deste trecho, resolvidos no cwd atual
        for m in re.finditer(r"(?:^|[^<&0-9])(?:[0-9]|&)?>>?\|?\s*(\"[^\"]*\"|'[^']*'|[^\s;|&<>]+)", seg_s):
            tgt = sub(m.group(1).strip("'\""))
            if tgt.startswith("&") or tgt in ("/dev/null", "/dev/stderr", "/dev/stdout"):
                continue
            if "$" in tgt or "`" in tgt:
                return f"redirect to a path that can't be resolved ({tgt}) in a command that touches a sensitive area"
            p = resolve(tgt, cwd)
            if in_zone(p) and not allowed(p, allow):
                return f"redirect writes to {p}"
        body = re.sub(r"(?:[0-9]|&)?>>?\|?\s*(\"[^\"]*\"|'[^']*'|[^\s;|&<>]+)", " ", seg_s)
        try:
            toks = [sub(t) for t in shlex.split(body, comments=False)]
        except ValueError:
            toks = [sub(t) for t in body.split()]
        while toks and (re.match(r"^[A-Za-z_]\w*=", toks[0]) or toks[0] in PREFIX):
            toks = toks[1:]
        if not toks:
            continue
        prog = os.path.basename(toks[0])
        args = toks[1:]
        if prog == "cd":
            cwd = resolve(args[0].strip("'\""), cwd) if args else HOME
            continue
        # run of an audited self-improve script (reviewed code), including improve_system.py itself.
        # Checked wherever the plugin is installed: abort/--force from Claude only after ABORT_MIN_AGE_H.
        script = None
        if prog in INTERP and args and not args[0].startswith("-"):
            script = resolve(args[0], cwd)
        elif "/" in toks[0] and os.path.isfile(resolve(toks[0], cwd)):
            script = resolve(toks[0], cwd)
        if script and os.path.isfile(script) and C.path_key(script) in {C.path_key(x) for x in AUDITED}:
            sub_cmd = (args[1] if len(args) > 1 else "") if prog in INTERP else (args[0] if args else "")
            if C.path_key(script) == C.path_key(SCRIPT) and (sub_cmd == "abort" or (sub_cmd == "start" and "--force" in args)):
                r = abort_check(args)
                if r:
                    return r
            continue
        # code typed into the command (python -c, node -e, a heredoc or pipe into python3) can build any
        # path at run time (Path.home()/".claude"), so its writes can't be checked from the text: blocked
        inline = None if C.opt("allow_inline_code") else inline_code(prog, args)
        if inline:
            return (f"`{prog}` runs {inline} during a run; its writes can't be checked. Use Read/Grep, "
                    f"Edit/Write on a released file, or wait until the run ends")
        vals = []
        for a in args:
            if "=" in a:  # of=/x, --output=/x, key=/x
                vals.append(a.split("=", 1)[1])
            if not a.startswith("-"):
                vals.append(a)
            elif re.match(r"^-o\S", a):  # -o/x colado
                vals.append(a[2:])
        zone_args = [v for v in vals if v and looks_path(v) and in_zone(resolve(v, cwd))]
        if names_zone(body, vals):
            zone_segs.add(idx)
        w = seg_writes(prog, args)
        if w:
            writer_segs[idx] = w
        if not (MARKERS.search(body) or zone_args or in_zone(cwd)):
            continue
        # plain read (no option that writes a file)
        if ro_prog(prog, args):
            continue
        if script and os.path.isfile(script) and in_zone(script):
            return (f"script inside a protected zone ({script}) does not run during an improve-system run: "
                    f"it could write to a skill/wiki without [x]. Run it after `finish`")
        # any other program: every mention of a zone must be a resolvable AND released path
        leftover = body
        if prog in DEST_ONLY:  # only the destination is written
            dests, srcs = dest_args(prog, args)
            for a in srcs:
                leftover = leftover.replace(a, "")
            zone_args = [a for a in dests if in_zone(resolve(a, cwd))]
        for a in zone_args:
            if not allowed(resolve(a, cwd), allow):
                return f"`{prog}` touches {resolve(a, cwd)}"
            leftover = leftover.replace(a, "")
        if MARKERS.search(leftover):
            return (f"`{prog}` mentions a protected zone in a way that can't be checked "
                    f"(inline interpreter, variable or built-up path)")
        if in_zone(cwd) and not zone_args and prog not in DEST_ONLY:
            return f"`{prog}` runs inside a protected zone ({cwd}) without a released path"
    for idx, w in writer_segs.items():
        if zone_segs - {idx}:
            return (f"{w} in a command whose other part names a protected zone (pipe into xargs, for-loop, "
                    f"$(...)): the write can take its path from there")
    return None


def main():
    raw = sys.stdin.buffer.read().decode("utf-8", "replace")  # Claude Code sends UTF-8 on every OS
    try:
        d0 = json.loads(raw)
        c0 = ((d0.get("tool_input") or {}).get("command") or "") if d0.get("tool_name") == "Bash" else ""
        if runs_start(c0):
            os.makedirs(STATE, exist_ok=True)
            json.dump({"session_id": d0.get("session_id"), "owner_pid": claude_pid(), "epoch": time.time()},
                      open(os.path.join(STATE, "starting-session.json"), "w", encoding="utf-8"))
    except Exception:
        pass
    if not os.path.exists(LOCK):
        return
    try:
        d = json.loads(raw)
    except Exception:
        deny("improve-system run active and the hook input is unreadable: blocked to be safe")
    tool, ti = d.get("tool_name", ""), d.get("tool_input") or {}
    try:
        with open(LOCK, encoding="utf-8") as f:
            lock = json.load(f)
        allow = C.read_allowed(os.path.join(lock["run_dir"], "allowed-paths.txt"))
        run_id = lock["run_id"]
    except Exception as e:
        lock, allow, run_id = None, set(), "?"
        broken = f"lock {LOCK} unreadable ({e})"
    else:
        broken = None
    howto = (f"A NEEDS SIGN-OFF item goes to the review-*.md and is only applied after your [x]. "
             f"If it is a valid AUTO item, put it in plan.json and run `render`. To end the run: "
             f"`python3 {SCRIPT} finish {run_id}` (or `abort {run_id}` if it was abandoned).")
    reason = None
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        p = ti.get("file_path") or ti.get("notebook_path") or ""
        if p and in_zone(p) and not allowed(p, allow):
            reason = f"{p} is a protected zone and is not released"
    elif tool == "Bash":
        reason = check_bash(ti.get("command", ""), d.get("cwd") or os.getcwd(), allow)
    if reason:
        deny(f"improve-system run {run_id} active: {broken + '; ' if broken else ''}{reason}. {howto}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # a crash during a run must not let writes through
        if os.path.exists(LOCK):
            deny(f"improve-system guard error during an active run ({e}): blocked to be safe")
