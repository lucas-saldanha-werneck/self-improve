#!/usr/bin/env python3
"""improve-system: find improvements to your Claude Code setup (sessions, skills, config, optional wiki)
and sort each one into 3 buckets: AUTO (applied now), SIGN-OFF (needs your [x]), CONTEXT (needs an answer).

Subcommands:
  status                         history + active run + reviews waiting for you
  start  [--since ISO] [--days N] [--full-wiki] [--apply-only] [--force]
         opens a run (window = last FINISHED run -> now). Reads the previous reviews ([x] = approved),
         gathers session data, runs the lint and writes the allowlist the guard reads.
         --apply-only: only apply the [x] items (execute-reviewed). Does not read sessions or the wiki,
         does not move the window, does not consume needs-context files.
  render RUN_ID                  validates <run_dir>/plan.json, writes review-*.md + needs-context-*.md
  backup RUN_ID PATH             copy of an allowed target before editing it
  intent RUN_ID --id ID --target PATH
                                 BEFORE a manual edit: records the target's hash (no double apply)
  apply-patch RUN_ID --id ID     applies the item's exact {old, new} patch (backup + log)
  log    RUN_ID --id ID --target PATH --summary TXT [--backup PATH]
                                 1 line in change-log.md per applied change
  finish RUN_ID [--note TXT] [--allow-missing] [--no-review]
                                 closes the run, marks reviews as read, releases the lock and
                                 launches the human-review session if something waits for you
  abort  RUN_ID [--note TXT]     abandoned run: releases the lock, window and reviews stay as they were
  notify TEXT                    sends one line through notify_command (no-op when not configured)
  wait-lock [--max-minutes N] [--every S]
                                 blocks until `start` may run: the wait_for_lock file is gone and
                                 pre_run_command no longer answers 3 (busy). Exit 0 = go, 3 = gave up

State lives outside the plugin (see si_config.py): SI_HOME/history.jsonl, SI_HOME/runs/<RUN_ID>/,
SI_HOME/active-run.json (the lock guard.py reads). Your files: OUT/review-*, needs-context-*, change-log.md.
Only a run closed with `finish` moves the window. An aborted run leaves the next window unchanged.
"""
import argparse, glob, json, os, re, subprocess, sys, time, unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import si_config as C  # noqa: E402

HOME = C.HOME
STATE = C.SI_HOME
OUT = C.OUT
WIKI = C.WIKI
RAW = C.RAW
SESSIONS = C.SESSIONS
SKILL_ROOTS = C.SKILL_ROOTS
HISTORY = os.path.join(STATE, "history.jsonl")
LOCK = os.path.join(STATE, "active-run.json")
WAIT_LOCK = C.path_opt("wait_for_lock")
STARTING = os.path.join(STATE, "starting-session.json")  # written by guard.py when it sees `start`
CHANGELOG = os.path.join(OUT, "change-log.md")
CHUNK_BYTES = 110_000
SELF_MARKERS = ("improve-system", "improve_system", "self-improve", "human-review", "execute-reviewed")
_ign = C.opt("ignore_message_regex")
try:
    IGNORE_RE = re.compile(_ign) if _ign else None
except re.error:
    IGNORE_RE = None
MAX_SIGNOFF = int(C.opt("max_signoff") or 10)
in_zone = C.in_zone
rp = C.rp

# Kinds that may go to AUTO. Any other kind marked auto = render error.
AUTO_KINDS = {
    "broken-link": "wiki link [[x]] or (x.md) to a page that does not exist -> point it to the right page",
    "index-entry": "page exists but is missing from INDEX.md (or INDEX points to a deleted page)",
    "log-entry": "wiki change without a line in log.md",
    "relative-date": "'yesterday', 'last week' -> absolute date taken from the source",
    "typo": "spelling/formatting error that does not change meaning",
    "stale-flag": "mark a passage '(check: reason, date)' without rewriting it",
    "conflicted-copy": "sync 'conflicted copy' file identical to the original (cmp) -> move to the trash folder",
    "backup-cleanup": "*.bak-* older than 14 days -> move to the old-backups folder",
    "memory-index": "MEMORY.md line points to a missing file, or a memory file has no line",
}
WIKI_KINDS = ("broken-link", "index-entry", "log-entry", "relative-date", "typo", "stale-flag")
AUTO_ROOTS_DOC = {k: "the wiki folder" for k in WIKI_KINDS}
AUTO_ROOTS_DOC.update({"conflicted-copy": "a 'conflicted copy' file or the trash folder",
                       "backup-cleanup": "a *.bak-* file or the old-backups folder",
                       "memory-index": "<claude home>/projects/*/memory/MEMORY.md"})


def auto_target_ok(kind, t):
    """Each AUTO kind may only touch its own place."""
    if kind in WIKI_KINDS:
        return bool(WIKI) and in_zone(t, [WIKI]) and t.endswith(".md")
    if kind == "conflicted-copy":
        return "conflicted copy" in os.path.basename(t) or in_zone(t, [C.TRASH])
    if kind == "backup-cleanup":
        return ".bak-" in os.path.basename(t) or in_zone(t, [C.OLD_BACKUPS])
    if kind == "memory-index":
        return os.path.basename(t) == "MEMORY.md" and in_zone(t, [SESSIONS])
    return False


BUCKETS = ("auto", "signoff", "context")
PREFIX = {"auto": "A", "signoff": "S", "context": "C"}

# Hints only (the analyst confirms in the text). English first, then Portuguese.
CORRECTION_RE = re.compile(
    r"\b(i said|i told you|i asked|again|wrong|don'?t|do not|stop|not what|you forgot|you ignored|why did you|"
    r"that'?s not|never|always|already (said|told|asked)|did you (check|verify)|(tag|mark) (for|to) improve|"
    r"nao e isso|nao era isso|errad\w*|de novo|outra vez|ja (te )?(disse|falei|pedi|expliquei)|"
    r"eu (disse|falei|pedi)|pedi (pra|para)|nao (faz|faca|precisa|quero|era|foi o que)|"
    r"por que (voce|vc)|voce (esqueceu|ignorou|nao)|vc (esqueceu|ignorou|nao)|para de|pare|"
    r"nunca|sempre|(tag|marca) (para|pra|pro) (o )?improve|ta errado|esta errado|confer\w*|verificou|cade|como assim)\b"
)
RELATIVE_DATE_RE = re.compile(r"\b(yesterday|the day before yesterday|last week|last month|next week|"
                              r"ontem|anteontem|semana passada|mês passado|semana que vem)\b", re.I)


# ---------- util ----------
def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(s):
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def norm(s):
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def read_history():
    if not os.path.exists(HISTORY):
        return []
    return [json.loads(l) for l in open(HISTORY, encoding="utf-8") if l.strip()]


def append_history(ev):
    os.makedirs(STATE, exist_ok=True)
    with open(HISTORY, "a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def last_finished(hist):
    fin = {e["run_id"] for e in hist if e["event"] == "finish"}
    starts = [e for e in hist if e["event"] == "start" and e["run_id"] in fin and not e.get("apply_only")]
    return starts[-1] if starts else None


def run_dir(run_id):
    return os.path.join(STATE, "runs", run_id)


def jdump(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def jload(path, default):
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else default


def short(p):
    return p.replace(HOME, "~")


# ---------- sessions ----------
TAG_RE = re.compile(r"<(system-reminder|local-command-caveat|local-command-stdout|bash-stdout|bash-stderr|"
                    r"context_window_protection|task-notification)\b.*?</\1>", re.S)
CMD_RE = re.compile(r"<command-name>/?([^<]+)</command-name>")
SKILL_PATH_RE = re.compile(r"/skills/([\w-]+)/")
BUILTIN_CMDS = {"clear", "model", "compact", "resume", "config", "context", "cost", "status", "help", "exit",
                "login", "logout", "mcp", "memory", "permissions", "plugin", "plugins", "doctor", "init", "fast",
                "output-style", "rewind", "agents", "hooks", "ide", "remote-control", "usage", "effort"}


def track_skill(raw, current):
    m = CMD_RE.search(raw)
    if m and m.group(1).strip() not in BUILTIN_CMDS:
        return m.group(1).strip()
    return current


def clean_text(t):
    t = TAG_RE.sub("", t)
    m = CMD_RE.search(t)
    if m:
        args = re.search(r"<command-args>(.*?)</command-args>", t, re.S)
        args = args.group(1).strip() if args else ""
        rest = re.sub(r"<command-(name|message|args)>.*?</command-\1>", "", t, flags=re.S).strip()
        cmd = f"/{m.group(1).strip()} {args}".strip() if args else ""  # a bare command carries no intent
        return f"{cmd}\n{rest}".strip() if rest else cmd
    return t.strip()


def content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def session_files(since):
    # first level only: subagent transcripts live in <session>/subagents/ and are not the user's conversation
    for f in glob.glob(os.path.join(SESSIONS, "*", "*.jsonl")):
        proj = os.path.basename(os.path.dirname(f))
        if "scratchpad" in proj or "-private-tmp-" in proj or proj.startswith("-tmp-"):
            continue  # throwaway/test sessions
        if os.path.getmtime(f) < since.timestamp():
            continue
        yield f


def extract(f, since, until, stats):
    """User events in one session + friction per skill (uses, tool errors, Esc, rejections)."""
    events, last_asst, active_skill, tool_names = [], "", "", {}
    after_interrupt, since_skill = False, 0
    SKILL_TTL = 15  # after 15 user messages, friction is no longer blamed on a skill called long ago
    sid = os.path.basename(f)[:-6]
    for line in open(f, errors="ignore", encoding="utf-8"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        typ = d.get("type")
        ts = parse_ts(d.get("timestamp", ""))
        in_window = bool(ts and since < ts <= until)
        if typ == "assistant":
            for b in d.get("message", {}).get("content", []) or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and b.get("text", "").strip():
                    last_asst = b["text"].strip()
                elif b.get("type") == "tool_use":
                    tool_names[b.get("id")] = b.get("name")
                    if b.get("name") == "Skill":
                        active_skill = (b.get("input") or {}).get("skill", active_skill)
                        since_skill = 0
                        if in_window:
                            stats[active_skill]["uses"] += 1
                    elif b.get("name") == "Bash" and in_window:  # skill used by running its script
                        for n in set(SKILL_PATH_RE.findall((b.get("input") or {}).get("command", ""))):
                            stats[n]["uses"] += 1
            continue
        if typ != "user" or d.get("entrypoint") == "sdk-cli" or d.get("isSidechain") or d.get("isCompactSummary"):
            continue
        content = d.get("message", {}).get("content")
        raw = content_text(content)
        origin = (d.get("origin") or {}).get("kind", "human")  # older transcripts have no "origin"
        prev_skill = active_skill
        active_skill = track_skill(raw, active_skill)
        if active_skill != prev_skill:
            since_skill = 0
        elif origin == "human" and not d.get("isMeta"):
            since_skill += 1
            if since_skill > SKILL_TTL:
                active_skill = ""
        if in_window and active_skill != prev_skill and CMD_RE.search(raw):
            stats[active_skill]["uses"] += 1
        if not in_window:
            continue
        base = {"session": sid, "ts": iso(ts), "cwd": d.get("cwd", ""), "skill": active_skill,
                "before": last_asst[-600:]}
        if isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_result":
                    c = b.get("content")
                    s = c if isinstance(c, str) else content_text(c)
                    if "doesn't want to proceed with this tool use" in s:
                        reason = s.split("the user said:", 1)[1].strip() if "the user said:" in s else ""
                        events.append({**base, "kind": "rejection",
                                       "tool": tool_names.get(b.get("tool_use_id"), "?"), "text": reason})
                        stats[active_skill or "-"]["rejections"] += 1
                    elif b.get("is_error"):
                        stats[active_skill or "-"]["tool_errors"] += 1
                elif b.get("type") == "text" and "[Request interrupted by user" in b.get("text", ""):
                    events.append({**base, "kind": "interrupt", "text": ""})
                    stats[active_skill or "-"]["interrupts"] += 1
                    after_interrupt = True
        if d.get("isMeta") or origin != "human":
            continue
        text = clean_text(raw)
        if not text or text.startswith("[Request interrupted"):
            continue
        if any(k in text for k in SELF_MARKERS) or (IGNORE_RE and IGNORE_RE.search(text)):
            continue
        hint = bool(CORRECTION_RE.search(norm(text))) or after_interrupt
        if hint:
            stats[active_skill or "-"]["correction_hints"] += 1
        events.append({**base, "kind": "message", "text": text, "after_interrupt": after_interrupt,
                       "hint": hint})
        after_interrupt = False
        last_asst = ""
    return events


def shingles(text):
    w = re.findall(r"[a-z0-9]+", norm(re.sub(r"(https?://\S+|/\S+)", " ", text)))
    return {" ".join(w[i:i + 2]) for i in range(len(w) - 1)} if len(w) >= 4 else set()


def repeats(msgs):
    """Near-identical messages (Jaccard of word bigrams >= 0.5): the 'I said the same thing again'."""
    items = [(i, s) for i, s in ((i, shingles(m["text"])) for i, m in enumerate(msgs)) if s]
    parent = {i: i for i, _ in items}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a in range(len(items)):
        ia, sa = items[a]
        for b in range(a + 1, len(items)):
            ib, sb = items[b]
            inter = len(sa & sb)
            if inter and inter / len(sa | sb) >= 0.5:
                parent[find(ia)] = find(ib)
    groups = defaultdict(list)
    for i, _ in items:
        groups[find(i)].append(msgs[i])
    out = []
    for g in groups.values():
        # re-sent in the same session within 90 s = edited/pasted again, not a repetition
        g.sort(key=lambda m: (m["session"], m["ts"]))
        kept = [m for k, m in enumerate(g) if not (k and m["session"] == g[k - 1]["session"] and
                (parse_ts(m["ts"]) - parse_ts(g[k - 1]["ts"])).total_seconds() < 90)]
        if len(kept) >= 2:
            out.append(kept)
    return sorted(out, key=len, reverse=True)


def desc_of(p):
    try:
        t = open(p, errors="ignore", encoding="utf-8").read()
    except Exception:
        return ""
    m = re.search(r"^description:\s*(.+)$", t, re.M)
    return (m.group(1) if m else "").strip()[:220]


def skill_files():
    """(owner label, SKILL.md path) for every skill in the skill roots."""
    rows, seen = [], set()
    for root in SKILL_ROOTS:
        for d in sorted(glob.glob(os.path.join(root, "*"))):
            p = os.path.join(d, "SKILL.md")
            if not os.path.isfile(p) or rp(p) in seen:
                continue
            seen.add(rp(p))
            linked_out = os.path.islink(d) and not any(
                in_zone(rp(d), [r]) for r in SKILL_ROOTS if rp(r) != rp(root))
            rows.append(("linked-skill (symlink: an update of its source may overwrite edits)" if linked_out
                         else "own-skill", p))
    return rows


def project_claude_mds(cwds=()):
    found = []
    for root in C.path_list("project_roots"):
        found += glob.glob(os.path.join(root, "CLAUDE.md")) + glob.glob(os.path.join(root, "*/CLAUDE.md")) + \
                 glob.glob(os.path.join(root, "*/*/CLAUDE.md"))
    for cwd in cwds:  # folders you actually worked in
        if cwd:
            found += [os.path.join(cwd, "CLAUDE.md"), os.path.join(cwd, ".claude", "CLAUDE.md")]
    return sorted({p for p in found if os.path.isfile(p)})


def inventory(cwds=()):
    """Editable targets and who owns them (editing a plugin's or a linked skill is lost on update)."""
    rows = [(owner, p, desc_of(p)) for owner, p in skill_files()]
    ch = C.CLAUDE_HOME
    paths = [os.path.join(ch, "CLAUDE.md"), os.path.join(ch, "settings.json")] + \
        sorted(glob.glob(os.path.join(ch, "output-styles/*.md"))) + sorted(glob.glob(os.path.join(ch, "hooks/*"))) + \
        sorted(glob.glob(os.path.join(ch, "agents/*.md"))) + sorted(glob.glob(os.path.join(ch, "commands/*.md")))
    if WIKI:
        paths += [os.path.join(WIKI, "CLAUDE.md"), os.path.join(os.path.dirname(WIKI), "CLAUDE.md")]
    paths += project_claude_mds(cwds)
    seen = set()
    for p in paths:
        if os.path.isfile(p) and p not in seen:
            seen.add(p)
            rows.append(("config", p, desc_of(p)))
    for p in sorted(glob.glob(os.path.join(SESSIONS, "*/memory/*.md"))):
        rows.append(("memory", p, desc_of(p)))
    return rows


# ---------- what changed / what came in ----------
def watch_list():
    ch = C.CLAUDE_HOME
    w = [("skill", r, "**/*") for r in SKILL_ROOTS] + [
        ("global config", ch, "*.md"), ("global config", ch, "settings*.json"),
        ("hook", os.path.join(ch, "hooks"), "*"), ("output-style", os.path.join(ch, "output-styles"), "*"),
        ("agent", os.path.join(ch, "agents"), "*"), ("command", os.path.join(ch, "commands"), "*"),
        ("memory", SESSIONS, "*/memory/*.md"),
        ("plugins", os.path.join(ch, "plugins"), "installed_plugins.json")]
    if WIKI:
        w.append(("wiki", WIKI, "**/*.md"))
    if RAW:
        w.append(("raw (new sources)", RAW, "**/*"))
    return w


def changed_files(since, until):
    out = defaultdict(list)
    for label, root, pat in watch_list():
        for p in glob.glob(os.path.join(root, pat), recursive=True):
            if not os.path.isfile(p) or "/.venv/" in p or "/node_modules/" in p or "/__pycache__/" in p \
                    or os.path.basename(p) == ".DS_Store" or ".bak-" in p:
                continue
            m = os.path.getmtime(p)
            if since.timestamp() < m <= until.timestamp():
                out[label].append((m, p))
    return {k: sorted(v, reverse=True) for k, v in out.items()}


def wiki_log_entries(since):
    rows = []
    if not WIKI:
        return rows
    for p in [os.path.join(WIKI, "log.md")] + sorted(glob.glob(os.path.join(WIKI, "log/*.md"))):
        if not os.path.exists(p):
            continue
        for line in open(p, errors="ignore", encoding="utf-8"):
            m = re.match(r"^##\s*\[(\d{4}-\d{2}-\d{2})\]\s*(.+)", line)
            if m and m.group(1) >= since.astimezone().strftime("%Y-%m-%d"):
                rows.append(f"{m.group(1)} {m.group(2).strip()}  ({short(p)})")
    return rows


# ---------- deterministic lint ----------
LINK_MD = re.compile(r"\]\(([^)\s]+?\.md)(#[^)]*)?\)")
LINK_WIKI = re.compile(r"\[\[([^\]|#\\]+)(#[^\]|\\]*)?(\\?\|[^\]]*)?\]\]")  # accepts [[x\|y]] inside tables
LINK_TICK = re.compile(r"`(\.\./[^`\s]+)`")
QUOTED = re.compile(r'"[^"\n]*"|“[^”\n]*”|`[^`\n]*`|^[ \t]*>.*$', re.M)  # quotes: "…", “…”, `…`, blockquote


def wiki_skipped(p):
    p = p.replace(os.sep, "/")
    if "/log/" in p:
        return True
    return any(f"/{d.strip('/')}/" in p for d in C.opt("wiki_auto_dirs") if d)


def lint_wiki(add):
    pages = sorted(glob.glob(os.path.join(WIKI, "**/*.md"), recursive=True))
    by_name = defaultdict(list)
    for p in pages:
        by_name[os.path.splitext(os.path.basename(p))[0]].append(p)
    names = {n: ps[0] for n, ps in by_name.items() if len(ps) == 1}
    for n, ps in by_name.items():
        if len(ps) > 1:
            add("ambiguous-name", ps[0], f"[[{n}]] is ambiguous: {', '.join(short(x) for x in ps)}", auto_ok=False)
    inbound = Counter()
    root = os.path.dirname(WIKI)
    for p in pages:
        txt = open(p, errors="ignore", encoding="utf-8").read()
        base = os.path.dirname(p)
        for m in LINK_WIKI.finditer(txt):
            n = m.group(1).strip()
            n_base = os.path.basename(n)
            if n in names or n_base in names:
                inbound[names.get(n) or names[n_base]] += 1
            elif os.path.exists(os.path.join(WIKI, n + ".md")):
                inbound[os.path.join(WIKI, n + ".md")] += 1
            elif n_base in by_name:
                pass  # ambiguous: already reported
            else:
                add("broken-link", p, f"[[{n}]] does not exist")
        for m in LINK_MD.finditer(txt):
            t = m.group(1)
            if t.startswith("http"):
                continue
            full = os.path.normpath(os.path.join(base, t))
            if os.path.exists(full):
                inbound[full] += 1
            else:
                add("broken-link", p, f"({t}) does not exist")
        for m in LINK_TICK.finditer(txt):
            rel = m.group(1).rstrip("/")  # project paths may be written relative to the wiki's parent folder
            if not any(os.path.exists(os.path.normpath(os.path.join(r, rel))) for r in (base, root)):
                add("broken-link", p, f"`{m.group(1)}` does not exist", auto_ok=False)
        for m in RELATIVE_DATE_RE.finditer(QUOTED.sub(" ", txt)):
            add("relative-date", p, f"relative date '{m.group(0)}'")
            break
    idx = os.path.join(WIKI, "INDEX.md")
    if os.path.exists(idx):  # INDEX checks only when the wiki has one
        idx_txt = open(idx, errors="ignore", encoding="utf-8").read()
        idx_links = {os.path.normpath(os.path.join(WIKI, t)) for t, _ in LINK_MD.findall(idx_txt)} | \
                    {os.path.normpath(names.get(m.group(1).strip(), os.path.join(WIKI, m.group(1).strip() + ".md")))
                     for m in LINK_WIKI.finditer(idx_txt)}
    else:
        idx_links = None
    special = {"INDEX.md", "log.md", "CLAUDE.md"}
    for p in pages:
        if os.path.basename(p) in special or wiki_skipped(p):
            continue
        if idx_links is not None and os.path.normpath(p) not in idx_links:
            add("index-entry", idx, f"{os.path.basename(p)} missing from INDEX")
        if inbound[p] == 0:
            add("orphan", p, "no page links here", auto_ok=False)
        age = (time.time() - os.path.getmtime(p)) / 86400
        if age > 60:
            add("stale", p, f"unchanged for {int(age)} days", auto_ok=False)


def lint(skill_stats_30d):
    items = []

    def add(kind, target, detail, auto_ok=True):
        items.append({"kind": kind, "target": target, "detail": detail, "auto_ok": auto_ok})

    if WIKI and os.path.isdir(WIKI):
        lint_wiki(add)
    for root in [r for r in [WIKI, RAW] + SKILL_ROOTS if r and os.path.isdir(r)]:
        for p in glob.glob(os.path.join(root, "**/*conflicted copy*"), recursive=True):
            if not in_zone(p, [C.TRASH]):
                add("conflicted-copy", p, "sync conflict copy")

    # own skills only (a linked skill belongs to its source; editing it here is lost on update)
    for owner, p in skill_files():
        if owner != "own-skill":
            continue
        txt = open(p, errors="ignore", encoding="utf-8").read()
        d = os.path.dirname(p)
        name = os.path.basename(d)
        if not re.search(r"^name:\s*\S", txt, re.M) or not re.search(r"^description:\s*\S", txt, re.M):
            add("skill-frontmatter", p, "frontmatter without name/description", auto_ok=False)
        refs = set()
        for m in re.finditer(r"\b((?:references|scripts|assets)/[\w./-]+\.\w+)", txt):
            s = m.start()
            while s > 0 and re.match(r"[~\w./-]", txt[s - 1]):
                s -= 1
            full = txt[s:m.end()]  # whole path: ~/…/other-skill/scripts/x, ../other/references/y
            refs.add(full if full.startswith(("~", "/", "..")) and full != "/" + m.group(1) else m.group(1))
        for ref in sorted(refs):
            r = os.path.expanduser(ref)
            if not os.path.exists(r if os.path.isabs(r) else os.path.join(d, r)):
                add("skill-broken-ref", p, f"mentions {ref}, which does not exist", auto_ok=False)
        if not in_zone(p, [C.USER_SKILLS]) and not os.path.exists(os.path.join(C.USER_SKILLS, name)):
            add("skill-not-linked", p, f"no link in {short(C.USER_SKILLS)}/{name}", auto_ok=False)
        if skill_stats_30d.get(name, {}).get("uses", 0) == 0:
            add("bloat-unused-skill", p, "0 uses in local sessions in 30 days (check cron/remote use first)",
                auto_ok=False)
        if len(txt) > 20_000:
            add("bloat-big-skill", p, f"SKILL.md is {len(txt)//1000} KB", auto_ok=False)
    for root in SKILL_ROOTS:
        for d in glob.glob(os.path.join(root, "*")):
            if os.path.islink(d) and not os.path.exists(d):
                add("broken-symlink", d, f"link points to {os.readlink(d)} (gone)", auto_ok=False)
    # old backups
    cutoff = time.time() - 14 * 86400
    for root in [C.CLAUDE_HOME] + [r for r in SKILL_ROOTS if not in_zone(r, [C.CLAUDE_HOME])]:
        for p in glob.glob(os.path.join(root, "**/*.bak-*"), recursive=True):
            if "/backups/" in p or "/plugins/" in p or "/projects/" in p:
                continue
            if os.stat(p).st_ctime < cutoff:  # copy2 keeps the original's mtime
                add("backup-cleanup", p, "backup older than 14 days")
    # memory: index vs files
    for mem in glob.glob(os.path.join(SESSIONS, "*/memory")):
        idxp = os.path.join(mem, "MEMORY.md")
        if not os.path.exists(idxp):
            continue
        itx = open(idxp, errors="ignore", encoding="utf-8").read()
        linked = set(re.findall(r"\]\(([^)]+\.md)\)", itx))
        files = {os.path.basename(p) for p in glob.glob(os.path.join(mem, "*.md"))} - {"MEMORY.md"}
        for f in sorted(linked - files):
            if os.path.exists(os.path.join(mem, os.path.expanduser(f))):
                continue  # link to an existing file outside the folder
            add("memory-index", idxp, f"points to {f}, which does not exist")
        for f in sorted(files - linked):
            add("memory-index", idxp, f"{f} has no line in the index")
    extra_lint(add)
    return items


def extra_lint(add):
    """Findings from the user's extra_lint_command: one per output line, as JSON
    {"kind", "target", "detail", "auto_ok"?} or as kind<TAB>target<TAB>detail. auto_ok defaults to false."""
    rc, out, err = C.run_user_command("extra_lint_command", "lint", {"SELF_IMPROVE_WIKI_DIR": WIKI,
                                                                    "SELF_IMPROVE_RAW_DIR": RAW})
    if rc is None and not err:
        return
    if rc != 0:
        add("extra-lint-failed", C.opt("extra_lint_command"),
            f"extra_lint_command failed ({err.strip()[:200] or 'exit ' + str(rc)})", auto_ok=False)
        return
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line) if line.startswith("{") else None
        except ValueError:
            d = None
        if isinstance(d, dict) and d.get("kind") and d.get("target"):
            add(str(d["kind"]), str(d["target"]), str(d.get("detail") or ""), auto_ok=d.get("auto_ok") is True)
            continue
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0] and parts[1]:
            add(parts[0], parts[1], "\t".join(parts[2:]), auto_ok=False)


# ---------- previous reviews ----------
# tolerant: a phone or notes app may drop the bold, swap "-" for "*" or change indentation;
# what identifies the item is the <!-- run/id --> comment on the checkbox line
ITEM_RE = re.compile(r"^\s*[-*+]\s*\[\s*([xX-]?)\s*\].*?<!--\s*([\w-]+)/(\w+)\s*-->")
ANS_HEAD_RE = re.compile(r"^\s*#{1,6}.*?<!--\s*([\w-]+)/(\w+)\s*-->")
ANY_ID_RE = re.compile(r"<!--\s*([\w-]+)/([ASC]\d+)\s*-->")
LABELS, LABEL_WARNINGS = C.labels()  # option `labels`: your words for the fixed text render writes


def _words(*words):
    """Regex alternation of the words (escaped, longest first, no duplicates)."""
    return "|".join(re.escape(w) for w in sorted(dict.fromkeys(words), key=len, reverse=True))


def answer_re(*extra):
    return re.compile(r"^\*\*(?:" + _words(*C.BUILTIN_ANSWER_WORDS, LABELS["answer"], *extra) + r"):\*\*\s*(.*)$")


def note_re(*extra):
    return re.compile(r"^\s*[-*]?\s*(" + _words(*C.BUILTIN_NOTE_WORDS, LABELS["note"], *extra) + r")\s*:", re.I)


# English, the old Portuguese word and your current label always count. A file also accepts the labels it
# was written with (recorded in render.json), so a later label change does not hide its notes or answers.
ANSWER_RE = answer_re()
NOTE_LINE_RE = note_re()


def file_labels():
    """{real path of a review file: {"note": ..., "answer": ...}} as render wrote them (older runs: none)."""
    out = {}
    for p in glob.glob(os.path.join(STATE, "runs", "*", "render.json")):
        r = jload(p, {})
        lab = r.get("labels") if isinstance(r.get("labels"), dict) else {}
        for k in ("review", "needs_context"):
            if r.get(k) and lab:
                out[os.path.realpath(r[k])] = {x: str(lab[x]) for x in ("note", "answer") if lab.get(x)}
    return out


def regexes_for(path, known=None):
    """(note regex, answer regex) for one review file: built-in words, current labels, and its own labels."""
    lab = (file_labels() if known is None else known).get(os.path.realpath(path), {})
    if not lab:
        return NOTE_LINE_RE, ANSWER_RE
    return note_re(*filter(None, [lab.get("note")])), answer_re(*filter(None, [lab.get("answer")]))


def consumed_files(hist):
    out = set()
    for e in hist:
        if e["event"] == "finish":
            out.update(e.get("consumed", []))
    return out


def load_plan_item(rid, iid):
    for it in jload(os.path.join(run_dir(rid), "plan.json"), []):
        if it.get("id") == iid:
            return {**it, "run_id": rid}
    return None


def resolved_ids(hist):
    """Canonical ids (run/id) already applied or rejected: they never come back, even if the [x] stays."""
    done = set()
    for f in glob.glob(os.path.join(STATE, "runs", "*", "applied.jsonl")):
        rid = os.path.basename(os.path.dirname(f))
        for l in open(f, encoding="utf-8"):
            try:
                x = json.loads(l)["id"]
            except Exception:
                continue
            done.add(x if "/" in x else f"{rid}/{x}")
    for e in hist:
        if e.get("event") == "finish":
            done.update(e.get("rejected", []))
    return done


def parse_reviews(hist):
    """[x] = approved; [-] = rejected; [ ] = still pending (carried to the next review)."""
    done = consumed_files(hist)
    resolved = resolved_ids(hist)
    seen_items = {}
    approved, rejected, pending, answers, open_q, files, warnings = [], [], [], [], [], [], []
    known = file_labels()
    for f in sorted(glob.glob(os.path.join(OUT, "review-*.md"))):
        if f in done:
            continue
        files.append(f)
        note_line_re = regexes_for(f, known)[0]
        lines = open(f, errors="ignore", encoding="utf-8").read().splitlines()
        for i, line in enumerate(lines):
            m = ITEM_RE.match(line)
            if not m:
                if ANY_ID_RE.search(line) and not line.lstrip().startswith("#"):
                    warnings.append(f"{os.path.basename(f)}: line has an id but no readable checkbox, "
                                    f"treated as pending: {line.strip()[:120]}")
                    mm = ANY_ID_RE.search(line)
                    key = f"{mm.group(1)}/{mm.group(2)}"
                    it = load_plan_item(mm.group(1), mm.group(2))
                    if it and key not in resolved and key not in seen_items:
                        it["review_file"], it["notes"] = f, []
                        seen_items[key] = (" ", it)
                continue
            mark, rid, iid = (m.group(1) or " "), m.group(2), m.group(3)
            notes = []
            for nxt in lines[i + 1:]:
                if not nxt.startswith(" ") and nxt.strip():
                    break
                nm = note_line_re.match(nxt)
                if nm:
                    notes.append(nxt[nm.end():].strip())
            it = load_plan_item(rid, iid) or {"id": iid, "run_id": rid, "title": line, "missing_plan": True}
            it["review_file"], it["notes"] = f, notes
            if it.get("missing_plan"):
                warnings.append(f"{rid}/{iid}: plan of the original run not found, item ignored")
                continue
            key = f"{rid}/{iid}"
            if key in resolved:
                continue
            # the same item can be in 2 files (pending copied to the new review): a decision beats a doubt
            old = seen_items.get(key)
            if old and old[0] in ("x", "X", "-") and mark == " ":
                continue
            seen_items[key] = (mark, it)
    for mark, it in seen_items.values():
        {"x": approved, "X": approved, "-": rejected, " ": pending}[mark].append(it)
    for f in sorted(glob.glob(os.path.join(OUT, "needs-context-*.md"))):
        if f in done:
            continue
        files.append(f)
        ans_re = regexes_for(f, known)[1]
        cur = None
        for line in open(f, errors="ignore", encoding="utf-8").read().splitlines():
            h = ANS_HEAD_RE.match(line)
            if h:
                if cur:
                    (answers if cur["answer"] else open_q).append(cur)
                it = load_plan_item(h.group(1), h.group(2)) or {"id": h.group(2), "run_id": h.group(1)}
                cur = {**it, "answer": "", "file": f}
                continue
            if cur is not None:
                m = ans_re.match(line)
                if m:
                    cur["answer"] = m.group(1).strip()
                elif cur["answer"] and line.strip() and not line.startswith(("#", "---", ">")):
                    cur["answer"] += " " + line.strip()
        if cur:
            (answers if cur["answer"] else open_q).append(cur)
    # the same question can be open in a new file and answered in an older one that stayed open
    answered = {f"{x['run_id']}/{x['id']}" for x in answers}
    open_q = [q for q in open_q if f"{q['run_id']}/{q['id']}" not in answered]
    return approved, rejected, pending, answers, open_q, files, warnings


def item_digest(it):
    """What the user approves: the fields shown in the review plus the paths it may touch."""
    import hashlib
    keep = {k: it.get(k) for k in ("title", "target", "extra_targets", "change", "patch")}
    return hashlib.sha256(json.dumps(keep, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def rendered_digests():
    """{review file: {run/id: digest}} from every render.json (older runs have no digests)."""
    out = {}
    for p in glob.glob(os.path.join(STATE, "runs", "*", "render.json")):
        r = jload(p, {})
        if r.get("review") and isinstance(r.get("digests"), dict):
            out[os.path.realpath(r["review"])] = r["digests"]
    return out


def drop_changed_since_render(approved, pending, warnings):
    """An [x] counts only for the content that was shown. If plan.json changed after render, the item goes
    back to pending (it is shown again with the new content) instead of being applied."""
    shown = rendered_digests()
    keep = []
    for it in approved:
        d = shown.get(os.path.realpath(it.get("review_file", "")), {}).get(f"{it['run_id']}/{it['id']}")
        if d and d != item_digest(it):
            warnings.append(f"{it['run_id']}/{it['id']}: changed after it was shown in the review, "
                            f"not applied; it comes back as pending")
            pending.append(it)
        else:
            keep.append(it)
    return keep


def wiki_side_files():
    return {rp(os.path.join(WIKI, "INDEX.md")), rp(os.path.join(WIKI, "log.md"))} if WIKI else set()


def targets_to_allow(items):
    """EXACT paths released to guard.py (file by file)."""
    allow = set()
    for it in items:
        for t in [it.get("target", "")] + list(it.get("extra_targets", [])):
            t = t[4:] if t.startswith("new:") else t
            if not t:
                continue
            t = os.path.expanduser(t)
            allow.add(rp(t))  # the exact file only: a new skill lists each file in extra_targets
            if WIKI and in_zone(t, [WIKI]):  # every wiki change leaves a trace in INDEX and log
                allow.update(wiki_side_files())
    return allow


def write_allow(rd, extra):
    p = os.path.join(rd, "allowed-paths.txt")
    cur = set(open(p, encoding="utf-8").read().split("\n")) if os.path.exists(p) else set()
    cur = {c for c in cur if c} | extra
    open(p, "w", encoding="utf-8").write("\n".join(sorted(cur)) + "\n")
    return sorted(cur)


# ---------- commands ----------
def write_lock(run_id, rd):
    """Lock for guard.py, tied to the session that ran `start` (when the hook recorded it)."""
    sess, owner = None, None
    s = jload(STARTING, {})
    if time.time() - s.get("epoch", 0) < 120:
        sess, owner = s.get("session_id"), s.get("owner_pid")
    # O_EXCL: two `start` at once -> only one gets the lock
    fd = os.open(LOCK, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"run_id": run_id, "run_dir": rd, "session_id": sess, "owner_pid": owner,
                   "started_epoch": time.time()}, f)
    return sess


def pre_run(mode, since, until):
    """The user's pre_run_command. {"code": exit code or None, "output": text for changes.md, "note": status}."""
    rc, out, err = C.run_user_command("pre_run_command", "pre_run", {
        "SELF_IMPROVE_MODE": mode, "SELF_IMPROVE_SINCE": iso(since) if since else "",
        "SELF_IMPROVE_UNTIL": iso(until) if until else "", "SELF_IMPROVE_WIKI_DIR": WIKI,
        "SELF_IMPROVE_RAW_DIR": RAW})
    if rc is None:
        return {"code": None, "output": "", "note": err.strip()}
    if rc == 0:
        return {"code": 0, "output": out.strip(), "note": ""}
    note = (err.strip() or out.strip())[:200]
    return {"code": rc, "output": "", "note": f"exit {rc}" + (f": {note}" if note else "")}


def write_user_notes(rd):
    """language + analyst_notes -> <run_dir>/user-notes.md for the analysts and the plan. Path, or None."""
    lang = C.opt("language")
    notes = C.text_or_file(C.opt("analyst_notes"))
    if not lang and not notes:
        return None
    parts = ["# Notes from the user's self-improve config (follow them)\n"]
    if lang:
        parts.append(f"Language: write titles, evidence, questions, options and the chat summary in {lang}. "
                     "Keep the JSON keys, bucket names and kinds in English.\n")
    if notes:
        parts.append(notes + "\n")
    path = os.path.join(rd, "user-notes.md")
    with open(path, "w", encoding="utf-8") as fo:
        fo.write("\n".join(parts))
    return path


def lock_busy():
    """Why `start` would exit 3 right now, or ""."""
    if WAIT_LOCK and os.path.exists(WAIT_LOCK):
        return f"{WAIT_LOCK} exists"
    # the same variables as a real run (no window yet), so a command that reads them doesn't crash here
    rc, out, err = C.run_user_command("pre_run_command", "pre_run", {
        "SELF_IMPROVE_MODE": "check", "SELF_IMPROVE_SINCE": "", "SELF_IMPROVE_UNTIL": "",
        "SELF_IMPROVE_WIKI_DIR": WIKI, "SELF_IMPROVE_RAW_DIR": RAW})
    if rc == 3:
        return "pre_run_command answered 3 (busy)"
    return ""


def cmd_wait_lock(a):
    limit = time.time() + 60 * (a.max_minutes if a.max_minutes is not None else C.opt("lock_wait_minutes"))
    while True:
        why = lock_busy()
        if not why:
            print("wait-lock: free, run `start`")
            return
        if time.time() >= limit:
            print(f"wait-lock: still busy ({why}); gave up", file=sys.stderr)
            sys.exit(3)
        time.sleep(max(1, min(a.every, limit - time.time())))


def cmd_start(a):
    hist = read_history()
    # another job (option wait_for_lock) still writing: wait, don't run alongside it
    if WAIT_LOCK and os.path.exists(WAIT_LOCK):
        print(f"{WAIT_LOCK} exists (another job is running): wait for it to finish and run `start` again",
              file=sys.stderr)
        sys.exit(3)
    lk = None
    if os.path.exists(LOCK):
        try:
            lk = json.load(open(LOCK, encoding="utf-8"))
        except Exception:
            lk = {"run_id": "?"}
        if not a.force:
            sys.exit(f"run {lk.get('run_id')} is already active. Close it with `finish {lk.get('run_id')}` "
                     f"or `abort {lk.get('run_id')}` (the lock never expires by itself)")
    until = now_utc()
    prev = last_finished(hist)
    if a.since:
        since = parse_ts(a.since)
        if not since:
            sys.exit(f"invalid --since: {a.since}")
    elif prev:
        since = parse_ts(prev["covered_until"])
    else:
        since = until - timedelta(days=a.days)
    pre = pre_run("apply-only" if a.apply_only else "full", since, until)
    if pre["code"] == 3:
        print(f"pre_run_command says another job is busy ({pre['note'] or 'exit 3'}): run `wait-lock`, "
              "then `start` again", file=sys.stderr)
        sys.exit(3)  # before --force touches the active run: a busy answer leaves it as it was
    if lk is not None:
        do_abort(lk.get("run_id", "?"), "replaced by --force")
    base_id = until.astimezone().strftime("%Y%m%d-%H%M%S")
    os.makedirs(os.path.join(STATE, "runs"), exist_ok=True)
    for n in range(100):  # exclusive mkdir: two starts in the same second don't share a folder
        run_id = base_id if n == 0 else f"{base_id}-{n}"
        rd = run_dir(run_id)
        try:
            os.mkdir(rd)
            break
        except FileExistsError:
            continue
    os.makedirs(OUT, exist_ok=True)

    # 1. previous reviews
    approved, rejected, pending, answers, open_q, rfiles, warnings = parse_reviews(hist)
    approved = drop_changed_since_render(approved, pending, warnings)
    # finish compares these: a review marked after this point stays open instead of being consumed
    jdump({f: sha256_of(f) for f in rfiles}, os.path.join(rd, "review-hashes.json"))
    flag_possibly_applied(approved)  # edited and crashed before `log` in an earlier run -> check, don't reapply
    jdump(warnings, os.path.join(rd, "review-warnings.json"))
    jdump(approved, os.path.join(rd, "approved.json"))
    jdump(pending, os.path.join(rd, "carried.json"))
    jdump(rejected, os.path.join(rd, "rejected-now.json"))
    jdump(answers, os.path.join(rd, "answers.json"))
    jdump(open_q, os.path.join(rd, "open-questions.json"))
    jdump(rfiles, os.path.join(rd, "review-files.json"))
    write_allow(rd, targets_to_allow(approved))
    notes = write_user_notes(rd)
    if pre["output"]:
        with open(os.path.join(rd, "pre-run.md"), "w", encoding="utf-8") as fo:
            fo.write(pre["output"] + "\n")
    if a.apply_only:  # only apply what you marked: no sessions/wiki, window stays, questions untouched
        jdump([], os.path.join(rd, "answers.json"))
        jdump([], os.path.join(rd, "open-questions.json"))
        jdump([f for f in rfiles if os.path.basename(f).startswith("review-")], os.path.join(rd, "review-files.json"))
        jdump([], os.path.join(rd, "plan.json"))
        sess = write_lock(run_id, rd)
        meta = {"event": "start", "run_id": run_id, "apply_only": True,
                "covered_from": iso(since), "covered_until": iso(since),
                "approved": len(approved), "rejected_now": len(rejected), "carried": len(pending),
                "guard_session": sess, "run_dir": rd, "user_notes": notes, "language": C.opt("language") or None,
                "pre_run": pre["note"] or None}
        jdump(meta, os.path.join(rd, "meta.json"))
        append_history(meta)
        print(json.dumps(meta, ensure_ascii=False, indent=1))
        return

    # 2. sessions in the window (+ 30 days only to count skill use)
    stats = defaultdict(Counter)
    stats30 = defaultdict(Counter)
    evs = []
    since30 = min(since, until - timedelta(days=30))
    for f in session_files(since30):
        evs.extend(extract(f, since, until, stats))
        extract(f, since30, until, stats30)
    evs.sort(key=lambda e: (e["session"], e["ts"]))
    msgs = [e for e in evs if e["kind"] == "message"]
    with open(os.path.join(rd, "events.jsonl"), "w", encoding="utf-8") as fo:
        for e in evs:
            fo.write(json.dumps(e, ensure_ascii=False) + "\n")

    by_sess = defaultdict(list)
    for e in evs:
        by_sess[e["session"]].append(e)
    chunks, cur, size = [], [], 0
    for sid, es in by_sess.items():
        block = [f"\n## Session {sid}  cwd={es[0]['cwd']}\n"]
        for e in es:
            flag = " ⚑" if e.get("hint") or e["kind"] != "message" else ""
            head = f"\n[{e['ts']}] {e['kind'].upper()}{flag} skill={e['skill'] or '-'}"
            if e["kind"] == "rejection":
                head += f" tool={e['tool']}"
            ctx = f"\n  CLAUDE BEFORE: {e['before'][-400:]!r}" if e["before"] else ""
            block.append(f"{head}{ctx}\n  USER: {e['text']}\n")
        # a huge session becomes several pieces (each repeats the session header)
        pieces, pc = [], block[0]
        for b in block[1:]:
            if len(pc) + len(b) > CHUNK_BYTES and len(pc) > len(block[0]):
                pieces.append(pc)
                pc = block[0].rstrip("\n") + " (continued)\n"
            pc += b
        pieces.append(pc)
        for txt in pieces:
            if cur and size + len(txt) > CHUNK_BYTES:
                chunks.append(cur)
                cur, size = [], 0
            cur.append(txt)
            size += len(txt)
    if cur:
        chunks.append(cur)
    for i, c in enumerate(chunks, 1):
        with open(os.path.join(rd, f"chunk-{i:02d}.md"), "w", encoding="utf-8") as fo:
            fo.write("".join(c))

    reps = repeats(msgs)
    with open(os.path.join(rd, "repeats.md"), "w", encoding="utf-8") as fo:
        fo.write("# Near-identical messages (2+ times)\n")
        for g in reps:
            fo.write(f"\n## {len(g)}x in {len({m['session'] for m in g})} sessions\n")
            for m in g[:6]:
                fo.write(f"- [{m['ts']}] {m['session'][:8]} skill={m['skill'] or '-'}: {m['text'][:200]!r}\n")

    with open(os.path.join(rd, "friction.md"), "w", encoding="utf-8") as fo:
        fo.write("# Friction per skill in the window\n\n"
                 "| skill | uses | tool errors | Esc | rejections | messages that look like corrections | uses 30d |\n"
                 "|---|---|---|---|---|---|---|\n")
        for k, c in sorted(stats.items(), key=lambda kv: -(kv[1]["tool_errors"] + 3 * kv[1]["interrupts"] +
                                                            3 * kv[1]["rejections"] + 2 * kv[1]["correction_hints"])):
            fo.write(f"| {k or '-'} | {c['uses']} | {c['tool_errors']} | {c['interrupts']} | {c['rejections']} | "
                     f"{c['correction_hints']} | {stats30.get(k, Counter())['uses']} |\n")

    # 3. what changed / what came in
    ch = changed_files(since, until)
    logs = wiki_log_entries(since)
    with open(os.path.join(rd, "changes.md"), "w", encoding="utf-8") as fo:
        fo.write(f"# Changes and new data {iso(since)} -> {iso(until)}\n")
        for label, rows in ch.items():
            fo.write(f"\n## {label} ({len(rows)})\n")
            for m, p in rows[:40]:
                fo.write(f"- {datetime.fromtimestamp(m).strftime('%Y-%m-%d %H:%M')}  {short(p)}\n")
            if len(rows) > 40:
                fo.write(f"- … +{len(rows) - 40}\n")
        if WIKI:
            fo.write(f"\n## Wiki log entries ({len(logs)})\n" + "".join(f"- {r}\n" for r in logs))
        if pre["output"]:
            fo.write("\n## From your pre_run_command\n\n" + pre["output"] + "\n")

    # 4. lint
    lt = lint({k: v for k, v in stats30.items()})
    jdump(lt, os.path.join(rd, "lint.json"))
    with open(os.path.join(rd, "lint.md"), "w", encoding="utf-8") as fo:
        fo.write("# Deterministic lint (machine findings: check before proposing)\n\n"
                 "| kind | can be AUTO? | target | detail |\n|---|---|---|---|\n")
        for it in lt:
            fo.write(f"| {it['kind']} | {'yes' if it['auto_ok'] and it['kind'] in AUTO_KINDS else 'no'} | "
                     f"{short(it['target'])} | {it['detail']} |\n")

    # 5. wiki in chunks (for the wiki analyst)
    # whole wiki every 14 days (or --full-wiki); otherwise only what changed + link neighbors
    all_pages = [p for p in sorted(glob.glob(os.path.join(WIKI, "**/*.md"), recursive=True))
                 if not wiki_skipped(p)] if WIKI else []
    fulls = [parse_ts(e["covered_until"]) for e in hist if e.get("event") == "start" and e.get("wiki_full")
             and e["run_id"] in {x["run_id"] for x in hist if x.get("event") == "finish"}]
    wiki_full = bool(WIKI) and (a.full_wiki or not fulls or (until - max(fulls)).days >= 14)
    if wiki_full:
        wiki_pages = all_pages
    else:
        changed = {p for _, p in ch.get("wiki", [])}
        nb = set(changed)
        for p in all_pages:
            t = open(p, errors="ignore", encoding="utf-8").read()
            links = {m.group(1).strip() for m in LINK_WIKI.finditer(t)} | \
                    {os.path.splitext(os.path.basename(x))[0] for x, _ in LINK_MD.findall(t)}
            stem = os.path.splitext(os.path.basename(p))[0]
            if p in changed:
                nb |= {q for q in all_pages if os.path.splitext(os.path.basename(q))[0] in links}
            elif any(os.path.splitext(os.path.basename(c))[0] in links for c in changed) and stem:
                nb.add(p)
        wiki_pages = [p for p in all_pages if p in nb]
    wchunks, cur, size = [], [], 0
    for p in wiki_pages:
        t = open(p, errors="ignore", encoding="utf-8").read()
        mt = datetime.fromtimestamp(os.path.getmtime(p)).strftime('%Y-%m-%d')
        block = f"\n\n===== FILE: {short(p)}  (mtime {mt}) =====\n{t}"
        if cur and size + len(block) > CHUNK_BYTES:
            wchunks.append(cur)
            cur, size = [], 0
        cur.append(block)
        size += len(block)
    if cur:
        wchunks.append(cur)
    for i, c in enumerate(wchunks, 1):
        with open(os.path.join(rd, f"wiki-{i:02d}.md"), "w", encoding="utf-8") as fo:
            fo.write("".join(c))

    cwds = sorted({e["cwd"] for e in evs if e.get("cwd")})
    with open(os.path.join(rd, "inventory.md"), "w", encoding="utf-8") as fo:
        fo.write("# Possible edit targets\n\n| owner | file | description |\n|---|---|---|\n")
        for owner, p, dsc in inventory(cwds):
            fo.write(f"| {owner} | {p} | {dsc.replace('|', '/')} |\n")
        fo.write("\nPlugin skills and linked (imported) skills are NOT edit targets: an update overwrites them. "
                 "Propose an override in your own skill, or a hook, instead.\n")

    with open(os.path.join(rd, "previously-rejected.md"), "w", encoding="utf-8") as fo:
        fo.write("# Proposals already rejected (do not propose again without new evidence)\n")
        for e in hist:
            if e["event"] == "finish":
                for r in e.get("rejected_items", []):
                    fo.write(f"- [{e['run_id']}] {r}\n")
        for it in rejected:
            fo.write(f"- [{it.get('run_id')}] {it.get('title', it['id'])}\n")

    # 6. lock for guard.py (tied to the session that ran start, when the hook recorded it)
    sess = write_lock(run_id, rd)

    meta = {"event": "start", "run_id": run_id, "covered_from": iso(since), "covered_until": iso(until),
            "sessions": len(by_sess), "messages": len(msgs),
            "hints": sum(1 for m in msgs if m["hint"]),
            "interrupts": sum(1 for e in evs if e["kind"] == "interrupt"),
            "rejections": sum(1 for e in evs if e["kind"] == "rejection"),
            "repeat_groups": len(reps), "chunks": len(chunks), "wiki_chunks": len(wchunks),
            "wiki_full": wiki_full, "wiki_pages": len(wiki_pages), "wiki_enabled": bool(WIKI),
            "changed": {k: len(v) for k, v in ch.items()}, "lint": len(lt),
            "approved": len(approved), "rejected_now": len(rejected), "carried": len(pending),
            "answers": len(answers), "open_questions": len(open_q), "guard_session": sess, "run_dir": rd,
            "user_notes": notes, "language": C.opt("language") or None, "pre_run": pre["note"] or None}
    jdump(meta, os.path.join(rd, "meta.json"))
    append_history(meta)
    print(json.dumps(meta, ensure_ascii=False, indent=1))


def cmd_render(a):
    rd = run_dir(a.run_id)
    plan_p = os.path.join(rd, "plan.json")
    if not os.path.exists(plan_p):
        sys.exit(f"missing {plan_p}")
    plan = json.load(open(plan_p, encoding="utf-8"))
    errs, seen = [], Counter()
    for it in plan:
        b = it.get("bucket")
        if b not in BUCKETS:
            errs.append(f"{it.get('title')}: invalid bucket '{b}'")
            continue
        for k in ("title", "kind"):
            if not it.get(k):
                errs.append(f"{it.get('title', '?')}: missing '{k}'")
        if b != "context" and not it.get("target"):
            errs.append(f"{it['title']}: missing 'target'")
        targets = [x for x in [it.get("target", "")] + list(it.get("extra_targets") or []) if x]
        for t in targets:
            t0 = os.path.expanduser(t[4:] if t.startswith("new:") else t)
            if not os.path.isabs(t0):
                errs.append(f"{it['title']}: target '{t}' must be an absolute path")
            elif RAW and in_zone(t0, [RAW]) and not (b == "auto" and it.get("kind") == "conflicted-copy"):
                errs.append(f"{it['title']}: {t} is in raw_dir (immutable). Only a conflict copy may be moved")
        if b == "auto":
            kind = it.get("kind")
            if kind not in AUTO_KINDS:
                errs.append(f"{it['title']}: kind '{kind}' is not AUTO. Move it to signoff")
            for t in targets:
                t0 = os.path.expanduser(t[4:] if t.startswith("new:") else t)
                junk = (kind == "backup-cleanup" and ".bak-" in os.path.basename(t0)) or \
                       (kind == "conflicted-copy" and "conflicted copy" in os.path.basename(t0))
                if C.is_protected(t0, include_wiki=False) and not junk:
                    errs.append(f"{it['title']}: {t} is a skill/config file, never AUTO. Move it to signoff")
                elif not auto_target_ok(kind, t0):
                    errs.append(f"{it['title']}: {t} is outside the place allowed for '{kind}' "
                                f"({AUTO_ROOTS_DOC.get(kind, '?')})")
        if b == "context" and not it.get("question"):
            errs.append(f"{it['title']}: CONTEXT needs a 'question'")
        if "patch" in it:  # exact swap: the text approved is the text that goes in (apply-patch)
            pt, t0 = it["patch"], os.path.expanduser(it.get("target", ""))
            if not (isinstance(pt, dict) and isinstance(pt.get("old"), str) and pt["old"]
                    and isinstance(pt.get("new"), str)):
                errs.append(f"{it['title']}: 'patch' needs old (not empty) and new, both text")
            elif t0.startswith("new:") or not os.path.isfile(t0):
                errs.append(f"{it['title']}: 'patch' only works on a target that exists ({it.get('target')})")
            elif open(t0, errors="ignore", encoding="utf-8").read().count(pt["old"]) != 1:
                errs.append(f"{it['title']}: 'patch.old' must appear exactly once in {short(t0)}")
    n_sign = sum(1 for i in plan if i.get("bucket") == "signoff")
    if n_sign > MAX_SIGNOFF:
        errs.append(f"{n_sign} SIGN-OFF items, the maximum is {MAX_SIGNOFF}. Sort by strength x frequency and cut; "
                    f"say in the chat how many weak themes were left out")
    if errs:
        sys.exit("plan.json refused:\n- " + "\n- ".join(errs))
    if os.path.exists(os.path.join(rd, "applied.jsonl")) and any(
            json.loads(l)["id"].startswith(f"{a.run_id}/") for l in open(os.path.join(rd, "applied.jsonl"), encoding="utf-8")):
        sys.exit("this run already applied AUTO items. It can't be rendered again (the ids would change). "
                 "A new item waits for the next run.")
    for it in plan:  # stable ids per bucket
        seen[it["bucket"]] += 1
        it["id"] = f"{PREFIX[it['bucket']]}{seen[it['bucket']]:02d}"
    jdump(plan, plan_p)

    carried = jload(os.path.join(rd, "carried.json"), [])
    open_q = jload(os.path.join(rd, "open-questions.json"), [])
    day = f"{a.run_id[:4]}-{a.run_id[4:6]}-{a.run_id[6:8]}"
    prev_render = jload(os.path.join(rd, "render.json"), {})
    if prev_render.get("suffix"):
        suffix = prev_render["suffix"]  # render again in the same run = same files, replaced
        for k in ("review", "needs_context"):
            if prev_render.get(k) and os.path.exists(prev_render[k]):
                os.remove(prev_render[k])
    else:
        taken = glob.glob(os.path.join(OUT, f"review-{day}*.md")) + glob.glob(os.path.join(OUT, f"needs-context-{day}*.md"))
        suffix = day if not taken else f"{day}-{a.run_id[9:15]}"
    signoff = [i for i in plan if i["bucket"] == "signoff"]
    ctx = [i for i in plan if i["bucket"] == "context"]
    auto = [i for i in plan if i["bucket"] == "auto"]
    out = {}

    L = LABELS
    for w in LABEL_WARNINGS:
        print(f"warning: {w}", file=sys.stderr)

    def item_md(it, rid):
        lines = [f"- [ ] **{it['id']} · {it.get('title', '')}** <!-- {rid}/{it['id']} -->"]
        if it.get("target"):
            lines.append(f"    - {L['where']}: `{short(it['target'])}` ({it.get('kind')})")
        for key in ("evidence", "change", "why", "risk"):
            if it.get(key):
                lines.append(f"    - {L[key]}: {it[key]}")
        pt = it.get("patch")
        if isinstance(pt, dict) and isinstance(pt.get("old"), str) and isinstance(pt.get("new"), str):
            # one line (JSON strings): exactly what apply-patch will swap
            lines.append(f"    - {L['exact_change']}: {L['replace']} {json.dumps(pt['old'], ensure_ascii=False)} "
                         f"{L['with']} {json.dumps(pt['new'], ensure_ascii=False)}")
        if it.get("carried_since"):
            lines.append(f"    - {L['pending_since']} {it['carried_since']}")
        for n in it.get("notes") or []:  # your adjustments travel with a pending item
            lines.append(f"    - {L['note']}: {' '.join(str(n).split())}")
        return "\n".join(lines)

    if signoff or carried:
        p = os.path.join(OUT, f"review-{suffix}.md")
        body = [f"# {L['review_title']}, {day}", "",
                C.fill_label(L["review_intro"], run=a.run_id, note=L["note"], answer=L["answer"]),
                L["review_footer"], ""]
        if signoff:
            body += [f"## {L['new']}", ""] + [item_md(i, a.run_id) for i in signoff] + [""]
        if carried:
            body += [f"## {L['still_pending']}", ""]
            for c in carried:
                c.setdefault("carried_since", c.get("run_id", "")[:8])
                body.append(item_md(c, c["run_id"]))
            body.append("")
        open(p, "w", encoding="utf-8").write("\n".join(body))
        out["review"] = p
    if ctx or open_q:
        p = os.path.join(OUT, f"needs-context-{suffix}.md")
        body = [f"# {L['context_title']}, {day}", "",
                C.fill_label(L["context_intro"], run=a.run_id, note=L["note"], answer=L["answer"]), ""]
        for it, rid in [(i, a.run_id) for i in ctx] + [(q, q["run_id"]) for q in open_q]:
            body += [f"### {it['id']} · {it.get('title', '')} <!-- {rid}/{it['id']} -->", "",
                     f"{it.get('question', '')}", ""]
            if it.get("options"):
                body += [f"- {o}" for o in it["options"]] + [""]
            if it.get("evidence"):
                body += [f"{L['why_i_ask']}: {it['evidence']}", ""]
            body += [f"**{L['answer']}:** ", "", "---", ""]
        open(p, "w", encoding="utf-8").write("\n".join(body))
        out["needs_context"] = p
    allow = write_allow(rd, targets_to_allow(auto))
    digests = {f"{a.run_id}/{i['id']}": item_digest(i) for i in signoff}
    digests.update({f"{c['run_id']}/{c['id']}": item_digest(c) for c in carried})
    res = {**out, "suffix": suffix, "auto": len(auto), "signoff": len(signoff), "context": len(ctx),
           "carried": len(carried), "open_questions": len(open_q), "allowed": allow, "digests": digests}
    res["labels"] = {"note": L["note"], "answer": L["answer"]}  # the parser reads these back from this file
    if LABEL_WARNINGS:
        res["label_warnings"] = LABEL_WARNINGS
    jdump(res, os.path.join(rd, "render.json"))
    print(json.dumps(res, ensure_ascii=False, indent=1))


def item_for(run_id, ident):
    """Item + the paths it may touch. Same rules for log / intent / apply-patch: AUTO only from its own
    run; SIGN-OFF only if it is in this run's approved.json."""
    rd = run_dir(run_id)
    if not os.path.isdir(rd):
        sys.exit(f"run {run_id} does not exist")
    rid, iid = ident.split("/", 1) if "/" in ident else (run_id, ident)
    key = f"{rid}/{iid}"  # canonical id: same format in applied.jsonl, finish and reviews
    it = load_plan_item(rid, iid)
    if it is None:
        sys.exit(f"item {key} is not in the plan.json of run {rid}")
    approved = {f"{x['run_id']}/{x['id']}": x for x in jload(os.path.join(rd, "approved.json"), [])}
    if it.get("bucket") == "auto" and rid != run_id:
        sys.exit(f"{key} is AUTO from another run. AUTO is only applied in the run that proposed it")
    if it.get("bucket") != "auto":
        if key not in approved:
            sys.exit(f"{key} is not approved with [x] in this run (approved.json), it can't be logged as applied")
        # the snapshot taken at `start` wins: a plan.json edited after approval changes nothing
        it = approved[key]
    tgts = [x[4:] if x.startswith("new:") else x for x in [it.get("target", "")] + list(it.get("extra_targets") or []) if x]
    ok_paths = {rp(x) for x in tgts}
    if WIKI and any(in_zone(x, [WIKI]) for x in tgts):
        ok_paths |= wiki_side_files()
    return key, it, ok_paths


def require_active(run_id):
    lk = jload(LOCK, {})
    if lk.get("run_id") != run_id:
        sys.exit(f"run {run_id} is not the active run ({lk.get('run_id')})")


def sha256_of(path):
    import hashlib
    p = os.path.expanduser(path)
    return hashlib.sha256(open(p, "rb").read()).hexdigest() if os.path.isfile(p) else None


def record_intent(run_id, key, target):
    """BEFORE editing: keep the target's hash. If the session dies between the edit and `log`, the next start
    sees the target changed since the intent and flags the item `possibly_applied` (never apply twice)."""
    with open(os.path.join(run_dir(run_id), "intents.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"id": key, "target": rp(target), "sha256_before": sha256_of(target),
                            "ts": iso(now_utc())}, ensure_ascii=False) + "\n")


def flag_possibly_applied(approved):
    last = {}
    for p in sorted(glob.glob(os.path.join(STATE, "runs", "*", "intents.jsonl"))):
        for l in open(p, encoding="utf-8"):
            if l.strip():
                x = json.loads(l)
                if x["ts"] >= last.get(x["id"], {}).get("ts", ""):
                    last[x["id"]] = x
    for it in approved:
        x = last.get(f"{it['run_id']}/{it['id']}")
        if x and sha256_of(x["target"]) != x["sha256_before"]:
            it["possibly_applied"] = True
            it["intent"] = x


def cmd_intent(a):
    require_active(a.run_id)
    key, it, ok_paths = item_for(a.run_id, a.id)
    if rp(a.target) not in ok_paths:
        sys.exit(f"{a.target} is not a target of item {key} (targets: {', '.join(sorted(short(x) for x in ok_paths))})")
    record_intent(a.run_id, key, a.target)
    print(f"intent recorded: {key} -> {short(a.target)}")


def invalid_config(target, old_text, new_text):
    """Reason the patched file would break Claude Code, or None. Checks JSON files and SKILL.md frontmatter."""
    if target.endswith(".json"):
        try:
            json.loads(new_text)
        except ValueError as e:
            return f"not valid JSON: {e}"
    if os.path.basename(target) == "SKILL.md" and old_text.startswith("---"):
        m = re.match(r"---\r?\n(.*?)\r?\n---(\r?\n|$)", new_text, re.S)
        if not m:
            return "SKILL.md frontmatter is missing or not closed"
        for field in ("name", "description"):
            if not re.search(rf"^{field}\s*:", m.group(1), re.M):
                return f"SKILL.md frontmatter has no '{field}'"
    return None


def cmd_apply_patch(a):
    """Applies the item's {old, new} patch, exactly, with no interpretation: the text you approved is the text
    that goes in. `old` must appear once in the target; otherwise exit 4 and touch nothing."""
    require_active(a.run_id)
    key, it, ok_paths = item_for(a.run_id, a.id)
    patch = it.get("patch")
    if not isinstance(patch, dict) or not isinstance(patch.get("old"), str) or not isinstance(patch.get("new"), str):
        sys.exit(f"{key} has no 'patch' {{old, new}}. Apply it the normal way (edit + log)")
    target = os.path.expanduser(it.get("target", ""))
    allow = C.read_allowed(os.path.join(run_dir(a.run_id), "allowed-paths.txt"))
    if C.path_key(target) not in allow or rp(target) not in ok_paths:
        sys.exit(f"{short(target)} is not released in this run")
    # newline="": keep the file's own line endings (Windows would turn \n into \r\n on write)
    text = open(target, encoding="utf-8", newline="").read() if os.path.isfile(target) else None
    old, new = patch["old"], patch["new"]
    if text is not None and "\r\n" in text and "\r" not in old and old not in text:
        # a CRLF file (common on Windows) and a patch written with \n: match it in the file's own line endings
        old, new = old.replace("\n", "\r\n"), new.replace("\r\n", "\n").replace("\n", "\r\n")
    n = text.count(old) if text is not None else 0
    if n != 1:
        print(f"{key}: the 'old' text appears {n} time(s) in {short(target)}. The target changed; nothing was touched",
              file=sys.stderr)
        sys.exit(4)
    new_text = text.replace(old, new, 1)
    bad = invalid_config(target, text, new_text)
    if bad:
        sys.exit(f"{key}: the patched {short(target)} would be invalid ({bad}). Nothing was touched")
    record_intent(a.run_id, key, target)
    bak = do_backup(a.run_id, target)
    tmp = f"{target}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8", newline="") as fo:
        fo.write(new_text)
    os.replace(tmp, target)
    if open(target, encoding="utf-8", newline="").read() != new_text:
        sys.exit(f"{short(target)} does not match the patch. Restore it from {bak}")
    cmd_log(argparse.Namespace(run_id=a.run_id, id=key, target=target, backup=bak, removed=False,
                               superseded_by=None, summary=f"exact patch ({len(patch['old'])}->{len(patch['new'])} chars)"))


def cmd_log(a):
    key, it, ok_paths = item_for(a.run_id, a.id)
    rd = run_dir(a.run_id)
    if rp(a.target) not in ok_paths:
        sys.exit(f"{a.target} is not a target of item {key} (targets: {', '.join(sorted(short(x) for x in ok_paths))})")
    label = {"auto": "AUTO", "signoff": "APPROVED", "context": "ANSWERED"}.get(it.get("bucket"), "?")
    if a.superseded_by:
        # an old [x] that became a new item: close the old one (it won't come back, finish won't ask for
        # --allow-missing); the new item stays in the review
        if "/" not in a.superseded_by:
            sys.exit("--superseded-by needs <run_id>/<id> (e.g. 20260101-063222/S01)")
        label = "SUPERSEDED"
        a.summary = f"superseded by {a.superseded_by}: {a.summary}"
    elif not os.path.exists(os.path.expanduser(a.target)) and not a.removed:
        sys.exit(f"target {a.target} does not exist on disk. Check before logging (or use --removed)")
    digest = None
    if os.path.isfile(os.path.expanduser(a.target)):
        import hashlib
        digest = hashlib.sha256(open(os.path.expanduser(a.target), "rb").read()).hexdigest()[:16]
    os.makedirs(OUT, exist_ok=True)
    txt = open(CHANGELOG, encoding="utf-8").read() if os.path.exists(CHANGELOG) else \
        "# Change log (improve-system)\n\nOne line per applied change. Newest at the bottom. Local time.\n"
    head = f"## {datetime.now().strftime('%Y-%m-%d')}, run {a.run_id}"
    if head not in txt:
        txt = txt.rstrip("\n") + f"\n\n{head}\n\n"
    line = (f"- {datetime.now().strftime('%H:%M')} · {label} · {key} · `{short(a.target)}`: {a.summary}"
            + (f" (backup `{short(a.backup)}`)" if a.backup else "") + "\n")
    open(CHANGELOG, "w", encoding="utf-8").write(txt + line)
    with open(os.path.join(rd, "applied.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"id": key, "bucket": it.get("bucket"), "target": a.target, "summary": a.summary,
                            "backup": a.backup, "sha256_after": digest, "ts": iso(now_utc()),
                            **({"superseded_by": a.superseded_by} if a.superseded_by else {})},
                           ensure_ascii=False) + "\n")
    print(line.strip())


PLUGIN_SKILLS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HUMAN_REVIEW = os.environ.get("HUMAN_REVIEW_SCRIPT") or \
    os.path.join(PLUGIN_SKILLS, "human-review", "scripts", "human_review.py")


def launch_review():
    """End of run: open the review session (option launch_command) so you can decide, maybe from a phone."""
    if not os.path.exists(HUMAN_REVIEW):
        print("review: human-review not installed")
        return
    args = [sys.executable, HUMAN_REVIEW, "launch"] + (["--dry-run"] if os.environ.get("HUMAN_REVIEW_DRY_RUN") else [])
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=150)
        out = (r.stdout + r.stderr).strip() or "no output"
        # FAILED in capitals: the SKILL.md tells Claude to notify when it sees this
        print(f"review: FAILED: {out}" if r.returncode else out)
    except Exception as e:
        print(f"review: FAILED ({e})")


def cmd_finish(a):
    hist = read_history()
    if not any(e["event"] == "start" and e["run_id"] == a.run_id for e in hist):
        sys.exit(f"run {a.run_id} is not in the history")
    if any(e["event"] == "finish" and e["run_id"] == a.run_id for e in hist):
        sys.exit(f"run {a.run_id} is already closed")
    rd = run_dir(a.run_id)
    ap = os.path.join(rd, "applied.jsonl")
    applied = [json.loads(l) for l in open(ap, encoding="utf-8")] if os.path.exists(ap) else []
    done_ids = {x["id"] for x in applied}
    approved = jload(os.path.join(rd, "approved.json"), [])
    plan = jload(os.path.join(rd, "plan.json"), [])
    rejected_now = jload(os.path.join(rd, "rejected-now.json"), [])
    if not os.path.exists(os.path.join(rd, "render.json")):
        sys.exit(f"run `render {a.run_id}` before finish (an empty plan.json is fine). Without render, "
                 f"the [ ] items and unanswered questions don't move to the new review")
    missing_appr = [x for x in approved if f"{x['run_id']}/{x['id']}" not in done_ids]
    missing = [f"{x['run_id']}/{x['id']}" for x in missing_appr]
    missing += [f"{a.run_id}/{x['id']}" for x in plan if x["bucket"] == "auto" and f"{a.run_id}/{x['id']}" not in done_ids]
    if missing and not a.allow_missing:
        sys.exit("not in the change log: " + ", ".join(missing) +
                 "\nLog them with `log`, or close with --allow-missing --note 'why'.")
    consumed = jload(os.path.join(rd, "review-files.json"), [])
    # a review with an [x] that was not applied stays open: the next run reads the [x] again (what was
    # applied never repeats: resolved_ids)
    keep_open = {x.get("review_file") for x in missing_appr}
    # same lock as human_review `mark`: a mark lands either before this check (file stays open) or after
    # the finish event (mark refuses a consumed file). Nothing recorded in between is lost.
    os.makedirs(STATE, exist_ok=True)
    with open(os.path.join(STATE, "mark.lock"), "w", encoding="utf-8") as lk:
        C.lock_exclusive(lk)
        _finish_locked(a, rd, done_ids, plan, rejected_now, missing, keep_open, consumed)
    if not a.no_review and not jload(os.path.join(rd, "meta.json"), {}).get("apply_only"):
        launch_review()


def _finish_locked(a, rd, done_ids, plan, rejected_now, missing, keep_open, consumed):
    render = jload(os.path.join(rd, "render.json"), {})
    # a review file marked or answered after `start` read it stays open: the next run reads the new decision
    hashes = jload(os.path.join(rd, "review-hashes.json"), {})
    keep_open |= {f for f in consumed if f in hashes and sha256_of(f) != hashes[f]}
    consumed = [f for f in consumed if f not in keep_open]
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    for f in consumed:  # mark the old review as read (pending items were already copied to the new one)
        if os.path.exists(f):
            with open(f, "a", encoding="utf-8") as fo:
                fo.write(f"\n> Read on {stamp} by run `{a.run_id}`. New marks here no longer count. "
                         f"Use the newest review.\n")
    ev = {"event": "finish", "run_id": a.run_id, "finished_at": iso(now_utc()),
          "proposed": len(plan), "applied": sorted(done_ids),
          "auto": sum(1 for x in plan if x["bucket"] == "auto"),
          "signoff": sum(1 for x in plan if x["bucket"] == "signoff"),
          "context": sum(1 for x in plan if x["bucket"] == "context"),
          "rejected": [f"{x['run_id']}/{x['id']}" for x in rejected_now],
          "rejected_items": [x.get("title", x["id"]) for x in rejected_now],
          "consumed": consumed, "review": render.get("review"), "needs_context": render.get("needs_context"),
          "missing": missing, "kept_open": sorted(f for f in keep_open if f), "note": a.note or ""}
    append_history(ev)
    if jload(LOCK, {}).get("run_id") == a.run_id:
        os.remove(LOCK)
    print(json.dumps(ev, ensure_ascii=False, indent=1))


def do_abort(run_id, note):
    append_history({"event": "abort", "run_id": run_id, "at": iso(now_utc()), "note": note})
    if os.path.exists(LOCK):
        try:
            same = json.load(open(LOCK, encoding="utf-8")).get("run_id") == run_id
        except Exception:
            same = True  # unreadable lock: aborting is the way out of it
        if same:
            os.remove(LOCK)


def cmd_backup(a):
    """Backup of an allowed target BEFORE editing: <file>.bak-YYYYMMDDHHMMSS next to it.
    It exists because the guard blocks `cp` into a protected zone (the .bak is not in the allowlist)."""
    print(do_backup(a.run_id, a.path))


def do_backup(run_id, path):
    import shutil
    require_active(run_id)
    allow = C.read_allowed(os.path.join(run_dir(run_id), "allowed-paths.txt"))
    src = os.path.expanduser(path)
    if C.path_key(src) not in allow:
        sys.exit(f"{path} is not released in this run. Backup only works on an approved/AUTO target")
    if not os.path.isfile(src):
        sys.exit(f"{path} does not exist (a new target needs no backup)")
    stamp = datetime.now().strftime('%Y%m%d%H%M%S')
    if WIKI and in_zone(src, [WIKI]):  # a .bak inside the wiki pollutes search and the link graph
        bdir = os.path.join(STATE, "backups", "wiki", run_id)
        os.makedirs(bdir, exist_ok=True)
        dst = os.path.join(bdir, f"{os.path.basename(src)}.bak-{stamp}")
    else:
        dst = f"{src}.bak-{stamp}"
    if os.path.exists(dst):
        sys.exit(f"{dst} already exists. Wait 1 s and run again")
    shutil.copy2(src, dst)
    if open(src, "rb").read() != open(dst, "rb").read():
        sys.exit(f"backup {dst} does not match the original")
    return dst


def cmd_abort(a):
    """Abandoned run: release the lock without moving the window and without consuming reviews."""
    if not os.path.exists(LOCK):
        sys.exit("no active run")
    try:
        lk = json.load(open(LOCK, encoding="utf-8"))
    except Exception:
        if not a.corrupt_lock:
            sys.exit("unreadable lock. To unlock anyway: `abort RUN_ID --corrupt-lock` (user only)")
        lk = {"run_id": a.run_id}
    if lk.get("run_id") != a.run_id:
        sys.exit(f"the active run is {lk.get('run_id')}, not {a.run_id}. Nothing changed")
    do_abort(a.run_id, a.note or "")
    print(f"run {a.run_id} aborted; lock released; window and reviews unchanged")


def cmd_notify(a):
    """One line through notify_command. Not configured = print and exit 0 (never blocks a run)."""
    cmd = C.opt("notify_command")
    if not cmd:
        print(f"notify: not configured ({a.text})")
        return
    try:
        r = subprocess.run(C.split_command(cmd) + [a.text], capture_output=True, text=True, timeout=60)
        print(f"notify: {'sent' if r.returncode == 0 else 'failed: ' + (r.stderr or r.stdout).strip()[:200]}")
    except Exception as e:
        print(f"notify: failed ({e})")


def cmd_status(a):
    hist = read_history()
    if not hist:
        print(f"no runs yet (the first one covers the last 7 days). Reviews go to {short(OUT)}")
    for e in hist:
        ev = e.get("event")
        if ev == "start" and e.get("apply_only"):
            print(f"start  {e['run_id']}  (apply [x] only; window does not move)  approved={e.get('approved', 0)}")
        elif ev == "start":
            print(f"start  {e['run_id']}  {e.get('covered_from')} -> {e.get('covered_until')}  "
                  f"{e.get('sessions', '?')} sessions, {e.get('messages', '?')} messages")
        elif ev == "finish":
            print(f"finish {e['run_id']}  proposed={e.get('proposed')} applied={len(e.get('applied', []))} "
                  f"rejected={len(e.get('rejected', []))}")
        elif ev == "abort":
            print(f"abort  {e['run_id']}  {e.get('note', '')}")
    lk = jload(LOCK, None)
    if lk:
        age = (time.time() - lk.get("started_epoch", 0)) / 3600
        print(f"\nACTIVE run: {lk['run_id']} ({age:.1f} h old). The guard lock holds until `finish` or `abort {lk['run_id']}`.")
    prev = last_finished(hist)
    print(f"\nnext run covers from: {prev['covered_until'] if prev else '(no closed run: last 7 days)'}")
    print(f"wiki: {short(WIKI) if WIKI else 'off (option wiki_dir)'}")
    done = consumed_files(hist)
    pend = [f for f in sorted(glob.glob(os.path.join(OUT, "review-*.md")) +
                              glob.glob(os.path.join(OUT, "needs-context-*.md"))) if f not in done]
    print("reviews waiting for you:" if pend else "no review waiting.")
    known = file_labels()
    for f in pend:
        t = open(f, errors="ignore", encoding="utf-8").read()
        n_appr = len(re.findall(r"^- \[[xX]\]", t, re.M))
        n_rej = len(re.findall(r"^- \[-\]", t, re.M))
        n_open = len(re.findall(r"^- \[ \]", t, re.M))
        ans_re = regexes_for(f, known)[1]
        n_ans = sum(1 for m in map(ans_re.match, t.splitlines()) if m and m.group(1).strip())
        print(f"  {short(f)}  approved={n_appr} rejected={n_rej} open={n_open} answers={n_ans}")


def main():
    C.seed_config()  # first use: create the config file if missing (never overwrites)
    p = argparse.ArgumentParser(description="improve-system (self-improve plugin)")
    sp = p.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("start")
    s.add_argument("--since")
    s.add_argument("--days", type=int, default=7)
    s.add_argument("--force", action="store_true", help="abort the active run and open another")
    s.add_argument("--full-wiki", action="store_true", help="analyze the whole wiki in this run")
    s.add_argument("--apply-only", action="store_true",
                   help="only apply the [x] of the reviews (execute-reviewed): no sessions, window stays")
    r = sp.add_parser("render")
    r.add_argument("run_id")
    lg = sp.add_parser("log")
    lg.add_argument("run_id")
    lg.add_argument("--id", required=True, help="A01, S03 or <run_id>/S03 (approved item from an earlier run)")
    lg.add_argument("--target", required=True)
    lg.add_argument("--summary", required=True)
    lg.add_argument("--backup")
    lg.add_argument("--removed", action="store_true", help="the target was deleted/moved on purpose")
    lg.add_argument("--superseded-by", help="<run_id>/<id> of the new item that replaces this [x] (closes the old one)")
    f = sp.add_parser("finish")
    f.add_argument("run_id")
    f.add_argument("--note")
    f.add_argument("--allow-missing", action="store_true")
    f.add_argument("--no-review", action="store_true", help="don't launch human-review at the end")
    bk = sp.add_parser("backup")
    bk.add_argument("run_id")
    bk.add_argument("path")
    it_ = sp.add_parser("intent", help="BEFORE a manual edit: record the target's hash (no double apply)")
    it_.add_argument("run_id")
    it_.add_argument("--id", required=True)
    it_.add_argument("--target", required=True)
    apx = sp.add_parser("apply-patch", help="apply the item's exact {old,new} patch (backup + log)")
    apx.add_argument("run_id")
    apx.add_argument("--id", required=True)
    ab = sp.add_parser("abort")
    ab.add_argument("run_id")
    ab.add_argument("--note")
    ab.add_argument("--corrupt-lock", action="store_true", help="unlock an unreadable lock (user only)")
    wl = sp.add_parser("wait-lock", help="wait until start may run (wait_for_lock gone, pre_run_command not busy)")
    wl.add_argument("--max-minutes", type=float, help="give up after this long (default: option lock_wait_minutes)")
    wl.add_argument("--every", type=float, default=60, help="seconds between checks (default 60)")
    nt = sp.add_parser("notify", help="send one line through notify_command (no-op if not configured)")
    nt.add_argument("text")
    sp.add_parser("status")
    a = p.parse_args()
    {"start": cmd_start, "render": cmd_render, "log": cmd_log, "finish": cmd_finish, "status": cmd_status,
     "abort": cmd_abort, "backup": cmd_backup, "intent": cmd_intent, "apply-patch": cmd_apply_patch,
     "notify": cmd_notify, "wait-lock": cmd_wait_lock}[a.cmd](a)


if __name__ == "__main__":
    main()
