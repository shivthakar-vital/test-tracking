"""
Snapshot export: a one-page summary of testing for a date range, saved as a PNG
(to paste into Slack or email) and a matching PDF (where run and bug links are
clickable).

Both files come from one layout: an HTML page with the charts drawn as images,
so they look the same. It covers:
  • end-to-end run trackers: status donut, workflow split, Coder vs Atlantis
    (trackers that can run on Coder), runs per software version, and the list
    of runs with links
  • Error Analysis: reason and subsystem pies, component tally
  • bug trackers: status donut, and the bugs closed in the date range, highlighted
"""

import html, re
from collections import Counter, defaultdict
from datetime import datetime

from PyQt5.QtCore import Qt, QUrl, QSizeF, QMarginsF
from PyQt5.QtGui import QTextDocument, QImage, QPainter, QPageSize, QPageLayout, QColor, QFont
from PyQt5.QtPrintSupport import QPrinter
from PyQt5.QtWidgets import QWidget

import charts
import tracker_store as store

WIDTH = 1040                    # page width in pixels
SCALE = 2                       # charts and the PNG are drawn at 2× for sharp text
INK, INK2, MUTED, LINE, GOOD_BG = "#1A1A18", "#52514E", "#8A8984", "#E6E4E0", "#E3F4E1"
NEW_BG = "#E8F1FC"

esc = html.escape


def _rows(tab):
    return [r for r in tab["rows"] if not store.is_blank(r)]

def _in_range(tab, rng):
    dcol = store.date_column(tab)
    rows = _rows(tab)
    if dcol is None or rng is None:
        return rows
    return [r for r in rows if store.in_range(r["values"][dcol["pos"]], rng)]

def _natural(text):
    """PROD-2 before PROD-10; v4.19.10 after v4.19.9."""
    return [(0, int(t), "") if t.isdigit() else (1, 0, t)
            for t in re.split(r"(\d+)", (text or "").lower()) if t]

def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


class Snapshot:
    def __init__(self, data, rng, range_text, settings=None):
        self.data, self.rng, self.range_text = data, rng, range_text
        self.settings = settings or {}
        self.images = {}                 # resource name → QImage
        charts.set_overrides(store.color_overrides(self.settings))
        charts.register(data)            # same colors as the dashboard
        self.doc = self._build()

    # ── charts as images ──────────────────────────────────────────────────────

    def _image(self, widget, width=None):
        """Draw a chart widget into an image and return an <img> tag for it."""
        widget.setAttribute(Qt.WA_DontShowOnScreen)
        if width:
            widget.setFixedWidth(width)
        widget.ensurePolished()
        if isinstance(widget, charts.ComponentTally):
            widget.resize(width, 100)
            widget._relayout()
        w, h = widget.width(), widget.height()
        img = QImage(w * SCALE, h * SCALE, QImage.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(SCALE)
        img.fill(QColor("#FFFFFF"))
        p = QPainter(img)
        widget.render(p, flags=QWidget.RenderFlags(QWidget.DrawChildren))   # no window background
        p.end()
        name = f"chart{len(self.images)}.png"
        self.images[name] = img
        return f'<img src="{name}" width="{w}" height="{h}">'

    def _donut(self, counts, colors, order, center):
        d = charts.DonutChart()
        d.set_data([(v, counts[v], colors.get(v, charts.PENDING)) for v in order if counts[v]], center)
        return self._image(d)

    def _status_donut(self, tab, rows):
        status, colors = charts.tab_colors(tab, rows)
        counts = Counter(store.row_status(tab, r) for r in rows)
        order = charts.status_order(list(counts), status["options"] if status else [])
        done = sum(n for v, n in counts.items() if charts.classify(v) == "done")
        pct = f"{round(100 * done / len(rows))}%" if rows else "–"
        return self._donut(counts, colors, order, (pct, "done")), status, colors, order

    def _bars(self, tab, rows, col, colors, order, width):
        groups = defaultdict(Counter)
        for r in rows:
            for part in store.parts(col, store.value_of(tab, r, col)):
                groups[part.strip()][store.row_status(tab, r)] += 1
        if store.is_version(col):                  # newest version first
            names = (sorted((k for k in groups if k), key=_natural, reverse=True)
                     + ([""] if "" in groups else []))[:10]
        else:
            names = sorted(groups, key=lambda k: (k == "", -sum(groups[k].values()), k))[:10]
        b = charts.StackedBars()
        b.set_data([(n, [(v, groups[n][v], colors.get(v, charts.PENDING)) for v in order]) for n in names],
                   blank=store.blank_label(col))
        return self._image(b, width)

    def _category_pie(self, rows, col, unit="issue"):
        counts = Counter(p.strip() for r in rows for p in store.parts(col, r["values"][col["pos"]]) if p.strip())
        return self._counts_pie(counts, col["options"], col["name"], unit=unit)

    def _counts_pie(self, counts, options, column, subsystem=False, unit="issue"):
        """A donut of category counts, in the app's shared colors for that column
        (or for subsystems), so they match the dashboard."""
        base = charts.SUBSYSTEM_ORDER if subsystem else options
        options = [v for v in base if v in counts] + sorted(v for v in counts if v not in base)
        palette = {v: charts.subsystem_color(v) if subsystem else charts.category_color(column, v)
                   for v in options}
        total = sum(counts.values())
        return self._donut(counts, palette, options, (str(total), unit if total == 1 else unit + "s"))

    # ── page ──────────────────────────────────────────────────────────────────

    def _build(self):
        tabs = self.data["tabs"]
        runs = [t for t in tabs if store.is_run_tab(t)]
        analysis = [t for t in runs if store.fault_column(t) or store.component_column(t)]
        bugs = [t for t in tabs if store.is_bug_tab(t)]

        body = [f'''
<table width="100%" cellspacing="0" cellpadding="0"><tr>
  <td><span style="font-size:24px; font-weight:bold; color:{INK}">{esc(self.data["title"])}</span><br>
      <span style="font-size:15px; color:{INK2}">Testing snapshot · <b>{esc(self.range_text)}</b></span></td>
  <td align="right" valign="bottom" style="color:{MUTED}; font-size:11px">
      Generated {datetime.now():%b %-d, %Y at %-I:%M %p}<br>Test Tracker · sheet data from {self._synced()}</td>
</tr></table>''']

        if runs:
            body.append(self._heading("End-to-End Runs"))
            for tab in runs:
                body.append(self._runs_section(tab))
        if analysis:                       # one combined section for all run trackers
            names = " + ".join(t["title"].replace(" End-to-End Tracker", "") for t in analysis)
            body.append(self._heading(f"Error Analysis — {names}" if len(analysis) > 1 else
                                      f"{analysis[0]['title']} — Error Analysis"))
            body.append(self._analysis_section(analysis))
        for tab in bugs:
            body.append(self._heading(tab["title"]))
            body.append(self._bugs_section(tab))

        doc = QTextDocument()
        font = QFont()
        font.setPixelSize(13)
        doc.setDefaultFont(font)
        doc.setDocumentMargin(28)
        doc.setDefaultStyleSheet(f'''
            a {{ color: #2F5E9E; text-decoration: underline; }}
            th {{ color: {MUTED}; font-size: 11px; font-weight: bold; text-align: left; }}
            td {{ color: {INK}; font-size: 13px; }}''')
        for name, img in self.images.items():
            doc.addResource(QTextDocument.ImageResource, QUrl(name), img)
        doc.setHtml(f'<body style="background:#FFFFFF">{"".join(body)}</body>')
        doc.setTextWidth(WIDTH)
        return doc

    def _synced(self):
        try:
            return f"{datetime.fromisoformat(self.data['fetched_at']):%b %-d, %-I:%M %p}"
        except (KeyError, ValueError):
            return "the last sync"

    @staticmethod
    def _heading(text):
        return (f'<p style="margin-top:26px; margin-bottom:6px; font-size:17px; font-weight:bold; '
                f'color:{INK}">{esc(text)}</p><hr style="color:{LINE}">')

    @staticmethod
    def _label(text):
        return f'<span style="font-size:11px; font-weight:bold; color:{MUTED}">{esc(text.upper())}</span><br>'

    def _runs_section(self, tab):
        rows = _in_range(tab, self.rng)
        total = len(_rows(tab))
        note = f"{_plural(len(rows), 'run')} in {self.range_text}"
        if self.rng and total and total != len(rows):
            note += f" (of {total} in total)"
        out = [f'<p style="margin-top:12px; margin-bottom:4px"><b style="font-size:15px">{esc(tab["title"])}</b>'
               f'&nbsp;&nbsp;<span style="color:{MUTED}">{esc(note)}</span></p>']
        if not rows:
            out.append(f'<p style="color:{MUTED}">No runs in this date range.</p>')
            return "".join(out)
        donut, status, colors, order = self._status_donut(tab, rows)
        cells = [f'<td valign="top" width="400">{self._label(status["name"] if status else "Status")}{donut}</td>']
        for wf in [c for c in store.pie_columns(tab, rows, self.settings) if store.is_workflow(c)]:
            cells.append(f'<td valign="top">{self._label(wf["name"])}{self._category_pie(rows, wf, unit="run")}</td>')
        out.append(f'<table cellspacing="0" cellpadding="4"><tr>{"".join(cells)}</tr></table>')

        bars = []
        plat = store.derived_columns(tab).get(store.PLATFORM)
        version = next((c for c in tab["columns"] if store.is_version(c) and "software" in c["name"].lower()), None)
        width = 480 if plat and version else 620
        if plat:
            bars.append(f'<td valign="top">{self._label("Runs on Coder vs Atlantis")}'
                        f'{self._bars(tab, rows, plat, colors, order, width)}</td>')
        if version and any(r["values"][version["pos"]].strip() for r in rows):
            bars.append(f'<td valign="top">{self._label("Runs per " + version["name"])}'
                        f'{self._bars(tab, rows, version, colors, order, width)}</td>')
        if bars:
            out.append(f'<table cellspacing="0" cellpadding="4"><tr>{"".join(bars)}</tr></table>')
        out.append(self._run_list(tab, rows, colors))
        return "".join(out)

    def _run_list(self, tab, rows, colors):
        dcol, name_col = store.date_column(tab), store.run_name_column(tab)
        plat = store.platform_column(tab)
        pies = store.pie_columns(tab, rows, self.settings)
        workflow = next((c for c in pies if store.is_workflow(c)), None)
        reason = next((c for c in pies if not store.is_workflow(c)), None)
        fault = store.fault_column(tab)
        extra = [c for c in (workflow, reason, fault) if c]
        head = ["Date", "Run"] + (["Ran on"] if plat else []) + ["Status"] + [c["name"] for c in extra]
        lines = [f'<p style="margin-top:8px; margin-bottom:2px">{self._label("Runs")}</p>',
                 '<table width="100%" cellspacing="0" cellpadding="5" style="border-collapse:collapse">',
                 "<tr>" + "".join(f"<th>{esc(h)}</th>" for h in head) + "</tr>"]
        key = lambda r: (store.parse_date(r["values"][dcol["pos"]]) if dcol else None) or datetime.min.date()
        for i, r in enumerate(sorted(rows, key=key, reverse=True)):
            name, link = r["values"][name_col["pos"]], r["links"][name_col["pos"]]
            run = (f'<a href="{esc(link, quote=True)}">{esc(name or "Open run")}</a>' if link
                   else esc(name or "—"))
            st = store.row_status(tab, r)
            dot = f'<span style="color:{colors.get(st, charts.PENDING)}">●</span> ' if st else ""
            vals = [esc(r["values"][dcol["pos"]] if dcol else ""), run]
            if plat:
                vals.append(esc(store.row_platform(tab, r)))
            vals.append(dot + esc(st or "—"))
            vals += [esc(r["values"][c["pos"]] or "—") for c in extra]
            bg = ' bgcolor="#F7F7F5"' if i % 2 else ""
            lines.append(f"<tr{bg}>" + "".join(f"<td>{v}</td>" for v in vals) + "</tr>")
        lines.append("</table>")
        linked = sum(bool(r["links"][name_col["pos"]]) for r in rows)
        if linked:
            lines.append(f'<p style="color:{MUTED}; font-size:11px">Run names with links open the run '
                         f'in Atlantis (in the PDF).</p>')
        return "".join(lines)

    def _analysis_section(self, tabs):
        """Why runs failed, summed across the given run trackers: one pie per reason
        column (matched by name), a Subsystem At Fault pie, and a tally of the
        components that had runs. Dropdown options from every tab are combined."""
        # a chain of continued runs ("Continued from Run#") is one issue, counted once
        per_tab = [(t, store.issues(t, _in_range(t, self.rng))) for t in tabs]
        is_failed = lambda t, r: charts.classify(store.row_status(t, r)) in store.FAILED
        failed = {t["title"]: sum(is_failed(t, r) for r in rows) for t, rows in per_tab}
        runs = sum(is_failed(t, r) for t in tabs for r in _in_range(t, self.rng))
        total_failed = sum(failed.values())
        note = f"{_plural(total_failed, 'failed run')} in {esc(self.range_text)}"
        if runs > total_failed:
            note = (f"{_plural(total_failed, 'issue')} from {runs} failed runs in {esc(self.range_text)} "
                    f"(repeats counted once)")
        if len(tabs) > 1:
            note += ": " + " · ".join(f"{esc(k.replace(' End-to-End Tracker', ''))} {v}" for k, v in failed.items())
        out = [f'<p style="color:{MUTED}; margin-bottom:4px">{note} · blank cells are left out</p>']

        def combine(pick):
            """(name, counts, options) for one kind of column, summed over the tabs."""
            counts, options, name = Counter(), [], None
            for tab, rows in per_tab:
                col = pick(tab, rows)
                if not col:
                    continue
                name = name or col["name"]
                options += [o for o in col["options"] if o not in options]
                counts.update(p.strip() for r in rows for p in store.parts(col, r["values"][col["pos"]]) if p.strip())
            options += sorted(v for v in counts if v not in options)
            return name, counts, options

        # reason-style pies, matched across tabs by column name (e.g. "Reason for Error Runs")
        reason_names = list(dict.fromkeys(c["name"].strip().lower() for t, rows in per_tab
                                          for c in store.pie_columns(t, rows, self.settings)
                                          if not store.is_workflow(c)))
        comp_name, comp_counts, comp_opts = combine(lambda t, rows: store.component_column(t))

        cells = []
        for key in reason_names:
            name, counts, opts = combine(lambda t, rows, k=key: next(
                (c for c in t["columns"] if c["name"].strip().lower() == k), None))
            if counts:
                cells.append(f'<td valign="top" width="400">{self._label(name)}'
                             f'{self._counts_pie(counts, opts, name)}</td>')
        f_name, f_counts, f_opts = combine(lambda t, rows: store.fault_column(t))
        if f_counts:
            cells.append(f'<td valign="top">{self._label(f_name)}{self._counts_pie(f_counts, f_opts, f_name, subsystem=True)}</td>')
        # open issues from before the range stay in the summary until they're resolved
        fixes = [e for t, _ in per_tab for e in store.resolutions(t, _rows(t), self.rng)]
        if not cells and not comp_counts and not fixes:
            out.append(f'<p style="color:{MUTED}">No reasons or subsystems recorded in this date range.</p>')
            return "".join(out)
        if cells:
            out.append(f'<table cellspacing="0" cellpadding="4"><tr>{"".join(cells)}</tr></table>')
        if comp_name:
            groups = {}                    # only components with at least one run
            for value in comp_opts:
                if comp_counts[value]:
                    sub, part = store.split_component(value)
                    groups.setdefault(sub, []).append((part or value, comp_counts[value], value))
            label = self._label(comp_name + " tally, by subsystem")
            if groups:
                tally = charts.ComponentTally()
                tally.set_data([(s_, charts.subsystem_color(s_), parts) for s_, parts in groups.items()])
                out.append(f'<p style="margin-top:6px">{label}{self._image(tally, WIDTH - 40)}</p>')
            else:
                out.append(f'<p style="margin-top:6px">{label}<span style="color:{MUTED}">'
                           f'No components recorded in this date range.</span></p>')
        from datetime import date as _date
        as_of = min(self.rng[1], _date.today()) if self.rng else None
        made = charts.resolution_charts(fixes, as_of)
        if made:
            donut, days, summary = made
            out.append(f'<table cellspacing="0" cellpadding="4" style="margin-top:8px"><tr>'
                       f'<td valign="top" width="400">{self._label("Resolved Issues from Failed Runs")}'
                       f'{self._image(donut)}</td>'
                       f'<td valign="top">{self._label("Days to resolve (run date → Resolved date)")}'
                       f'{self._image(days, 520)}<br><span style="font-size:12px">{esc(summary)}</span></td>'
                       f'</tr></table>')
            for resolved, title in ((True, "Resolved, by subsystem"), (False, "Unresolved, by subsystem")):
                by_sub = charts.resolved_by_subsystem(fixes, resolved)
                if by_sub is not None:
                    out.append(f'<p style="margin-top:6px">{self._label(title)}'
                               f'{self._image(by_sub, WIDTH - 40)}</p>')
        return "".join(out)

    def _bugs_section(self, tab):
        """Status of all bugs now; counts of open / new / closed / in QA; and the bugs
        opened and closed in the date range, with clickable keys."""
        rows = _rows(tab)
        made, done_col = store.created_date_column(tab), store.done_date_column(tab)
        key_col = next((c for c in tab["columns"] if any(r["links"][c["pos"]] for r in rows)), tab["columns"][0])
        summary = next((c for c in tab["columns"] if "summary" in c["name"].lower()), None)
        who = next((c for c in tab["columns"] if "assign" in c["name"].lower()), None)
        val = lambda r, c: r["values"][c["pos"]] if c else ""
        st = {id(r): store.row_status(tab, r) for r in rows}
        is_done = lambda r: charts.classify(st[id(r)]) == "done" or bool(store.parse_date(val(r, done_col)))
        open_ = [r for r in rows if not is_done(r)]
        qa = [r for r in open_ if "qa" in st[id(r)].lower().split()]
        new = [r for r in rows if made and store.in_range(val(r, made), self.rng)]
        closed = [r for r in rows if done_col and store.in_range(val(r, done_col), self.rng)]
        donut, status, colors, order = self._status_donut(tab, rows)

        def tile(label, n, color, sub):
            return (f'<td width="25%" valign="top" style="padding:8px 12px; border:1px solid {LINE}">'
                    f'<span style="font-size:11px; color:{INK2}">{esc(label)}</span><br>'
                    f'<span style="font-size:26px; font-weight:bold; color:{color}">{n}</span><br>'
                    f'<span style="font-size:10px; color:{MUTED}">{esc(sub)}</span></td>')

        tiles = ("<table width='100%' cellspacing='6' cellpadding='0'><tr>"
                 + tile("Open now", len(open_), INK, f"{len(rows) - len(open_)} of {len(rows)} done")
                 + tile("New", len(new), charts.PROGRESS, f"opened · {self.range_text}")
                 + tile("Closed", len(closed), "#0B7A0B", f"done · {self.range_text}")
                 + tile("In QA", len(qa), charts.QA, "waiting on QA now")
                 + "</tr></table>")
        out = [f'<table width="100%" cellspacing="0" cellpadding="4"><tr>'
               f'<td valign="top" width="400">{self._label("Status now (all bugs)")}{donut}</td>'
               f'<td valign="top">{self._label("New and closed · " + self.range_text)}{tiles}</td>'
               f'</tr></table>']

        def listing(title, items, date_col, bg, mark, empty):
            if not items:
                return (f'<p style="margin-top:10px">{self._label(title)}'
                        f'<span style="color:{MUTED}">{esc(empty)}</span></p>')
            head = [key_col["name"], summary["name"] if summary else "", "Status",
                    who["name"] if who else "", date_col["name"]]
            lines = [f'<p style="margin-top:10px; margin-bottom:2px">{self._label(f"{title} ({len(items)})")}</p>',
                     '<table width="100%" cellspacing="0" cellpadding="5">',
                     "<tr>" + "".join(f"<th>{esc(h)}</th>" for h in head) + "</tr>"]
            for r in sorted(items, key=lambda r: store.parse_date(val(r, date_col)) or datetime.min.date(),
                            reverse=True):
                link, key = r["links"][key_col["pos"]], val(r, key_col)
                keyh = f'<a href="{esc(link, quote=True)}">{esc(key)}</a>' if link else esc(key)
                when = store.parse_date(val(r, date_col))
                lines.append(f'<tr bgcolor="{bg}"><td>{mark}{keyh}</td><td>{esc(val(r, summary))}</td>'
                             f'<td>{esc(st[id(r)])}</td><td>{esc(val(r, who))}</td>'
                             f'<td>{f"{when.month}/{when.day}/{when.year}" if when else ""}</td></tr>')
            lines.append("</table>")
            return "".join(lines)

        if made:
            out.append(listing("New bugs", new, made, NEW_BG, "", f"No bugs were opened in {self.range_text}."))
        if done_col:
            out.append(listing("Closed bugs", closed, done_col, GOOD_BG, "<b>✓</b> ",
                               f"No bugs were closed in {self.range_text}."))
        return "".join(out)

    # ── saving ────────────────────────────────────────────────────────────────

    def height(self):
        return int(self.doc.size().height()) + 1

    def save_png(self, path):
        w, h = WIDTH, self.height()
        img = QImage(w * SCALE, h * SCALE, QImage.Format_ARGB32_Premultiplied)
        img.fill(QColor("#FFFFFF"))
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.scale(SCALE, SCALE)
        self.doc.drawContents(p)
        p.end()
        if not img.save(path, "PNG"):
            raise OSError(f"Couldn't save {path}")

    def save_pdf(self, path):
        """One tall page the same shape as the PNG, with clickable links."""
        printer = QPrinter(QPrinter.HighResolution)
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setOutputFileName(path)
        pt = 0.75                                  # CSS pixels → PDF points
        size = QPageSize(QSizeF(WIDTH * pt, self.height() * pt * 1.01), QPageSize.Point, "Snapshot")
        printer.setPageLayout(QPageLayout(size, QPageLayout.Portrait, QMarginsF(0, 0, 0, 0)))
        doc = self.doc.clone()
        for name, img in self.images.items():
            doc.addResource(QTextDocument.ImageResource, QUrl(name), img)
        doc.setPageSize(QSizeF(WIDTH, self.height() * 1.01))
        doc.print_(printer)
