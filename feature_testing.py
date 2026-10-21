"""
Feature testing, from JIRA's Testing tickets ("[QA TESTING] - …").

Testers log each day's work in a Note panel in the ticket's description:

    Session 20
    Tester: Manya Gulati
    Date: <date>, 10:30 AM - 5:00 PM
    Build: 4.18.7-7c76931
    Test System: Proto-0022
    TEST NOTES
      1. Colour Calibration - Atlantis Research - Success!
         • Atlantis Run link → https://…
      2. Closed documentation tickets → PROD-7897, PROD-5240

This module finds the tickets with a JQL search, reads every session out of those panels
(date, testers, build, system, notes, linked PROD tickets), and treats clones of one ticket
("PT2 - …", "PT3 - …", made when a description fills up) as one test effort.

The admin's app (the one with the JIRA token) syncs this to a hidden tab of the sheet,
"Test Tracker Feature Testing", every few minutes; everyone reads it from there.
"""

import json, re
from datetime import datetime, timezone

import changelog

TAB = "Test Tracker Feature Testing"
JQL_KEY = "testing_jql"
DEFAULT_JQL = ('summary ~"[QA Testing]" AND (assignee=712020:b928dc8a-87ce-4da9-8a19-1008fde4ac16 OR '
               'assignee=712020:31f38293-6326-4293-b590-a30c90cefcbe OR '
               'assignee=712020:18bee935-c4ad-4a7f-9868-66f9266e0472 OR '
               'assignee=712020:46184db6-b6e8-4d2f-a4c3-4251420c5c3f) AND (status!=Done AND '
               'status!="Closed (Won\'t Do)") AND type=Testing')
SYNC_MINUTES = 10
CHUNK = 40000
KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
PART_RE = re.compile(r"^\s*PT\s*(\d+)\s*[-–:]\s*", re.I)


def jql(settings):
    return ((settings or {}).get(JQL_KEY) or "").strip() or DEFAULT_JQL

def effort_name(summary):
    """ "PT3 - [QA TESTING] - SIFT SWQA Exploratory Testing" → "SIFT SWQA Exploratory Testing"."""
    s = PART_RE.sub("", summary or "")
    s = re.sub(r"^\s*\[\s*qa\s*testing\s*\]\s*[-–:]?\s*", "", s, flags=re.I)
    return s.strip() or (summary or "").strip()

def part_of(summary):
    m = PART_RE.match(summary or "")
    return int(m.group(1)) if m else 1

def product_of(ticket):
    text = " ".join([ticket.get("summary", ""), " ".join(ticket.get("labels", []))]).lower()
    return "Viking" if "viking" in text or "vkg" in text else "VitalOne"


# ── reading the session panels ────────────────────────────────────────────────

def _text(node):
    """Plain text of an ADF node (dates as YYYY-MM-DD, smart links as their URL)."""
    t = node.get("type")
    if t == "text":
        return node.get("text", "")
    if t == "hardBreak":
        return "\n"
    if t == "date":
        try:
            ts = int(node["attrs"]["timestamp"]) / 1000
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        except (KeyError, ValueError, TypeError):
            return ""
    if t in ("inlineCard", "blockCard"):
        return node.get("attrs", {}).get("url", "")
    if t == "mention":
        return node.get("attrs", {}).get("text", "").lstrip("@")
    if t == "status":
        return node.get("attrs", {}).get("text", "")
    parts = [_text(c) for c in node.get("content", []) or []]
    if t in ("paragraph", "heading"):
        return "".join(parts)
    return "\n".join(p for p in parts if p)          # blocks: one per line

def _lines(node, depth=0, out=None):
    """The notes as lines, with list nesting kept as indentation."""
    out = [] if out is None else out
    t = node.get("type")
    if t in ("paragraph", "heading"):
        s = _text(node).strip()
        if s:
            out.append(("  " * max(depth - 1, 0)) + s)
        return out
    if t in ("bulletList", "orderedList"):
        for item in node.get("content", []) or []:
            first = True
            for ch in item.get("content", []) or []:
                if ch.get("type") in ("paragraph", "heading") and first:
                    s = _text(ch).strip()
                    if s:
                        out.append("  " * depth + "• " + s)
                    first = False
                else:
                    _lines(ch, depth + 1, out)
        return out
    for ch in node.get("content", []) or []:
        _lines(ch, depth, out)
    return out

def _keys(node, out=None):
    """PROD keys linked or mentioned in a node."""
    out = [] if out is None else out
    t = node.get("type")
    if t in ("inlineCard", "blockCard"):
        m = re.search(r"/browse/([A-Z][A-Z0-9]+-\d+)", node.get("attrs", {}).get("url", ""))
        if m:
            out.append(m.group(1))
    elif t == "text":
        out += KEY_RE.findall(node.get("text", ""))
        for mk in node.get("marks", []) or []:
            m = re.search(r"/browse/([A-Z][A-Z0-9]+-\d+)", (mk.get("attrs") or {}).get("href", ""))
            if m:
                out.append(m.group(1))
    for ch in node.get("content", []) or []:
        _keys(ch, out)
    return out

def sessions(description):
    """Every session panel in a ticket's description, in order."""
    out = []
    for node in (description or {}).get("content", []) or []:
        if node.get("type") != "panel":
            continue
        head_text, lines = "", _lines(node)
        full = _text(node)
        m = re.search(r"session\s*#?\s*(\d+)", full, re.I)
        info = {}
        for key, pat in (("testers", r"Testers?\s*:\s*([^\n]+?)(?=\s*Date\s*:|\n|$)"),
                         ("date", r"Date\s*:\s*(\d{4}-\d{2}-\d{2})"),
                         ("time", r"Date\s*:\s*\d{4}-\d{2}-\d{2}\s*,?\s*([^\n]*?)(?=\s*Build|\n|$)"),
                         ("build", r"(?:Build|SW)\s*:\s*([^\n]+?)(?=\s*Test System|\n|$)"),
                         ("system", r"Test System\s*:\s*([^\n]+)")):
            mm = re.search(pat, full, re.I)
            info[key] = mm.group(1).strip(" ,") if mm else ""
        # the notes: what comes after "TEST NOTES" (or after the header lines)
        notes, started = [], False
        for ln in lines:
            s = ln.strip()
            if re.match(r"test notes\s*:?\s*$", s, re.I):
                started = True
                continue
            if not started and (re.match(r"session\s*#?\s*\d+", s, re.I) or re.match(r"(tester|date|build|sw|test system)", s, re.I)):
                continue
            notes.append(ln)
        keys = list(dict.fromkeys(k for k in _keys(node) if k.startswith(changelog.JIRA_PROJECT + "-")))
        out.append({"n": int(m.group(1)) if m else len(out) + 1, "date": info["date"], "time": info["time"],
                    "testers": [t.strip() for t in re.split(r",|&| and ", info["testers"]) if t.strip()],
                    "build": info["build"], "system": info["system"], "notes": notes[:60], "bugs": keys})
    return out


# ── JIRA ──────────────────────────────────────────────────────────────────────

def fetch(query, progress=lambda m: None):
    """[ticket] for a JQL search, with their sessions."""
    import requests
    c = changelog.credentials()
    if not (c["jira_email"] and c["jira"]):
        raise changelog.ChangeLogError("Add your JIRA email and token first (Change Log → Tokens…).")
    tickets, token = [], None
    fields = ["summary", "status", "assignee", "created", "updated", "labels", "parent", "fixVersions",
              "description", "issuelinks"]
    while True:
        progress(f"Reading Testing tickets from JIRA ({len(tickets)} so far)…")
        body = {"jql": query, "maxResults": 50, "fields": fields}
        if token:
            body["nextPageToken"] = token
        try:
            r = requests.post(f"{changelog.JIRA_SITE}/rest/api/3/search/jql", auth=(c["jira_email"], c["jira"]),
                              headers={"Accept": "application/json", "Content-Type": "application/json"},
                              json=body, timeout=60)
        except requests.RequestException as exc:
            raise changelog.ChangeLogError(f"Couldn't reach JIRA ({type(exc).__name__}).")
        if r.status_code == 401:
            raise changelog.ChangeLogError("JIRA didn't accept the email/token.")
        if r.status_code >= 400:
            raise changelog.ChangeLogError(f"JIRA answered {r.status_code}: {r.text[:300]}")
        data = r.json()
        for it in data.get("issues", []):
            f = it["fields"]
            st = f.get("status") or {}
            tickets.append({
                "key": it["key"], "url": f"{changelog.JIRA_SITE}/browse/{it['key']}",
                "summary": f.get("summary") or "", "effort": effort_name(f.get("summary")),
                "part": part_of(f.get("summary")), "status": st.get("name", ""),
                "status_category": (st.get("statusCategory") or {}).get("key", ""),
                "assignee": (f.get("assignee") or {}).get("displayName", ""),
                "created": (f.get("created") or "")[:10], "updated": (f.get("updated") or "")[:10],
                "labels": f.get("labels") or [], "parent": (f.get("parent") or {}).get("key", ""),
                "parent_summary": ((f.get("parent") or {}).get("fields") or {}).get("summary", ""),
                "fix_versions": [v["name"] for v in f.get("fixVersions") or []],
                "sessions": sessions(f.get("description"))})
        token = data.get("nextPageToken")
        if not token or data.get("isLast", True) or len(tickets) >= 500:
            break
    for t in tickets:
        t["product"] = product_of(t)
    return tickets

def snapshot(query, progress=lambda m: None):
    return {"jql": query, "synced": datetime.now().isoformat(timespec="seconds"),
            "tickets": fetch(query, progress)}


# ── test efforts (clones of one ticket together) ──────────────────────────────

def efforts(testing):
    """[{name, product, tickets (PT1, PT2… order), status, assignees, sessions (newest last)}]"""
    groups = {}
    for t in (testing or {}).get("tickets", []):
        groups.setdefault(t["effort"].lower(), []).append(t)
    out = []
    for name, ts in groups.items():
        ts.sort(key=lambda t: t["part"])
        sess = [dict(s, ticket=t["key"], url=t["url"]) for t in ts for s in t["sessions"]]
        sess.sort(key=lambda s: (s["date"] or "", s["n"]))
        # the ticket being worked on: the one with the latest session (a fresh PT3 clone that's
        # still "Created" shouldn't hide that PT2 is In Progress), else the newest clone
        with_sessions = [t for t in ts if t["sessions"]]
        current = max(with_sessions, key=lambda t: max((s["date"] or "") for s in t["sessions"])) \
            if with_sessions else ts[-1]
        if with_sessions and current is not ts[-1] and ts[-1]["status_category"] == "indeterminate":
            current = ts[-1]
        out.append({"name": ts[0]["effort"], "product": current["product"], "tickets": ts,
                    "status": current["status"], "status_category": current["status_category"],
                    "assignees": list(dict.fromkeys(t["assignee"] for t in ts if t["assignee"])),
                    "testers": list(dict.fromkeys(x for s in sess for x in s["testers"])),
                    "sessions": sess, "last": next((s["date"] for s in reversed(sess) if s["date"]), ""),
                    "bugs": list(dict.fromkeys(k for s in sess for k in s["bugs"])), "url": current["url"],
                    "key": current["key"], "updated": max(t["updated"] for t in ts)})
    out.sort(key=lambda e: (e["last"] or e["updated"] or ""), reverse=True)
    return out

def sessions_in(testing, rng):
    """[(effort, session)] dated in rng (first day, last day), newest first."""
    out = []
    for e in efforts(testing):
        for s in e["sessions"]:
            if s["date"] and (rng is None or rng[0].isoformat() <= s["date"] <= rng[1].isoformat()):
                out.append((e, s))
    out.sort(key=lambda es: (es[1]["date"], es[1]["n"]), reverse=True)
    return out


# ── stored in the sheet (hidden tab) ──────────────────────────────────────────

def to_rows(snap):
    """Rows for the hidden tab: a header, then one row per ticket as JSON (split over cells)."""
    rows = [["key", "updated", "data"], ["_meta", snap["synced"], json.dumps({"jql": snap["jql"],
                                                                               "synced": snap["synced"]})]]
    for t in snap["tickets"]:
        text = json.dumps(t, separators=(",", ":"))
        rows.append([t["key"], t["updated"]] + [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)])
    return rows

def from_rows(rows):
    snap = {"jql": "", "synced": "", "tickets": []}
    for row in rows[1:]:
        if not row or not row[0]:
            continue
        try:
            body = json.loads("".join(row[2:]))
        except ValueError:
            continue
        if row[0] == "_meta":
            snap.update(body)
        else:
            snap["tickets"].append(body)
    return snap
