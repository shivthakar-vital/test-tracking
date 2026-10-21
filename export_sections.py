"""
Export sections: the simple, decluttered export. Instead of picking cards and charts,
pick sections — End-to-End Runs, Error Analysis, New Bugs, Change Log — each with a few
options, and which products to show (VitalOne, Viking; side by side or combined).

    config = {"order": ["e2e", "errors", "bugs", "changelog"],
              "on": {"e2e": True, …},
              "opts": {"e2e": {"status": True, "versions": True, "failed": True, …}, …},
              "products": {"VitalOne": True, "Viking": True},
              "split": True}                         # side by side (True) or combined

Each section becomes one card on the export page (export_report.Snapshot), so the
page layouts, arrangement, PNG/PDF and Slack summary all work the same.
"""

import html
from collections import Counter
from datetime import date, timedelta

import blocks
import changelog
import charts
import layout as lay
import tracker_store as store

esc = html.escape
INK, INK2, MUTED, LINK = "#1A1A18", "#52514E", "#8A8984", "#2F5E9E"
NEUTRAL = "#888780"

SECTIONS = {
    "e2e":       ("End-to-End Runs", [("status", "Run Status", True), ("tiles", "Run Counts", True),
                                      ("versions", "Runs Per Software Version", True),
                                      ("failed", "Failed Runs", True), ("days", "Pass Rate By Day", False),
                                      ("platform", "Coder vs Atlantis", False)]),
    "errors":    ("Error Analysis", [("reason", "Reason", True), ("subsystem", "Subsystem", True),
                                     ("notes", "Unresolved With Notes", False)]),
    "bugs":      ("New Bugs", [("open", "Open New Bugs", True), ("closed", "Closed Bugs", False),
                               ("allstatus", "All-Bugs Status", False)]),
    "testing":   ("Feature Testing", [("overview", "Overview Tiles", True), ("sessions", "What We Tested", True),
                                      ("efforts", "Test Efforts", True), ("found", "Bugs From Testing", True),
                                      ("hw", "Hardware Support Requests", True)]),
    "changelog": ("Change Log", [("counts", "Category Counts", True), ("qa", "In QA", True)]),
}

def default_config():
    return {"order": list(SECTIONS), "on": {"e2e": True, "errors": True, "bugs": True, "testing": True,
                                            "changelog": False},
            "opts": {sid: {k: d for k, _, d in opts} for sid, (_, opts) in SECTIONS.items()},
            "products": {}, "split": True}

def normalized(cfg):
    """A config with everything filled in (older or partial ones too)."""
    base = default_config()
    cfg = dict(cfg or {})
    order = [s for s in cfg.get("order", []) if s in SECTIONS]
    base["order"] = order + [s for s in SECTIONS if s not in order]
    base["on"].update({k: bool(v) for k, v in (cfg.get("on") or {}).items() if k in SECTIONS})
    for sid, opts in (cfg.get("opts") or {}).items():
        if sid in base["opts"]:
            base["opts"][sid].update({k: bool(v) for k, v in opts.items()})
    base["products"] = dict(cfg.get("products") or {})
    base["split"] = cfg.get("split", True)
    return base


# ── products ──────────────────────────────────────────────────────────────────

def run_products(data):
    """[(name, run tab)] — e.g. VitalOne and Viking."""
    return [(blocks.short_name(t["title"]), t) for t in data.get("tabs", []) if store.is_run_tab(t)]

def product_names(data):
    names = [n for n, _ in run_products(data)]
    bug = next((t for t in data.get("tabs", []) if store.is_bug_tab(t)), None)
    for n, _ in (lay.split_groups(bug, {"split": "viking"}) or []) if bug else []:
        if n not in names:
            names.append(n)
    return names

def chosen(cfg, name):
    return cfg["products"].get(name, True)


# ── helpers ───────────────────────────────────────────────────────────────────

def _label(text):
    return (f'<span style="font-size:11px; font-weight:bold; color:{MUTED}">{esc(text.upper())}</span>'
            f'<br>')

def _tile(label, value, sub="", color=INK):
    return (f'<td valign="top" bgcolor="#F7F7F5" style="padding:8px 12px">'
            f'<span style="font-size:11px; color:{INK2}">{esc(label)}</span><br>'
            f'<span style="font-size:22px; font-weight:bold; color:{color}">{esc(str(value))}</span><br>'
            f'<span style="font-size:10px; color:{MUTED}">{esc(sub)}</span></td>')

def _tag(text):
    return f'<span style="background:#F1EFE8; color:#52514E; font-size:11px">&nbsp;{esc(text)}&nbsp;</span>'

def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"

def _columns(cells, gap=24):
    """Side-by-side blocks of HTML, as a table."""
    if len(cells) == 1:
        return cells[0]
    w = int(100 / len(cells))
    return ('<table width="100%" cellspacing="0" cellpadding="0"><tr>' +
            "".join(f'<td valign="top" width="{w}%" style="padding-right:{gap}px">{c}</td>' for c in cells) +
            "</tr></table>")

def _find(tab, *words):
    return next((c for c in tab["columns"] if all(w in c["name"].lower() for w in words)), None)

def _reason_col(tab):
    return next((c for c in tab["columns"] if store.wants_pie(c) and not store.is_workflow(c)), None)


# ── sections ──────────────────────────────────────────────────────────────────

class Renderer:
    """Draws sections for an export Snapshot. `img(widget, width)` turns a chart into an
    <img> tag; `donut_hook(title, widget)` collects donuts for the Slack summary."""

    def __init__(self, data, ctx, cfg, img, inner_width, donut_hook=lambda *a: None):
        self.data, self.ctx, self.cfg, self.img, self.w = data, ctx, cfg, img, inner_width
        self.donut_hook = donut_hook
        self.runs = [(n, t) for n, t in run_products(data) if chosen(cfg, n)]
        self.bug_tab = next((t for t in data.get("tabs", []) if store.is_bug_tab(t)), None)

    def opt(self, sid, key):
        return self.cfg["opts"].get(sid, {}).get(key, False)

    def groups(self):
        """[(heading or None, [tabs])] — side by side, or one combined group."""
        if not self.runs:
            return []
        if self.cfg["split"] and len(self.runs) > 1:
            return [(n, [t]) for n, t in self.runs]
        return [(None, [t for _, t in self.runs])]

    def chart(self, b, tabs, width, ctx=None):
        parts = blocks.render(b, tabs, ctx or self.ctx)
        if not parts:
            return ""
        c = parts[0].content
        if isinstance(c, list):
            c = c[0].content
        if isinstance(c, str):
            return c
        if isinstance(c, charts.DonutChart):
            self.donut_hook(parts[0].title, c)
        return self.img(c, width)

    def render(self, sid):
        """(title, subtitle, html) for one section, or None if there's nothing to show."""
        return getattr(self, "_" + sid)()

    # End-to-End Runs ─────────────────────────────────────────────────────────

    def _e2e(self):
        groups = self.groups()
        if not groups:
            return None
        ctx = self.ctx
        col_w = (self.w - 24 * (len(groups) - 1)) // len(groups)
        cells = []
        for name, tabs in groups:
            out = [f'<p style="font-size:14px; font-weight:bold; margin:8px 0 6px 0">{esc(name)}</p>'] if name else []
            rows = [r for t in tabs for r in ctx.rows(t)]
            if self.opt("e2e", "status"):
                donut = self.chart({"type": "status", "column": lay.STATUS_REF, "count": "rows"}, tabs, col_w)
                week = self._week_runs(tabs)
                fixes = [e for t in tabs for e in store.resolutions(t, ctx.all_rows(t), ctx.rng)]
                open_ = [e for e in fixes if e[1] is None]
                as_of = min(ctx.rng[1], date.today()) if ctx.rng else date.today()
                oldest = max(((as_of - e[3]).days for e in open_ if e[3]), default=None)
                tiles = ('<table cellspacing="0" cellpadding="0"><tr>' +
                         _tile("Runs", len(rows), f"7 days: {week}" if week is not None else "") +
                         '<td width="8"></td>' +
                         _tile("Unresolved", len(open_), f"oldest {_plural(oldest, 'day')}" if oldest is not None
                               else "none open", "#B26B00" if open_ else INK) + "</tr></table>")
                if self.opt("e2e", "tiles"):
                    out.append(f'<p style="margin:0 0 8px 0">{tiles}</p>')
                out.append(f'{_label("Run Status")}' + (donut or f'<span style="color:{MUTED}">No runs in '
                                                                 f'{esc(ctx.range_text)}.</span>'))
            if self.opt("e2e", "versions"):
                ver = next((c for c in (_find(t, "software", "version") for t in tabs) if c), None)
                if ver:
                    bars = self.chart({"type": "bars", "column": ver["name"], "count": "rows"}, tabs, col_w)
                    if bars:
                        out.append(f'<p style="margin:12px 0 0 0">{_label("Runs Per Software Version")}{bars}</p>')
            if self.opt("e2e", "days"):
                d = store.date_column(tabs[0])
                days = self.chart({"type": "days", "column": d["name"] if d else None}, tabs[:1], col_w) if d else ""
                if days:
                    out.append(f'<p style="margin:12px 0 0 0">{_label("Runs By Day")}{days}</p>')
            if self.opt("e2e", "platform") and any(store.platform_column(t) for t in tabs):
                plat = self.chart({"type": "bars", "column": lay.PLATFORM_REF}, [t for t in tabs if store.platform_column(t)], col_w)
                if plat:
                    out.append(f'<p style="margin:12px 0 0 0">{_label("Coder vs Atlantis")}{plat}</p>')
            cells.append("".join(out))
        html_ = _columns(cells)
        if self.opt("e2e", "failed"):
            html_ += self._failed_runs()
        names = " and ".join(n for n, _ in self.runs)
        return "End-to-End Runs", names, html_

    def _week_runs(self, tabs):
        if self.ctx.rng is None:
            return None
        end = self.ctx.rng[1]
        start = end - timedelta(days=6)
        n = 0
        for t in tabs:
            d = store.date_column(t)
            if d:
                n += sum(store.in_range(r["values"][d["pos"]], (start, end)) for r in self.ctx.all_rows(t))
        return n

    def _failed_runs(self):
        ctx, items = self.ctx, []
        for name, t in self.runs:
            reason, fault = _reason_col(t), store.fault_column(t)
            notes = next((c for c in t["columns"] if store._words(c["name"]) & {"note", "notes"}), None)
            res, d = store.resolved_column(t), store.date_column(t)
            run_col = store.run_name_column(t)
            for r in ctx.rows(t):
                if charts.classify(store.row_status(t, r)) not in store.FAILED:
                    continue
                ran = store.parse_date(r["values"][d["pos"]]) if d else None
                fixed = store.parse_date(r["values"][res["pos"]]) if res else None
                as_of = min(ctx.rng[1], date.today()) if ctx.rng else date.today()
                if fixed and ran:
                    days = (fixed - ran).days
                    state = ("same day" if days <= 0 else f"fixed in {_plural(days, 'day')}"), "#0B7A0B"
                elif ran:
                    state = f"open {_plural((as_of - ran).days, 'day')}", MUTED
                else:
                    state = "open", MUTED
                bits = [r["values"][c["pos"]].strip() for c in (reason, fault) if c and r["values"][c["pos"]].strip()]
                note = r["values"][notes["pos"]].strip() if notes else ""
                if len(note) > 90:
                    note = note[:88].rstrip() + "…"
                num = r["values"][t["columns"][0]["pos"]].strip()
                link = r["links"][run_col["pos"]] if run_col else ""
                label = f"#{esc(num.lstrip('#'))}" if num else "run"
                run = f'<a href="{esc(link, quote=True)}">{label}</a>' if link else label
                items.append((ran or date.min, run, name, " · ".join(bits), note, state))
        if not items:
            return (f'<p style="margin:12px 0 0 0">{_label("Failed Runs")}'
                    f'<span style="color:{MUTED}">No failed runs in {esc(ctx.range_text)}.</span></p>')
        items.sort(key=lambda x: x[0], reverse=True)
        rows = "".join(
            f'<tr><td style="padding:5px 10px 5px 0; border-top:1px solid #ECEAE6">{run}</td>'
            f'<td style="padding:5px 10px 5px 0; border-top:1px solid #ECEAE6">{_tag(prod)}</td>'
            f'<td style="padding:5px 10px 5px 0; border-top:1px solid #ECEAE6">{esc(what) or "—"}'
            + (f'<span style="color:{MUTED}"> · {esc(note)}</span>' if note else "") + "</td>"
            f'<td style="padding:5px 0; border-top:1px solid #ECEAE6; white-space:nowrap; color:{color}">'
            f'{esc(state)}</td></tr>'
            for _, run, prod, what, note, (state, color) in items)
        return (f'<p style="margin:14px 0 2px 0">{_label(f"Failed Runs ({len(items)})")}</p>'
                f'<table width="100%" cellspacing="0" cellpadding="0">{rows}</table>')

    # Error Analysis ──────────────────────────────────────────────────────────

    def _errors(self):
        groups = self.groups()
        if not groups:
            return None
        col_w = (self.w - 24 * (len(groups) - 1)) // len(groups)
        cells, total_issues = [], 0
        for name, tabs in groups:
            out = [f'<p style="font-size:14px; font-weight:bold; margin:8px 0 6px 0">{esc(name)}</p>'] if name else []
            inner = []
            if self.opt("errors", "reason"):
                rc = next((c for c in (_reason_col(t) for t in tabs) if c), None)
                pie = self.chart({"type": "pie", "column": rc["name"], "count": "issues"}, tabs, col_w) if rc else ""
                inner.append(f'{_label("Reason")}{pie or f"<span style=color:{MUTED}>None recorded.</span>"}')
            if self.opt("errors", "subsystem"):
                stacked = not (len(groups) == 1 and self.opt("errors", "reason"))
                inner.append(f'{_label("Subsystem")}{self._subsystem_bars(tabs, col_w if stacked else col_w // 2 - 24)}')
            if inner:
                if len(groups) == 1 and len(inner) == 2:
                    out.append(_columns(inner))
                else:
                    out.append("".join(f'<p style="margin:0 0 10px 0">{x}</p>' for x in inner))
            if self.opt("errors", "notes"):
                notes = self.chart({"type": "by_subsystem", "which": "unresolved", "notes": True}, tabs, col_w)
                if notes:
                    out.append(f'<p style="margin:10px 0 0 0">{_label("Unresolved")}{notes}</p>')
            failed = sum(charts.classify(store.row_status(t, r)) in store.FAILED
                         for t in tabs for r in store.issues(t, self.ctx.rows(t)))
            total_issues += failed
            cells.append("".join(out))
        body = _columns(cells)
        if self.opt("errors", "notes"):                # the notes need the full width
            tabs = [t for _, t in self.runs]
            parts = blocks.render({"type": "by_subsystem", "which": "unresolved", "notes": True}, tabs, self.ctx) or []
            listing = next((p.content for p in parts[1:] if isinstance(p.content, str)), "")
            if listing:
                body += f'<p style="margin:12px 0 0 0"></p>{listing}'
        return "Error Analysis", f"{_plural(total_issues, 'issue')} · repeats counted once", body

    def _subsystem_bars(self, tabs, width):
        counts = Counter()
        for t in tabs:
            fault = store.fault_column(t)
            if not fault:
                continue
            for r in store.issues(t, self.ctx.rows(t)):
                if charts.classify(store.row_status(t, r)) in store.FAILED and r["values"][fault["pos"]].strip():
                    counts[r["values"][fault["pos"]].strip()] += 1
        if not counts:
            return f'<span style="color:{MUTED}">None recorded.</span>'
        bars = charts.StackedBars()
        bars.set_data([(k, [("Issues", n, NEUTRAL)]) for k, n in counts.most_common(8)])
        return self.img(bars, width)

    # New Bugs ────────────────────────────────────────────────────────────────

    def _bugs(self):
        tab = self.bug_tab
        if tab is None:
            return None
        groups = lay.split_groups(tab, {"split": "viking"}) or []
        picked = [(n, test) for n, test in groups if chosen(self.cfg, n)]
        if not groups:
            parts = [(None, self.ctx)]
        elif self.cfg["split"]:
            parts = [(n, self.ctx.section(test)) for n, test in picked]
        else:
            tests = [test for _, test in picked]
            parts = [(None, self.ctx.section(lambda r, ts=tests: any(t(r) for t in ts)))]
        if not parts:
            return None
        product = dict(groups)
        cells = []
        for name, ctx in parts:
            bugs = blocks._Bugs(tab, ctx)
            head = (f'<span style="color:{MUTED}; font-weight:normal; font-size:12px">'
                    f'{len(bugs.new)} new · {len(bugs.closed)} closed · {len(bugs.open)} open</span>')
            out = [f'<p style="font-size:14px; font-weight:bold; margin:8px 0 6px 0">{esc(name or "All products")} '
                   f'{head}</p>']
            if self.opt("bugs", "open"):
                out.append(self._bug_rows(bugs, bugs.new, bugs.made, product if not name else None,
                                          f"No new bugs in {ctx.range_text}."))
            if self.opt("bugs", "closed"):
                out.append(f'<p style="margin:10px 0 2px 0">{_label("Closed")}</p>')
                out.append(self._bug_rows(bugs, bugs.closed, bugs.done_col, product if not name else None,
                                          f"No bugs closed in {ctx.range_text}.", done=True))
            if self.opt("bugs", "allstatus"):
                donut = self.chart({"type": "status", "column": lay.STATUS_REF, "count": "rows"}, [tab], self.w, ctx)
                if donut:
                    out.append(f'<p style="margin:10px 0 0 0">{_label("All Bugs By Status")}{donut}</p>')
            cells.append("".join(out))
        return "New Bugs", f"opened · {self.ctx.range_text}", _columns(cells)

    def _bug_rows(self, bugs, items, date_col, product, empty, done=False):
        if not items:
            return f'<span style="color:{MUTED}">{esc(empty)}</span>'
        when = lambda r: bugs.when(r, date_col) or date.min
        rows = []
        for r in sorted(items, key=when, reverse=True):
            prod = ""
            if product:
                prod = " " + _tag(next((n for n, test in product.items() if test(r)), ""))
            d = bugs.when(r, date_col)
            rows.append(f'<tr><td style="padding:5px 10px 5px 0; border-top:1px solid #ECEAE6; white-space:nowrap">'
                        f'{"✓ " if done else ""}{bugs.link(r)}</td>'
                        f'<td style="padding:5px 10px 5px 0; border-top:1px solid #ECEAE6">'
                        f'{esc(bugs.val(r, bugs.summary_col))}{prod}</td>'
                        f'<td style="padding:5px 0; border-top:1px solid #ECEAE6; white-space:nowrap">'
                        f'{_tag(bugs.st[id(r)])}</td></tr>')
        return f'<table width="100%" cellspacing="0" cellpadding="0">{"".join(rows)}</table>'

    # Change Log ──────────────────────────────────────────────────────────────

    def _changelog(self):
        logs = changelog.saved(self.data.get("settings"))
        if not logs:
            return None
        log = logs[0]
        counts = Counter(e["category"] for e in log["entries"])
        qa = changelog.qa_entries(log)
        cells = "".join(_tile(c, counts.get(c, 0)) for c in changelog.CATEGORIES)
        out = [f'<p style="margin:0 0 6px 0"><code>{esc(log["from"])}</code> → <code>{esc(log["to"])}</code> '
               f'<span style="color:{MUTED}">· {log["commits"]} commits · {len(log["entries"])} PRs</span></p>',
               f'<table cellspacing="6" cellpadding="0"><tr>{cells}</tr></table>']
        if qa:
            rows = "".join(
                f'<tr><td style="padding:4px 10px 4px 0"><a href="{esc(t["url"])}">{esc(t["key"])}</a></td>'
                f'<td style="padding:4px 10px 4px 0">{esc(t["summary"])}</td>'
                f'<td style="padding:4px 0">{_tag(t["status"])}</td></tr>'
                for e in qa for t in e["tickets"] if changelog.in_qa(t))
            out.append(f'<p style="margin:10px 0 2px 0">{_label("In QA")}</p><table cellspacing="0">{rows}</table>')
        return "Change Log", f"latest · made {log['generated'][:10]}", "".join(out)


def kpis(data, ctx, cfg):
    """Headline numbers for the Slack summary, from the chosen products."""
    out = []
    for name, t in run_products(data):
        if chosen(cfg, name):
            rows = ctx.rows(t)
            failed = sum(charts.classify(store.row_status(t, r)) in store.FAILED for r in rows)
            out.append((0, (f"{name} runs", len(rows), f"{failed} failed", INK)))
    bug = next((t for t in data.get("tabs", []) if store.is_bug_tab(t)), None)
    if bug is not None:
        b = blocks._Bugs(bug, ctx)
        out.append((2, ("New bugs", len(b.new), ctx.range_text, charts.PROGRESS)))
        out.append((2, ("Bugs closed", len(b.closed), ctx.range_text, "#0B7A0B")))
    return out
