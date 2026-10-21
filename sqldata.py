"""
The sheet as a small SQL database (SQLite, in memory), so a chart can be any query.

Rebuilt for each date range the dashboard or export shows (it takes a few ms). It has:

  • one table per tab, named like the tab ("VitalOne End-to-End Tracker"), with the
    sheet's columns by header name, plus:
        _tab, _row   which tab and sheet row a row came from (for click-through)
        "<column> link"   for columns that hold links (e.g. "Run Name w/ Link link")
    Date columns are stored as YYYY-MM-DD so they sort and compare.

  • ready-made views with friendly column names (plus every sheet column of those tabs):
        runs         every end-to-end run: tracker, run_number, run_name, run_link, run_date,
                     status, status_class (done/active/blocked/…), failed (1/0), platform
        issues       failed or not, each repeated issue ("Continued from Run#") once, for the
                     runs in the dates on screen; the newest run's diagnosis wins; repeats = runs in it
        all_issues   the same, for all dates
        resolutions  every failed issue in the dates on screen, with carry-over like the
                     Error Analysis: first_date, resolved_date, days_to_resolve, resolved (1/0),
                     subsystem, component, carried_over, notes (from its first run)
        bugs         the bug tab: key, key_link, summary, status, status_class, assignee,
                     created, done, product (VitalOne / Viking), is_open, in_qa
        testing      JIRA Testing tickets (feature testing): key, effort, part, status, assignee,
                     sessions, last_session, bugs
        test_sessions  one row per logged test session: key, effort, product, session, date,
                     testers, build, system, notes, bugs

  • parameters for the dates on screen: :start and :end (YYYY-MM-DD; all dates when none
    is picked) — e.g.  WHERE run_date BETWEEN :start AND :end
"""

import re, sqlite3, time
from datetime import date

import charts
import tracker_store as store

ALL_START, ALL_END = "0001-01-01", "9999-12-31"


def ident(name):
    return '"' + str(name).replace('"', '""') + '"'

def iso(value):
    d = store.parse_date(value) if isinstance(value, str) else value
    return d.isoformat() if d else None

def short_name(title):
    return title.replace(" End-to-End Tracker", "")


class Database:
    def __init__(self, data, rng=None, row_filter=None):
        self.data, self.rng = data, rng
        self.row_filter = row_filter
        self.conn = sqlite3.connect(":memory:")
        self.conn.create_function("REGEXP", 2, lambda p, s: bool(s and re.search(p, s, re.I)))
        self.rows_by_key = {}            # (tab title, sheet row) → row, for click-through
        self.tables, self.views = [], {}
        self._build()

    # ── building ──────────────────────────────────────────────────────────────

    def _rows(self, tab):
        rows = [r for r in tab["rows"] if not store.is_blank(r)]
        return [r for r in rows if self.row_filter(r)] if self.row_filter else rows

    def _in_range(self, tab, rows):
        dcol = store.date_column(tab)
        if self.rng is None or dcol is None:
            return rows
        return [r for r in rows if store.in_range(r["values"][dcol["pos"]], self.rng)]

    @staticmethod
    def _sheet_columns(tab, taken=()):
        """[(sql name, column, is link)] — the sheet's columns, plus a link column where
        a column holds links. Names are made unique (case-insensitively)."""
        out, seen = [], {t.lower() for t in taken}
        for c in tab["columns"]:
            for name, link in ((c["name"].strip(), False), (c["name"].strip() + " link", True)):
                if link and not c.get("link"):
                    continue
                n, k = name, 2
                while n.lower() in seen:
                    n, k = f"{name} ({k})", k + 1
                seen.add(n.lower())
                out.append((n, c, link))
        return out

    @staticmethod
    def _cell(r, c, link):
        if link:
            return r["links"][c["pos"]] or None
        v = r["values"][c["pos"]]
        if c["kind"] == "date":
            return iso(v) or (v or None)
        if c["kind"] == "number":
            try:
                return float(v.replace(",", "")) if v.strip() else None
            except ValueError:
                return v
        return v if v != "" else None

    def _create(self, name, columns, rows):
        cols = ", ".join(ident(c) for c in columns)
        self.conn.execute(f"CREATE TABLE {ident(name)} ({cols})")
        if rows:
            marks = ", ".join("?" * len(columns))
            self.conn.executemany(f"INSERT INTO {ident(name)} VALUES ({marks})", rows)

    def _build(self):
        tabs = self.data.get("tabs", [])
        for tab in tabs:                                     # 1. one table per tab
            cols = self._sheet_columns(tab, ("_tab", "_row"))
            rows = []
            for r in self._rows(tab):
                self.rows_by_key[(tab["title"], r["r"])] = r
                rows.append([tab["title"], r["r"]] + [self._cell(r, c, l) for _, c, l in cols])
            self._create(tab["title"], ["_tab", "_row"] + [n for n, _, _ in cols], rows)
            self.tables.append(tab["title"])

        run_tabs = [t for t in tabs if store.is_run_tab(t)]
        RUN = ["tracker", "_tab", "_row", "run_number", "run_name", "run_link", "run_date",
               "status", "status_class", "failed", "platform"]

        def run_values(tab, r):
            name = store.run_name_column(tab)
            st = store.row_status(tab, r)
            dcol = store.date_column(tab)
            return [short_name(tab["title"]), tab["title"], r["r"], r["values"][tab["columns"][0]["pos"]],
                    r["values"][name["pos"]] if name else None, r["links"][name["pos"]] if name else None,
                    iso(r["values"][dcol["pos"]]) if dcol else None, st, charts.classify(st),
                    int(charts.classify(st) in store.FAILED), store.row_platform(tab, r) or None]

        # every sheet column of the run tabs, by name (missing ones are empty)
        extra, seen = [], {c.lower() for c in RUN}
        for tab in run_tabs:
            for n, c, l in self._sheet_columns(tab):
                if n.lower() not in seen:
                    seen.add(n.lower())
                    extra.append(n)

        def with_extra(tab, r, base):
            cells = {n.lower(): self._cell(r, c, l) for n, c, l in self._sheet_columns(tab)}
            return base + [cells.get(n.lower()) for n in extra]

        rows = [with_extra(t, r, run_values(t, r)) for t in run_tabs for r in self._rows(t)]
        self._create("runs", RUN + extra, rows)               # 2. runs
        self.views["runs"] = RUN + extra

        for name, pick in (("issues", lambda t: self._in_range(t, self._rows(t))),
                           ("all_issues", lambda t: self._rows(t))):     # 3. issues
            rows = []
            for t in run_tabs:
                for r in store.issues(t, pick(t)):
                    for x in r.get("_runs", [r]):
                        self.rows_by_key.setdefault((t["title"], x["r"]), x)
                    rows.append(with_extra(t, r, run_values(t, r)) + [len(r.get("_runs", [r]))])
            self._create(name, RUN + extra + ["repeats"], rows)
            self.views[name] = RUN + extra + ["repeats"]

        RES = ["tracker", "_tab", "_row", "run_number", "run_name", "run_link", "first_date",
               "resolved_date", "days_to_resolve", "resolved", "subsystem", "component",
               "carried_over", "notes", "repeats"]
        rows = []
        for t in run_tabs + [t for t in tabs if store.is_ticket_tab(t)]:   # 4. resolutions
            notes_col = next((c for c in t["columns"] if store._words(c["name"]) & {"note", "notes"}), None)
            name = store.run_name_column(t) or next(
                (c for c in t["columns"] if any(r["links"][c["pos"]] for r in t["rows"])), t["columns"][0])
            ticket = not store.is_run_tab(t)
            number = name if ticket else t["columns"][0]          # tickets: the key, then the summary
            title_col = next((c for c in t["columns"] if "summary" in c["name"].lower()), name) if ticket else name
            for e in store.resolutions(t, self._rows(t), self.rng):
                first = e[0].get("_runs", [e[0]])[0]
                rows.append([short_name(t["title"]), t["title"], first["r"],
                             first["values"][number["pos"]], first["values"][title_col["pos"]],
                             first["links"][name["pos"]] or None, iso(e[3]),
                             iso(e[1]) if e[1] and e[1] != date.min else None, e[2],
                             int(e[1] is not None), e[4] or None, e[5] or None, int(bool(e[6])),
                             first["values"][notes_col["pos"]] if notes_col else None,
                             len(e[0].get("_runs", [e[0]]))])
        self._create("resolutions", RES, rows)
        self.views["resolutions"] = RES

        BUG = ["_tab", "_row", "key", "key_link", "summary", "status", "status_class", "assignee",
               "created", "done", "product", "is_open", "in_qa"]
        rows = []
        for t in [t for t in tabs if store.is_bug_tab(t)]:              # 5. bugs
            import layout as lay
            groups = lay.split_groups(t, {"split": "viking"}) or []
            viking = next((test for n, test in groups if n == "Viking"), lambda r: False)
            key = next((c for c in t["columns"] if any(r["links"][c["pos"]] for r in t["rows"])), t["columns"][0])
            find = lambda w: next((c for c in t["columns"] if w in c["name"].lower()), None)
            summ, who = find("summary"), find("assign")
            made, done = store.created_date_column(t), store.done_date_column(t)
            for r in self._rows(t):
                st = store.row_status(t, r)
                cls = charts.classify(st)
                finished = cls == "done" or bool(done and store.parse_date(r["values"][done["pos"]]))
                v = lambda c: r["values"][c["pos"]] if c else None
                rows.append([t["title"], r["r"], v(key), r["links"][key["pos"]] or None, v(summ), st, cls,
                             v(who), iso(v(made)) if made else None, iso(v(done)) if done else None,
                             "Viking" if viking(r) else "VitalOne", int(not finished),
                             int(not finished and "qa" in store._words(st))])
        self._create("bugs", BUG, rows)
        self.views["bugs"] = BUG

        import feature_testing                                           # 6. feature testing (JIRA)
        testing = self.data.get("testing") or {}
        T = ["key", "url", "effort", "part", "summary", "product", "status", "status_category", "assignee",
             "created", "updated", "sessions", "last_session", "bugs"]
        S = ["key", "url", "effort", "product", "session", "date", "time", "testers", "build", "system",
             "notes", "bugs", "hw_requests"]
        trows, srows = [], []
        for t in testing.get("tickets", []):
            ss = t.get("sessions", [])
            trows.append([t["key"], t["url"], t["effort"], t["part"], t["summary"], t.get("product"), t["status"],
                          t["status_category"], t["assignee"], t["created"], t["updated"], len(ss),
                          max((x["date"] for x in ss if x["date"]), default=None),
                          ", ".join(dict.fromkeys(b for x in ss for b in x["bugs"]))])
            for x in ss:
                srows.append([t["key"], t["url"], t["effort"], t.get("product"), x["n"], x["date"] or None, x["time"],
                              ", ".join(x["testers"]), x["build"], x["system"], "\n".join(x["notes"]),
                              ", ".join(x["bugs"]), ", ".join(x.get("support", []))])
        self._create("testing", T, trows)
        self._create("test_sessions", S, srows)
        self.views["testing"], self.views["test_sessions"] = T, S
        self.conn.commit()

    # ── querying ──────────────────────────────────────────────────────────────

    def params(self):
        if self.rng is None:
            return {"start": ALL_START, "end": ALL_END}
        return {"start": self.rng[0].isoformat(), "end": self.rng[1].isoformat()}

    def query(self, sql, limit=5000):
        """(column names, rows, milliseconds). Raises sqlite3.Error with a readable message."""
        t0 = time.perf_counter()
        sql = sql.strip().rstrip(";")
        if not re.match(r"(?is)^\s*(with|select)\b", sql):
            raise sqlite3.Error("Only SELECT queries are allowed.")
        cur = self.conn.execute(sql, self.params())
        cols = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(limit)
        return cols, rows, (time.perf_counter() - t0) * 1000

    def columns_of(self, name):
        cur = self.conn.execute(f"SELECT * FROM {ident(name)} LIMIT 0")
        return [d[0] for d in cur.description]

    def distinct(self, name, column, limit=200):
        cur = self.conn.execute(f"SELECT DISTINCT {ident(column)} FROM {ident(name)} "
                                f"WHERE {ident(column)} IS NOT NULL AND {ident(column)} != '' "
                                f"ORDER BY 1 LIMIT {limit}")
        return [r[0] for r in cur.fetchall()]

    def rows_for(self, refs):
        """Sheet rows for "tab title:row" references (from a query's _rows column), by tab."""
        out = {}
        for ref in refs:
            title, _, r = str(ref).rpartition(":")
            row = self.rows_by_key.get((title, int(r))) if r.lstrip("-").isdigit() else None
            if row is not None:
                out.setdefault(title, []).append(row)
        return out


_cache = {}

def for_context(data, rng, row_filter=None):
    """A database for this data and date range (kept while the data doesn't change)."""
    key = (id(data), data.get("fetched_at"), rng, id(row_filter) if row_filter else None)
    db = _cache.get(key)
    if db is None:
        if len(_cache) > 12:
            _cache.clear()
        db = _cache[key] = Database(data, rng, row_filter)
    return db
