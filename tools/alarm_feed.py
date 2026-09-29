#!/usr/bin/env python3
"""
Publish a due-date calendar with alarms in it — something Canvas itself refuses to do.

Two sources, same output:
  python3 alarm_feed.py --feed <canvas user .ics url> --out due.ics [--exclude "Leeds,Orientation"]
      straight from Canvas's public calendar feed (no login; what a student can hand over in a link)
  python3 alarm_feed.py --items items.json --core core.json --out due.ics
      from a Sko Board's data — the Canvas items PLUS the ones read out of each syllabus that Canvas
      never put on its calendar, with any dates Claude corrected (in-class exams at their real hour)

Why: Canvas's feed carries no VALARM blocks, and iOS will not let you add alerts to a subscribed
calendar — on a subscription, alerts come from the publisher. So we publish a copy that has them.

Alarms (per event, as absolute times, so they behave the same on every phone):
  1. 5:00 PM local on the day BEFORE it's due
  2. 11:00 PM local on the day it's due — or, when it's due earlier than that (an exam at 12:30 PM,
     a midterm at 6:30 PM), one hour before it starts instead
Canvas sends assignments as all-day dates; those become 11:59 PM local, the real deadline, so the
alarms land where you'd expect.
"""
import argparse
import datetime as dt
import json
import re
import urllib.request
from zoneinfo import ZoneInfo

ap = argparse.ArgumentParser()
src = ap.add_mutually_exclusive_group(required=True)
src.add_argument("--feed", help="Canvas user calendar .ics URL")
src.add_argument("--items", help="board items.json (data/items)")
ap.add_argument("--core", help="board core.json (data/core) — exam times and course labels")
ap.add_argument("--extras", help="feed mode only: JSON list of board-format items to add or override (syllabus items, corrected times)")
ap.add_argument("--out", required=True)
ap.add_argument("--tz", default="America/Denver")
ap.add_argument("--exclude", default="", help="comma-separated course-name fragments to drop")
ap.add_argument("--name", default="Classes (due dates)")
ap.add_argument("--before", default="17:00", help="HH:MM local, the day before")
ap.add_argument("--dayof", default="23:00", help="HH:MM local, the day it's due")
a = ap.parse_args()

TZ = ZoneInfo(a.tz)
DROP = [x.strip().lower() for x in a.exclude.split(",") if x.strip()]
H1, M1 = map(int, a.before.split(":"))
H2, M2 = map(int, a.dayof.split(":"))


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "sko-alarm-feed/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")


def fold(line):
    out, b = [], line.encode("utf-8")
    while len(b) > 73:
        cut = 73
        while cut > 0 and (b[cut] & 0xC0) == 0x80:
            cut -= 1
        out.append(b[:cut].decode("utf-8"))
        b = b" " + b[cut:]
    out.append(b.decode("utf-8"))
    return "\r\n".join(out)


def esc(s):
    return str(s or "").replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")


def unesc(s):
    """Undo iCalendar text escaping on a value read from the feed, so esc() doesn't double it on the way out."""
    return re.sub(r"\\([\\,;nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), s or "")


def z(d):
    return d.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def short_course(code):
    m = re.match(r"\s*([A-Za-z]+)\s*(\d{3,4})", code)
    return f"{m.group(1).upper()} {m.group(2)}" if m else code.strip()[:18]


def alarms_for(due):
    """Absolute alarm instants for one deadline (a tz-aware datetime)."""
    local = due.astimezone(TZ)
    day_before = (local - dt.timedelta(days=1)).replace(hour=H1, minute=M1, second=0, microsecond=0)
    day_of = local.replace(hour=H2, minute=M2, second=0, microsecond=0)
    if day_of >= local - dt.timedelta(minutes=30):       # due before the evening: warn an hour ahead instead
        day_of = local - dt.timedelta(hours=1)
    return [day_before, day_of]


# ---------- collect events as plain dicts ----------
events = []
if a.feed:
    raw = fetch(a.feed)
    text = raw.replace("\r\n ", "").replace("\n ", "").replace("\r\n", "\n")
    for block in re.findall(r"BEGIN:VEVENT\n(.*?)\nEND:VEVENT", text, re.S):
        f = {}
        for ln in block.split("\n"):
            m = re.match(r"^([A-Z\-]+)((?:;[^:]*)?):(.*)$", ln)
            if m:
                f[m.group(1)] = m.group(3)
        summary = unesc(f.get("SUMMARY", ""))
        cm = re.search(r"\[([^\]]+)\]\s*$", summary)
        course = cm.group(1).strip() if cm else ""
        title = summary[: cm.start()].strip() if cm else summary.strip()
        v = f.get("DTSTART", "").strip()
        if re.fullmatch(r"\d{8}", v):
            due = dt.datetime.strptime(v, "%Y%m%d").replace(hour=23, minute=59, tzinfo=TZ)
        elif re.fullmatch(r"\d{8}T\d{6}Z", v):
            due = dt.datetime.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
        elif re.fullmatch(r"\d{8}T\d{6}", v):
            due = dt.datetime.strptime(v, "%Y%m%dT%H%M%S").replace(tzinfo=TZ)
        else:
            continue
        desc = unesc(f.get("DESCRIPTION", "")).strip()
        events.append({"uid": f.get("UID", ""), "course": course, "short": short_course(course), "title": title,
                       "due": due, "url": f.get("URL", ""), "location": unesc(f.get("LOCATION", "")), "desc": desc[:700]})
    if a.extras:
        by_uid = {e["uid"]: e for e in events}
        for it in json.load(open(a.extras)):
            if not it.get("dueAt"):
                continue
            iid = str(it["id"]); m = re.match(r"^[aq](\d+)$", iid)
            uid = f"event-assignment-{m.group(1)}" if m else f"sko-{iid}"
            src = it.get("source") or {}
            note = ("From the syllabus — not on the Canvas calendar. " + (src.get("label") or "")) if src.get("kind") == "found" \
                   else (src.get("label") or "") if src.get("kind") == "corrected" else ""
            base = by_uid.get(uid, {})
            ev = {"uid": uid, "course": it.get("course") or base.get("course", ""),
                  "short": it.get("short") or base.get("short") or short_course(it.get("course") or ""),
                  "title": it.get("title") or base.get("title", ""),
                  "due": dt.datetime.fromisoformat(it["dueAt"].replace("Z", "+00:00")),
                  "url": it.get("url") or base.get("url", ""), "location": it.get("location") or base.get("location", ""),
                  "desc": " ".join(x for x in [note, (it.get("desc") or base.get("desc") or "")[:500]] if x).strip(),
                  # optional explicit alarm instants (ISO, UTC) — replaces the computed pair; used for tests and one-offs
                  "alarms": [dt.datetime.fromisoformat(x.replace("Z", "+00:00")) for x in (it.get("alarmsAt") or [])]}
            if uid in by_uid:
                events[events.index(by_uid[uid])] = ev
            else:
                events.append(ev)
            by_uid[uid] = ev
        # let the extras' course labels (MKTG, ACCT...) win over the raw "BUSM 2010" so one calendar reads one way
        labels = {}
        for it in json.load(open(a.extras)):
            if it.get("course") and it.get("short"):
                labels[it["course"].strip().lower()] = it["short"]
        for e in events:
            for code, sh in labels.items():
                if e["course"].strip().lower().startswith(code):
                    e["short"] = sh
else:
    items = json.load(open(a.items))["items"]
    core = json.load(open(a.core)) if a.core else {}
    labels = {c["code"]: c.get("short") or short_course(c["code"]) for c in core.get("courses", [])}
    exam_place = {}
    for e in core.get("exams", []):
        exam_place[e["id"]] = e.get("time") or ""
    for it in items:
        if it.get("kind") not in ("todo",) or not it.get("dueAt") or it.get("published") is False:
            continue
        due = dt.datetime.fromisoformat(it["dueAt"].replace("Z", "+00:00"))
        src_kind = (it.get("source") or {}).get("kind")
        note = ""
        if src_kind == "found":
            note = "From the syllabus — not on the Canvas calendar. " + ((it.get("source") or {}).get("label") or "")
        elif src_kind == "corrected":
            note = (it.get("source") or {}).get("label") or "Time corrected from the syllabus."
        place = exam_place.get(it.get("examId") or "", "")
        # keep Canvas's own UIDs for Canvas items so a feed-built and a board-built calendar agree event for event
        iid = str(it["id"]); m = re.match(r"^[aq](\d+)$", iid)
        uid = f"event-assignment-{m.group(1)}" if m else f"sko-{iid}"
        events.append({"uid": uid, "course": it["course"], "short": labels.get(it["course"], short_course(it["course"])),
                       "title": it["title"], "due": due, "url": it.get("url") or "", "location": place,
                       "desc": " ".join(x for x in [note, (it.get("desc") or "")[:500]] if x).strip()})

# ---------- write ----------
out = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//sko board//canvas alarms//EN", "CALSCALE:GREGORIAN",
       "METHOD:PUBLISH", f"X-WR-CALNAME:{esc(a.name)}", f"X-WR-TIMEZONE:{a.tz}",
       "X-PUBLISHED-TTL:PT1H", "REFRESH-INTERVAL;VALUE=DURATION:PT1H"]
now = dt.datetime.now(dt.timezone.utc)
kept = dropped = 0
for e in sorted(events, key=lambda x: x["due"]):
    if any(d in e["course"].lower() or d in e["title"].lower() for d in DROP):
        dropped += 1
        continue
    label = f"{e['short']} · {e['title']}" if e["short"] else e["title"]
    body = " \\n\\n".join(x for x in [esc(e["desc"]), esc(e["url"])] if x)
    ev = ["BEGIN:VEVENT", f"UID:{e['uid']}", f"DTSTAMP:{z(now)}", f"DTSTART:{z(e['due'])}", f"DTEND:{z(e['due'])}",
          f"SUMMARY:{esc(label)}"]
    if e["url"]:
        ev.append(f"URL;VALUE=URI:{e['url']}")
    if e["location"]:
        ev.append(f"LOCATION:{esc(e['location'])}")
    if body:
        ev.append(f"DESCRIPTION:{body}")
    for when in (e.get("alarms") or alarms_for(e["due"])):
        ev += ["BEGIN:VALARM", "ACTION:DISPLAY", f"TRIGGER;VALUE=DATE-TIME:{z(when)}",
               f"DESCRIPTION:{esc(label)}", "END:VALARM"]
    ev.append("END:VEVENT")
    out += ev
    kept += 1
out.append("END:VCALENDAR")
open(a.out, "w", newline="").write("\r\n".join(fold(l) for l in out) + "\r\n")
print(f"{kept} events, 2 alarms each ({a.before} day before, {a.dayof} day of / 1h before a timed one); {dropped} dropped -> {a.out}")
