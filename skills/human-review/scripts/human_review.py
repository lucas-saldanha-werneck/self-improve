#!/usr/bin/env python3
"""human-review: your improve-system decisions as buttons (AskUserQuestion), recorded in the review files.

Subcommands:
  items                JSON {"open": [...], "approved": N}: SIGN-OFF [ ] items and unanswered CONTEXT questions
  mark --file F --ref RUN/ID --choice apply|reject|later [--note TXT]
  mark --file F --ref RUN/ID --answer TXT
  launch [--exec] [--dry-run]
                       opens a separate session through the option launch_command:
                       default = review session ("REVIEW DD-MM", prompt "/self-improve:human-review answer");
                       --exec = apply session ("EXEC DD-MM", prompt "/self-improve:execute-reviewed").
                       No launch_command = prints what to run and exits 0.
Reads and writes the same review-*.md / needs-context-*.md as improve_system.py (same settings).
"""
import argparse, json, os, re, shutil, subprocess, sys
from datetime import datetime

IS_DIR = os.environ.get("IMPROVE_SYSTEM_SCRIPTS") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "improve-system", "scripts")
sys.path.insert(0, IS_DIR)
import improve_system as isys  # noqa: E402
import si_config as C  # noqa: E402

PROMPTS = {"review": "/self-improve:human-review answer", "exec": "/self-improve:execute-reviewed"}
NAMES = {"review": "REVIEW", "exec": "EXEC"}
MARK = {"apply": "x", "reject": "-", "later": " "}
NOTE_RE = isys.NOTE_LINE_RE


def one_line(s):
    return " ".join(s.split()).replace("-->", "--&gt;")


def open_items():
    r = isys.parse_reviews(isys.read_history())  # (approved, rejected, pending, answers, open_q, files, …)
    approved, pending, open_q = r[0], r[2], r[4]

    def card(it, kind, f):
        return {"kind": kind, "ref": f"{it['run_id']}/{it['id']}", "id": it["id"], "file": f,
                "title": it.get("title", ""), "target": it.get("target", ""), "change": it.get("change", ""),
                "why": it.get("why", ""), "evidence": it.get("evidence", ""), "risk": it.get("risk", ""),
                "recommend": it.get("recommend", "apply"), "question": it.get("question", ""),
                "options": it.get("options", []), "notes": it.get("notes", []), "patch": it.get("patch")}

    return {"open": [card(i, "signoff", i["review_file"]) for i in pending] +
                    [card(q, "context", q["file"]) for q in open_q],
            "approved": len(approved)}


def cmd_items(a):
    print(json.dumps(open_items(), ensure_ascii=False, indent=1))


def cmd_mark(a):
    f = os.path.realpath(a.file)
    if not C.path_key(f).startswith(C.path_key(isys.OUT) + "/") or not os.path.isfile(f):
        sys.exit(f"{a.file} is not a file in {isys.OUT}")
    if (a.answer is None) == (a.choice is None):
        sys.exit("use --choice or --answer (one of them)")
    if "/" not in a.ref:
        sys.exit(f"--ref must be RUN/ID (got {a.ref!r})")
    os.makedirs(isys.STATE, exist_ok=True)
    with open(os.path.join(isys.STATE, "mark.lock"), "w", encoding="utf-8") as lk:  # 2 marks at once: one waits for the other
        C.lock_exclusive(lk)
        _mark_locked(a, f)


def _mark_locked(a, f):
    # a run already read this file: a new mark here would be lost (the parser skips consumed files)
    if f in {os.path.realpath(x) for x in isys.consumed_files(isys.read_history())}:
        sys.exit(f"{os.path.basename(f)} was already read by an improve-system run; run `items` again "
                 f"(the item is in the newest review)")
    rid, iid = a.ref.split("/", 1)
    tag = re.compile(r"<!--\s*" + re.escape(rid) + "/" + re.escape(iid) + r"\s*-->")
    lines = open(f, errors="ignore", encoding="utf-8").read().splitlines()
    idx = next((i for i, l in enumerate(lines) if tag.search(l)), None)
    if idx is None:
        sys.exit(f"{a.ref} not found in {a.file}")
    if a.answer is not None:
        if not isys.ANS_HEAD_RE.match(lines[idx]):
            sys.exit(f"{a.ref} is not a CONTEXT question")
        j = next((k for k in range(idx + 1, len(lines)) if isys.ANSWER_RE.match(lines[k])
                  or isys.ANS_HEAD_RE.match(lines[k])), None)
        if j is None or not isys.ANSWER_RE.match(lines[j]):
            sys.exit(f"{a.ref}: no **{isys.LABELS['answer']}:** line")
        k = j + 1  # an old answer may continue on the lines below
        while k < len(lines) and lines[k].strip() and not lines[k].startswith(("#", "---", ">")):
            k += 1
        lines[j:k] = [f"**{isys.LABELS['answer']}:** {one_line(a.answer)}"]
    else:
        if not isys.ITEM_RE.match(lines[idx]):
            sys.exit(f"{a.ref} is not a SIGN-OFF item")
        lines[idx] = re.sub(r"\[\s*[xX-]?\s*\]", f"[{MARK[a.choice]}]", lines[idx], count=1)
        end = idx + 1
        while end < len(lines) and lines[end].startswith(" "):
            end += 1
        block = [l for l in lines[idx + 1:end] if not NOTE_RE.match(l)]
        if a.note:
            block.append(f"    - {isys.LABELS['note']}: {one_line(a.note)}")
        lines[idx + 1:end] = block
    tmp = os.path.join(os.path.dirname(f), f".{os.path.basename(f)}.tmp-{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as fo:  # whole file or nothing: a crash halfway does not truncate the review
        fo.write("\n".join(lines) + "\n")
        fo.flush()
        os.fsync(fo.fileno())
    os.replace(tmp, f)
    print(json.dumps({"ref": a.ref, "file": f, "ok": True}, ensure_ascii=False))


def open_session(prefix):
    """Name of an open session whose name starts with prefix (any day), or None. Two REVIEW sessions on
    the same files would fight. Uses `claude agents --json`; if that fails, assume none is open."""
    raw = os.environ.get("HUMAN_REVIEW_AGENTS_JSON")
    try:
        if raw is None:
            custom = C.opt("claude_command")  # e.g. a pinned stable copy of claude
            exe = C.split_command(custom) if custom else ([shutil.which("claude")] if shutil.which("claude") else None)
            if not exe:
                return None
            raw = subprocess.run(exe + ["agents", "--json"], capture_output=True, text=True, timeout=20).stdout
        return next((x["name"] for x in json.loads(raw or "[]")
                     if x.get("kind") == "interactive" and str(x.get("name", "")).startswith(prefix + " ")), None)
    except Exception:
        return None  # can't tell: open it (worst case 2 sessions; mark has a lock)


def cmd_launch(a):
    kind = "exec" if a.exec else "review"
    d = open_items()
    n = d["approved"] if kind == "exec" else len(d["open"]) + d["approved"]
    if not n:
        print(f"{kind}: nothing pending")
        return
    name = f"{NAMES[kind]} {datetime.now():%d-%m}"
    template = os.environ.get("HUMAN_REVIEW_LAUNCH") or C.opt("launch_command")
    if not template:
        what = "say \"apply what I approved\" or run /self-improve:execute-reviewed" if kind == "exec" \
            else "run /self-improve:human-review in any session"
        print(f"{kind}: {n} item(s) waiting. No launch_command configured: {what}")
        return
    already = open_session(NAMES[kind])
    if already:
        print(f"{kind}: {already} already open")
        return
    try:
        parts = C.split_command(template)
    except ValueError as e:
        print(f"{kind}: launch_command can't be parsed ({e})")
        sys.exit(1)
    cmd = [p.replace("{name}", name).replace("{prompt}", PROMPTS[kind]) for p in parts]
    if a.dry_run:
        cmd.append("--dry-run")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except Exception as e:
        print(f"{kind}: failed to open {name}: {e}")
        sys.exit(1)
    if r.stdout.strip():
        print(r.stdout.strip())
    if r.returncode:
        print(f"{kind}: failed to open {name}: {r.stderr.strip()}")
        sys.exit(1)
    print(f"{kind}: opened {name} ({n} items)")


def main():
    p = argparse.ArgumentParser(description="human-review (self-improve plugin)")
    sp = p.add_subparsers(dest="cmd", required=True)
    l = sp.add_parser("launch")
    l.add_argument("--exec", action="store_true", help="open the apply session instead of the review session")
    l.add_argument("--dry-run", action="store_true", help="pass --dry-run to launch_command")
    sp.add_parser("items")
    m = sp.add_parser("mark")
    m.add_argument("--file", required=True)
    m.add_argument("--ref", required=True, help="RUN/ID, e.g. 20260101-181058/S03")
    m.add_argument("--choice", choices=sorted(MARK))
    m.add_argument("--note")
    m.add_argument("--answer")
    a = p.parse_args()
    {"launch": cmd_launch, "items": cmd_items, "mark": cmd_mark}[a.cmd](a)


if __name__ == "__main__":
    main()
