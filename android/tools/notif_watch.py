#!/usr/bin/env python3
"""Watch the SOSForge notifications on an emulator, and assert what a drill must produce.

  notif_watch.py <serial> watch <out.jsonl> [--seconds N] [--every 0.5]
      samples `dumpsys notification --noredact` and appends one JSON line per CHANGE:
      {"t": ..., "op": "post" | "update" | "remove", "id": ..., "channel": ..., "title": ..., "text": ...,
       "when": ..., "importance": ...}
  notif_watch.py <serial> now
      prints the notifications standing right now (one JSON per line)
  notif_watch.py summarize <out.jsonl>
      per notification id: how many posts, updates, removes, its channels and titles over time

Run every adb call under ~/dev-context/tools/adb-file/adb_file.py (the per-serial lock). Read-only on the
device: it never posts, cancels or changes anything.
"""
import json
import re
import subprocess
import sys
import time

PKG = "co.soclose.sosforge"  # --pkg co.soclose.sosforge.debug for the drills' debug build
REC = re.compile(r"NotificationRecord\(0x[0-9a-f]+: pkg=(\S+) user=\S+ id=(-?\d+) tag=(\S+) importance=(-?\d+)")


def dump(serial):
    out = subprocess.run(["adb", "-s", serial, "shell", "dumpsys", "notification", "--noredact"], capture_output=True, text=True, timeout=60).stdout
    return parse(out)


def section(lines, title):
    """The lines of one top-level dumpsys section (two-space indent), up to the next one.

    The other sections (Snoozed, Ranking Config, Group summaries, ...) also name NotificationRecords; read
    together, a snoozed or archived record looked like a standing notification."""
    out, inside = [], False
    for ln in lines:
        top = re.match(r"^  \S.*:$", ln)
        if top:
            inside = ln.strip() == title
            continue
        if inside:
            out.append(ln)
    return out


def parse(out):
    """The standing notifications of PKG, keyed by id, from the 'Notification List' section only."""
    lines = section(out.splitlines(), "Notification List:")
    res = {}
    cur = None
    in_extras = False
    for ln in lines:
        m = REC.search(ln)
        if m:
            cur = None
            if m.group(1) == PKG:
                nid = int(m.group(2))
                if nid in res:
                    # the same record is dumped again further down (ranking, history): keep the first
                    cur = None
                    continue
                ch = re.search(r"channel=(\S+)", ln)
                res[nid] = {"id": nid, "importance": int(m.group(4)), "channel": ch.group(1) if ch else None,
                            "title": None, "text": None, "when": None}
                cur = res[nid]
            in_extras = False
            continue
        if cur is None:
            continue
        s = ln.strip()
        if s.startswith("extras={"):
            in_extras = True
            continue
        if in_extras and s == "}":
            in_extras = False
            continue
        if in_extras:
            kv = re.match(r"(android\.\w+)=\S+ \((.*)\)$", s)
            if kv and kv.group(1) == "android.title" and cur["title"] is None:
                cur["title"] = kv.group(2)
            elif kv and kv.group(1) == "android.text" and cur["text"] is None:
                cur["text"] = kv.group(2)
        w = re.search(r"\bwhen=(\d+)", s)
        if w and cur["when"] is None:
            cur["when"] = int(w.group(1))
    return res


def watch(serial, out_path, seconds, every):
    prev = {}
    end = time.time() + seconds
    with open(out_path, "a") as f:
        while time.time() < end:
            try:
                now = dump(serial)
            except Exception as e:  # a dumpsys hiccup must not end the watch
                f.write(json.dumps({"t": time.time(), "op": "error", "error": str(e)}) + "\n")
                time.sleep(every)
                continue
            t = time.time()
            for nid, n in now.items():
                if nid not in prev:
                    f.write(json.dumps({"t": t, "op": "post", **n}, ensure_ascii=False) + "\n")
                elif (n["title"], n["text"], n["channel"], n["when"]) != (prev[nid]["title"], prev[nid]["text"], prev[nid]["channel"], prev[nid]["when"]):
                    f.write(json.dumps({"t": t, "op": "update", **n}, ensure_ascii=False) + "\n")
            for nid, n in prev.items():
                if nid not in now:
                    f.write(json.dumps({"t": t, "op": "remove", **n}, ensure_ascii=False) + "\n")
            f.flush()
            prev = now
            time.sleep(every)


def summarize(path):
    per = {}
    for ln in open(path):
        e = json.loads(ln)
        if e.get("op") == "error":
            continue
        p = per.setdefault(e["id"], {"posts": 0, "updates": 0, "removes": 0, "history": []})
        p[{"post": "posts", "update": "updates", "remove": "removes"}[e["op"]]] += 1
        p["history"].append(f'{e["op"]}:{e["channel"]}:{e["title"]}')
    for nid, p in per.items():
        print(json.dumps({"id": nid, **p}, ensure_ascii=False))


def main():
    global PKG
    a = sys.argv[1:]
    if "--pkg" in a:
        i = a.index("--pkg")
        PKG = a[i + 1]
        a = a[:i] + a[i + 2:]
    if a and a[0] == "summarize":
        return summarize(a[1])
    serial, cmd = a[0], a[1]
    if cmd == "now":
        for n in dump(serial).values():
            print(json.dumps(n, ensure_ascii=False))
    elif cmd == "watch":
        seconds = float(a[a.index("--seconds") + 1]) if "--seconds" in a else 120.0
        every = float(a[a.index("--every") + 1]) if "--every" in a else 0.5
        watch(serial, a[2], seconds, every)
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
