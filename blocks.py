"""
Draws the blocks of a card layout (see layout.py). The dashboard and the export
both call render(), so a block looks the same in both. A block becomes one or
more Parts: a titled chart widget or a piece of HTML (lists, summaries), plus
what clicking it should open.

A block can be drawn from several tabs at once (the export's combined Error
Analysis for VitalOne + Viking): counts are summed and columns matched by name.
"""

import html
from collections import Counter, defaultdict
from datetime import date, datetime

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

import charts
import layout as lay
import tracker_store as store

esc = html.escape
INK, INK2, MUTED, LINK = "#1A1A18", "#52514E", "#8A8984", "#2F5E9E"
GOOD_BG, NEW_BG, DONE_INK = "#E3F4E1", "#E8F1FC", "#0B7A0B"


class Ctx:
    """What's being drawn: the date range and whether it's for the dashboard or the export."""

    def __init__(self, rng=None, range_text="All dates", export=False, settings=None):
        self.rng, self.range_text, self.export = rng, range_text, export
        self.settings = settings or {}

    def all_rows(self, tab):
        return [r for r in tab["rows"] if not store.is_blank(r)]

    def dated(self, tab):
        return self.rng is not None and store.date_column(tab) is not None

    def rows(self, tab):
        rows = self.all_rows(tab)
        if not self.dated(tab):
            return rows
        dcol = store.date_column(tab)
        return [r for r in rows if store.in_range(r["values"][dcol["pos"]], self.rng)]


class Part:
    """content: a chart widget, an HTML string, or a list of Parts stacked vertically.
    pick(payload) → ("filters", tab, {pos: values}) or ("rows", tab, rows) or None.
    wide: takes the spare width in its row (bars, lists) instead of its natural size."""

    def __init__(self, title, content, pick=None, wide=False, full=False):
        self.title, self.content, self.pick, self.wide = title, content, pick, wide
        self.full = full                 # tables: always a row of their own in the export


def _unit(tab, b):
    if store.is_ticket_tab(tab):
        return "ticket"
    if b.get("count") == "issues":
        return "issue"
    return "run" if store.is_run_tab(tab) else "row"

def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"

def _counted_rows(tab, b, ctx):
    rows = ctx.rows(tab)
    return store.issues(tab, rows) if b.get("count") == "issues" else rows

def _split(tab, col, value):
    """The values in one cell: "HT - Camera, IA - Coils" → both; "A, B" in multi-selects → both."""
    if col is store.component_column(tab):
        return store.component_parts(col, value)
    return [p.strip() for p in store.parts(col, value) if p.strip()]

def _natural(text):
    import re
    return [(0, int(t), "") if t.isdigit() else (1, 0, t)
            for t in re.split(r"(\d+)", (text or "").lower()) if t]


# ── one renderer per block type ───────────────────────────────────────────────

def render(b, tabs, ctx, title=None):
    """Parts for block `b` drawn from `tabs` (usually one), or None when there's
    nothing to show (e.g. a pie whose column is still blank)."""
    fn = _RENDERERS.get(b["type"])
    if fn is None:
        return None
    parts = fn(b, tabs, ctx)
    if not parts:
        return None
    if b.get("title"):                         # the admin's own title replaces the first one
        parts[0].title = b["title"]
    return parts


def _status(b, tabs, ctx):
    counts, colors, options = Counter(), {}, []
    for tab in tabs:
        rows = _counted_rows(tab, b, ctx)
        col, cols = charts.tab_colors(tab, rows)
        if not col:
            continue
        colors.update(cols)
        options += [o for o in col["options"] if o not in options]
        counts.update(store.row_status(tab, r) for r in rows)
    if not counts:
        return None
    order = charts.status_order(list(counts), options)
    done = sum(n for v, n in counts.items() if charts.classify(v) == "done")
    total = sum(counts.values())
    donut = charts.DonutChart()
    donut.set_data([(v, counts[v], colors.get(v, charts.PENDING)) for v in order if counts[v]],
                   (f"{round(100 * done / total)}%", "done"))
    tab = tabs[0]
    return [Part(lay.auto_title(tab, b), donut,
                 lambda v, t=tab: ("filters", t, {store.STATUS: {v}}))]


def _pie(b, tabs, ctx):
    counts, options, name, subsystem = Counter(), [], None, b.get("colors") == "subsystem"
    unit = "issue"
    for tab in tabs:
        col = lay.find_column(tab, b.get("column"))
        if not col:
            continue
        name = name or col["name"]
        unit = _unit(tab, b)
        if col is store.fault_column(tab) and "colors" not in b:
            subsystem = True
        options += [o for o in col["options"] if o not in options]
        for r in _counted_rows(tab, b, ctx):
            counts.update(_split(tab, col, store.value_of(tab, r, col)))
    if not counts:
        return None
    base = charts.SUBSYSTEM_ORDER if subsystem else options
    order = [v for v in base if v in counts] + sorted(v for v in counts if v not in base)
    if b.get("column") == lay.STATUS_REF:
        palette = charts.tab_colors(tabs[0])[1]
    else:
        palette = {v: charts.subsystem_color(v) if subsystem else charts.category_color(name, v)
                   for v in order}
    total = sum(counts.values())
    pie = charts.DonutChart()
    pie.set_data([(v, counts[v], palette.get(v, charts.PENDING)) for v in order],
                 (str(total), unit if total == 1 else unit + "s"))
    tab = tabs[0]
    col = lay.find_column(tab, b.get("column"))
    pick = (lambda v, t=tab, p=col["pos"]: ("filters", t, {p: {v}})) if col else None
    return [Part(name if not b.get("title") else b["title"], pie, pick)]


def _group_names(col, groups, limit):
    if store.is_version(col):                   # newest version first
        return (sorted((k for k in groups if k), key=_natural, reverse=True)
                + ([""] if "" in groups else []))[:limit]
    return sorted(groups, key=lambda k: (k == "", -sum(groups[k].values()), k))[:limit]


def _bars(b, tabs, ctx):
    groups, colors, options, col0 = defaultdict(Counter), {}, [], None
    for tab in tabs:
        col = lay.find_column(tab, b.get("column"))
        status, cols = charts.tab_colors(tab, ctx.rows(tab))
        if not col:
            continue
        if not status:
            return _count(b, tabs, ctx)            # no status column: plain counts instead
        col0 = col0 or col
        colors.update(cols)
        options += [o for o in status["options"] if o not in options]
        for r in _counted_rows(tab, b, ctx):
            s = store.row_status(tab, r)
            for part in store.parts(col, store.value_of(tab, r, col)) or [""]:
                groups[part.strip()][s] += 1
    if not groups or (len(groups) < 2 and "" in groups):
        return None                                # nobody has filled this column in yet
    names = _group_names(col0, groups, 10 if ctx.export else 12)
    order = charts.status_order({s for g in groups.values() for s in g}, options)
    bars = charts.StackedBars()
    bars.set_data([(n, [(v, groups[n][v], colors.get(v, charts.PENDING)) for v in order]) for n in names],
                  blank=store.blank_label(col0))
    tab = tabs[0]
    gp = lay.find_column(tab, b.get("column"))["pos"]
    return [Part(lay.auto_title(tab, b), bars,
                 lambda gv, t=tab, gp=gp: ("filters", t, {gp: {gv[0]}, store.STATUS: {gv[1]}}), wide=True)]


def _count(b, tabs, ctx):
    groups, col0, unit = defaultdict(Counter), None, "rows"
    for tab in tabs:
        col = lay.find_column(tab, b.get("column"))
        if not col:
            continue
        col0 = col0 or col
        unit = _unit(tab, b) + "s"
        for r in _counted_rows(tab, b, ctx):
            for part in _split(tab, col, store.value_of(tab, r, col)) or [""]:
                groups[part][unit] += 1
    if not groups or (len(groups) < 2 and "" in groups):
        return None
    names = _group_names(col0, groups, 10 if ctx.export else 12)
    bars = charts.StackedBars()
    bars.set_data([(n, [(unit.capitalize(), groups[n][unit], charts.category_color(col0["name"], n)
                         if n else charts.PENDING)]) for n in names], blank=store.blank_label(col0))
    tab = tabs[0]
    gp = lay.find_column(tab, b.get("column"))["pos"]
    return [Part(lay.auto_title(tab, b), bars, lambda gv, t=tab, gp=gp: ("filters", t, {gp: {gv[0]}}),
                 wide=True)]


def _tally(b, tabs, ctx):
    counts, options, name = Counter(), [], None
    for tab in tabs:
        col = lay.find_column(tab, b.get("column"))
        if not col:
            continue
        name = name or col["name"]
        options += [o for o in col["options"] if o not in options]
        for r in _counted_rows(tab, b, ctx):
            counts.update(store.component_parts(col, store.value_of(tab, r, col)))
    if name is None:
        return None
    hide = b.get("hide_zero")
    hide_zero = hide is True or hide == "always" or (hide == "export" and ctx.export)
    groups = {}
    for value in options + sorted(v for v in counts if v not in options):
        if hide_zero and not counts[value]:
            continue
        sub, part = store.split_component(value)
        groups.setdefault(sub, []).append((part or value, counts[value], value))
    tab = tabs[0]
    title = b.get("title") or f"{name} tally, by subsystem"
    if not groups:
        if not ctx.export:
            return None
        return [Part(title, f'<span style="color:{MUTED}">No {esc(name.lower())} recorded'
                            f'{" in this date range" if ctx.rng else ""}.</span>', wide=True)]
    tally = charts.ComponentTally()
    tally.set_data([(sub, charts.subsystem_color(sub), parts) for sub, parts in groups.items()])
    col = lay.find_column(tab, b.get("column"))
    pick = (lambda v, t=tab, p=col["pos"]: ("filters", t, {p: {v}})) if col else None
    return [Part(title, tally, pick, wide=True)]


def _days(b, tabs, ctx):
    tab = tabs[0]
    dcol = lay.find_column(tab, b.get("column")) or store.date_column(tab)
    status, colors = charts.tab_colors(tab, ctx.rows(tab))
    if not dcol:
        return None
    days, raw = defaultdict(Counter), defaultdict(set)
    for r in _counted_rows(tab, b, ctx):
        d = store.parse_date(r["values"][dcol["pos"]])
        if d:
            days[d][store.row_status(tab, r) if status else "Rows"] += 1
            raw[d].add(r["values"][dcol["pos"]])
    if not days:
        return None
    order = charts.status_order({s for g in days.values() for s in g}, status["options"] if status else [])
    chart = charts.DayColumns()
    chart.set_data([(d, [(v, days[d][v], colors.get(v, charts.PROGRESS)) for v in order]) for d in sorted(days)])

    def pick(ds, t=tab, dp=dcol["pos"]):
        f = {dp: raw[ds[0]]}
        if status:
            f[store.STATUS] = {ds[1]}
        return "filters", t, f
    return [Part(f"Per day ({dcol['name']})", chart, pick, wide=True)]


def _cell_html(tab, r, col, colors):
    v = store.value_of(tab, r, col)
    if col["pos"] == store.STATUS:
        dot = f'<span style="color:{colors.get(v, charts.PENDING)}">●</span> ' if v else ""
        return dot + esc(v or "—")
    link = r["links"][col["pos"]] if isinstance(col["pos"], int) else ""
    if link:
        return f'<a href="{esc(link, quote=True)}" style="color:{LINK}">{esc(v or "Open")}</a>'
    return esc(v or "—")


def _list(b, tabs, ctx):
    tab = tabs[0]
    cols = [c for c in (lay.find_column(tab, ref) for ref in b.get("columns") or []) if c]
    if not cols:
        cols = [c for c in tab["columns"][:5]]
    rows = []
    for t in tabs:
        rows += [(t, r) for r in _counted_rows(t, b, ctx)]
    if not rows:
        return None
    sort = lay.find_column(tab, b.get("sort")) or store.date_column(tab)
    if sort:
        key = (lambda tr: store.parse_date(store.value_of(tr[0], tr[1], sort)) or date.min) \
            if sort["kind"] == "date" else (lambda tr: _natural(store.value_of(tr[0], tr[1], sort)))
        rows.sort(key=key, reverse=sort["kind"] == "date")
    colors = charts.tab_colors(tab, [r for _, r in rows])[1]
    limit = None if ctx.export else 25
    shown = rows[:limit] if limit else rows
    head = "".join(f'<th align="left" style="color:{MUTED}; font-size:11px; padding:4px 8px 4px 0">'
                   f'{esc("Run" if c is store.run_name_column(tab) else c["name"])}</th>' for c in cols)
    lines = [f'<table width="100%" cellspacing="0" cellpadding="5"><tr>{head}</tr>']
    for i, (t, r) in enumerate(shown):
        bg = ' bgcolor="#F7F7F5"' if i % 2 else ""
        cells = []
        for c in cols:
            tc = lay.find_column(t, lay.col_ref(c)) or c
            cells.append(f'<td style="padding:3px 8px 3px 0">{_cell_html(t, r, tc, colors)}</td>')
        lines.append(f"<tr{bg}>{''.join(cells)}</tr>")
    lines.append("</table>")
    if limit and len(rows) > limit:
        lines.append(f'<span style="color:{MUTED}">and {len(rows) - limit} more · '
                     f'<a href="pick:all" style="color:{LINK}">open them in the table</a></span>')
    name_col = store.run_name_column(tab)
    if ctx.export and name_col in cols and any(r["links"][name_col["pos"]] for _, r in rows):
        lines.append(f'<p style="color:{MUTED}; font-size:11px">Run names with links open the run '
                     f'in Atlantis (in the PDF).</p>')
    return [Part(lay.auto_title(tab, b), "".join(lines),
                 lambda _, t=tab, rs=[r for _, r in rows]: ("rows", t, rs), wide=True, full=True)]


# ── bugs ──────────────────────────────────────────────────────────────────────

class _Bugs:
    """Who's open, new, closed and in QA, for the bug summary and lists."""

    def __init__(self, tab, ctx):
        rows = ctx.rows(tab)
        self.tab, self.rows = tab, rows
        self.made, self.done_col = store.created_date_column(tab), store.done_date_column(tab)
        self.key_col = next((c for c in tab["columns"] if any(r["links"][c["pos"]] for r in rows)),
                            tab["columns"][0])
        self.summary_col = next((c for c in tab["columns"] if "summary" in c["name"].lower()), None)
        self.who_col = next((c for c in tab["columns"] if "assign" in c["name"].lower()), None)
        self.st = {id(r): store.row_status(tab, r) for r in rows}
        is_done = lambda r: charts.classify(self.st[id(r)]) == "done" or bool(store.parse_date(self.val(r, self.done_col)))
        self.open = [r for r in rows if not is_done(r)]
        self.qa = [r for r in self.open if "qa" in store._words(self.st[id(r)])]
        self.new = [r for r in rows if self.made and store.in_range(self.val(r, self.made), ctx.rng)]
        self.closed = [r for r in rows if self.done_col and store.in_range(self.val(r, self.done_col), ctx.rng)]

    @staticmethod
    def val(r, c):
        return r["values"][c["pos"]] if c else ""

    def when(self, r, c):
        return store.parse_date(self.val(r, c))

    def link(self, r):
        key, link = r["values"][self.key_col["pos"]], r["links"][self.key_col["pos"]]
        return f'<a href="{esc(link, quote=True)}" style="color:{LINK}">{esc(key)}</a>' if link else esc(key)

    def filters(self, which):
        """What clicking a tile opens."""
        spos = store.STATUS
        if which == "open":
            return {spos: {self.st[id(r)] for r in self.open} or {"\0"}}
        if which == "qa":
            return {spos: {self.st[id(r)] for r in self.qa} or {"\0"}}
        col, items = (self.made, self.new) if which == "new" else (self.done_col, self.closed)
        return {col["pos"]: {self.val(r, col) for r in items} or {"\0"}} if col else None


def _tile_widget(label, n, color, sub, on_click):
    tile = QFrame()
    tile.setObjectName("tile")
    tl = QVBoxLayout(tile)
    tl.setContentsMargins(12, 8, 12, 8)
    tl.setSpacing(0)
    for text, style in ((label, "color:#666663; font-size:11px;"),
                        (str(n), f"font-size:22px; font-weight:bold; color:{color};"),
                        (sub, f"color:#999996; font-size:10px;")):
        lbl = QLabel(text)
        lbl.setStyleSheet(style)
        tl.addWidget(lbl)
    if on_click:
        tile.setCursor(Qt.PointingHandCursor)
        tile.setToolTip(f"See these {n} bugs")
        tile.mousePressEvent = lambda e: on_click()
    return tile


def _bug_summary(b, tabs, ctx):
    tab = tabs[0]
    if not store.status_columns(tab):
        return None
    bugs = _Bugs(tab, ctx)
    period = ctx.range_text
    tiles = [("Open now", len(bugs.open), INK, f"{len(bugs.rows) - len(bugs.open)} of {len(bugs.rows)} done", "open"),
             ("New", len(bugs.new), charts.PROGRESS, f"opened · {period}", "new"),
             ("Closed", len(bugs.closed), DONE_INK, f"done · {period}", "closed"),
             ("In QA", len(bugs.qa), charts.QA, "waiting on QA now", "qa")]
    pick = lambda which, t=tab, bg=bugs: ("filters", t, bg.filters(which)) if bg.filters(which) else None
    title = b.get("title") or f"New and closed · {period}"
    if ctx.export:
        cells = "".join(
            f'<td width="25%" valign="top" style="padding:8px 12px; border:1px solid #E6E4E0">'
            f'<span style="font-size:11px; color:{INK2}">{esc(label)}</span><br>'
            f'<span style="font-size:26px; font-weight:bold; color:{color}">{n}</span><br>'
            f'<span style="font-size:10px; color:{MUTED}">{esc(sub)}</span></td>'
            for label, n, color, sub, _ in tiles)
        return [Part(title, f"<table width='100%' cellspacing='6' cellpadding='0'><tr>{cells}</tr></table>",
                     wide=True)]

    box = QWidget()
    vl = QVBoxLayout(box)
    vl.setContentsMargins(0, 0, 0, 0)
    vl.setSpacing(10)
    row = QHBoxLayout()
    row.setSpacing(10)
    for label, n, color, sub, which in tiles:
        f = bugs.filters(which)
        row.addWidget(_tile_widget(label, n, color, sub,
                                   (lambda w=which: box.picked_cb(w)) if f is not None else None), 1)
    vl.addLayout(row)
    net = len(bugs.new) - len(bugs.closed)
    first = f"<b>{esc(period)}:</b> {len(bugs.new)} new, {len(bugs.closed)} closed"
    if ctx.rng is not None:
        first += f" (net {'+' if net > 0 else ''}{net})"
    bits = [first, f"{len(bugs.open)} open now", f"{len(bugs.qa)} in QA"]
    age = lambda r: (date.today() - bugs.when(r, bugs.made)).days if bugs.made and bugs.when(r, bugs.made) else None
    oldest = max((r for r in bugs.open if age(r) is not None), key=age, default=None)
    if oldest is not None:
        bits.append(f"oldest open: {bugs.link(oldest)} ({_plural(age(oldest), 'day')})")
    line = QLabel(" · ".join(bits))
    line.setTextFormat(Qt.RichText)
    line.setOpenExternalLinks(True)
    line.setWordWrap(True)
    line.setStyleSheet("font-size:12px; color:#1A1A18;")
    vl.addWidget(line)
    box.picked_cb = lambda w: None              # the dashboard wires this up
    return [Part(title, box, pick, wide=True)]


def _bug_list(b, tabs, ctx):
    tab = tabs[0]
    bugs = _Bugs(tab, ctx)
    which = b.get("which", "new")
    today = date.today()
    if which == "new":
        items = sorted(bugs.new, key=lambda r: bugs.when(r, bugs.made) or today, reverse=True)
        color, date_col, bg, mark = charts.PROGRESS, bugs.made, NEW_BG, ""
        extra = lambda r: bugs.st[id(r)]
    elif which == "closed":
        items = sorted(bugs.closed, key=lambda r: bugs.when(r, bugs.done_col) or today, reverse=True)
        color, date_col, bg, mark = DONE_INK, bugs.done_col, GOOD_BG, "<b>✓</b> "
        extra = lambda r: bugs.val(r, bugs.done_col)
    else:
        items = sorted(bugs.qa, key=lambda r: bugs.when(r, bugs.made) or today)
        color, date_col, bg, mark = charts.QA, bugs.made, "#FCEFF4", ""
        extra = lambda r: bugs.val(r, bugs.who_col)
    if which in ("new", "closed") and date_col is None:
        return None
    title = lay.auto_title(tab, b)
    pick = lambda _, t=tab, rs=items: ("rows", t, rs)

    if ctx.export:
        if not items:
            empty = {"new": "opened", "closed": "closed", "qa": "waiting on QA"}[which]
            return [Part(title, f'<span style="color:{MUTED}">No bugs {empty} in {esc(ctx.range_text)}.</span>',
                         wide=True, full=True)]
        head = [bugs.key_col["name"], bugs.summary_col["name"] if bugs.summary_col else "", "Status",
                bugs.who_col["name"] if bugs.who_col else "", date_col["name"] if date_col else ""]
        lines = ['<table width="100%" cellspacing="0" cellpadding="5">',
                 "<tr>" + "".join(f'<th align="left" style="color:{MUTED}; font-size:11px">{esc(h)}</th>'
                                  for h in head) + "</tr>"]
        for r in items:
            when = bugs.when(r, date_col)
            lines.append(f'<tr bgcolor="{bg}"><td>{mark}{bugs.link(r)}</td>'
                         f'<td>{esc(bugs.val(r, bugs.summary_col))}</td><td>{esc(bugs.st[id(r)])}</td>'
                         f'<td>{esc(bugs.val(r, bugs.who_col))}</td>'
                         f'<td>{f"{when.month}/{when.day}/{when.year}" if when else ""}</td></tr>')
        lines.append("</table>")
        return [Part(f"{title} ({len(items)})", "".join(lines), pick, wide=True, full=True)]

    limit = 5
    rows_html = []
    for r in items[:limit]:
        summ = bugs.val(r, bugs.summary_col)
        summ = summ if len(summ) <= 36 else summ[:34].rstrip() + "…"
        rows_html.append(f'<tr><td style="padding:3px 10px 3px 0; white-space:nowrap">{bugs.link(r)}</td>'
                         f'<td style="padding:3px 10px 3px 0; color:#1A1A18; white-space:nowrap">{esc(summ)}</td>'
                         f'<td style="padding:3px 0; color:#8A8984; white-space:nowrap">{esc(extra(r))}</td></tr>')
    more = (f'<br><a href="pick:all" style="color:{LINK}">and {len(items) - limit} more</a>'
            if len(items) > limit else "")
    body = (f'<table cellspacing="0">{"".join(rows_html)}</table>{more}' if items
            else '<span style="color:#8A8984">None</span>')
    heading = {"new": "New", "closed": "Closed", "qa": "In QA"}[which] if not b.get("title") else b["title"]
    text = (f'<span style="color:{color}; font-weight:bold">●</span> <b>{esc(heading)}</b> '
            f'<span style="color:#8A8984">({len(items)})</span><br>{body}')
    return [Part("", text, pick, wide=True)]


# ── resolution ────────────────────────────────────────────────────────────────

def _fixes(tabs, ctx):
    """Every failed run's issue, resolved or not (open ones from before the range included)."""
    return [(t, e) for t in tabs for e in store.resolutions(t, ctx.all_rows(t), ctx.rng)]

def _rows_pick(tab, fixes, test):
    return lambda payload, t=tab, f=fixes: ("rows", t, [e[0] for _, e in f if test(e, payload)])

def _resolved(b, tabs, ctx):
    fixes = _fixes(tabs, ctx)
    tab = tabs[0]
    as_of = min(ctx.rng[1], date.today()) if ctx.rng else None
    made = charts.resolution_charts([e for _, e in fixes], as_of,
                                    noun="ticket" if store.is_ticket_tab(tab) else "issue")
    if not made:
        return None
    donut, days, summary = made
    left = Part(lay.auto_title(tab, b), donut,
                _rows_pick(tab, fixes, lambda e, v: (e[1] is not None) == (v == "Resolved")))
    right = []
    if days is not None:
        right.append(Part("Days to resolve (run date → Resolved date)", days, wide=True))
    right.append(Part("", f'<span style="font-size:12px; color:{INK}">{esc(summary)}</span>', wide=True))
    return [left, Part("", right, wide=True)]


def _by_subsystem(b, tabs, ctx):
    fixes = _fixes(tabs, ctx)
    resolved = b.get("which") == "resolved"
    tally = charts.resolved_by_subsystem([e for _, e in fixes], resolved)
    if tally is None:
        return None
    test = lambda e, p: ((e[1] is not None) == resolved and (e[4] or charts.NOT_RECORDED) == p[0]
                         and e[5] == p[1])
    return [Part(lay.auto_title(tabs[0], b), tally, _rows_pick(tabs[0], fixes, test), wide=True)]


_RENDERERS = {"status": _status, "pie": _pie, "bars": _bars, "count": _count, "tally": _tally,
              "days": _days, "list": _list, "bug_summary": _bug_summary, "bug_list": _bug_list,
              "resolved": _resolved, "by_subsystem": _by_subsystem}


# ── card headers ──────────────────────────────────────────────────────────────

def short_name(title):
    return title.replace(" End-to-End Tracker", "")

def card_note(card, tabs, ctx):
    """The grey line next to a card's title, e.g. “6 failed runs · Today (Oct 7)”."""
    if card["id"] == "analysis":
        tab = tabs[0]
        if store.is_ticket_tab(tab):
            rows = store.issues(tab, ctx.rows(tab))
            done = sum(charts.classify(store.row_status(tab, r)) == "done" for r in rows)
            note = f"{_plural(len(rows), 'ticket')} · {len(rows) - done} open"
        else:
            is_failed = lambda t, r: charts.classify(store.row_status(t, r)) in store.FAILED
            per = {t["title"]: sum(is_failed(t, r) for r in store.issues(t, ctx.rows(t))) for t in tabs}
            issues = sum(per.values())
            runs = sum(is_failed(t, r) for t in tabs for r in ctx.rows(t))
            note = _plural(issues, "failed run")
            if runs > issues:
                note = f"{_plural(issues, 'issue')} from {runs} failed runs (repeats counted once)"
            if len(tabs) > 1:
                note += ": " + " · ".join(f"{short_name(k)} {v}" for k, v in per.items())
        if any(ctx.dated(t) for t in tabs):
            note += " · " + ctx.range_text
        return note + " · blanks are left out"
    tab = tabs[0]
    n = sum(len(ctx.rows(t)) for t in tabs)
    unit = "run" if store.is_run_tab(tab) else "row"
    text = _plural(n, unit)
    if any(ctx.dated(t) for t in tabs):
        total = sum(len(ctx.all_rows(t)) for t in tabs)
        text = f"{n} of {total} {unit}s · {ctx.range_text}" if not ctx.export else \
            f"{_plural(n, unit)} in {ctx.range_text}" + (f" (of {total} in total)" if total != n else "")
    status = store.derived_columns(tab).get(store.STATUS)
    if status and not ctx.export:
        text += f" · by {status['name']}"
    return text


def rows_of(items):
    """Split a card's drawn blocks [(block, parts)] into rows: new_row starts a new one."""
    out = []
    for item in items:
        b = item[0]
        if not out or b.get("new_row"):
            out.append([])
        out[-1].append(item)
    return out
