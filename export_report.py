"""
Snapshot export: a one-page summary of testing for a date range, saved as a PNG
(to paste into Slack or email) and a matching PDF (where run and bug links are
clickable).

Both files come from one layout: an HTML page with the charts drawn as images,
so they look the same. It covers:
  • end-to-end run trackers: status donut, Coder vs Atlantis, and (for the
    trackers in RUN_LIST_FOR) the list of runs with links
  • Error Analysis: reason and subsystem pies, component tally
  • bug trackers: status donut, and the bugs closed in the date range, highlighted
"""

import html
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
RUN_LIST_FOR = ("vitalone",)    # end-to-end trackers whose runs are listed with links
INK, INK2, MUTED, LINE, GOOD_BG = "#1A1A18", "#52514E", "#8A8984", "#E6E4E0", "#E3F4E1"

esc = html.escape


def _rows(tab):
    return [r for r in tab["rows"] if not store.is_blank(r)]

def _in_range(tab, rng):
    dcol = store.date_column(tab)
    rows = _rows(tab)
    if dcol is None or rng is None:
        return rows
    return [r for r in rows if store.in_range(r["values"][dcol["pos"]], rng)]

def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


class Snapshot:
    def __init__(self, data, rng, range_text, settings=None):
        self.data, self.rng, self.range_text = data, rng, range_text
        self.settings = settings or {}
        self.images = {}                 # resource name → QImage
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
        names = sorted(groups, key=lambda k: (k == "", -sum(groups[k].values()), k))[:10]
        b = charts.StackedBars()
        b.set_data([(n, [(v, groups[n][v], colors.get(v, charts.PENDING)) for v in order]) for n in names],
                   blank=store.blank_label(col))
        return self._image(b, width)

    def _category_pie(self, rows, col, palette=None):
        counts = Counter(p.strip() for r in rows for p in store.parts(col, r["values"][col["pos"]]) if p.strip())
        options = list(palette or col["options"]) + sorted(v for v in counts if v not in (palette or col["options"]))
        palette = dict(palette or {})
        free = iter(c for c in charts.CATEGORICAL if c not in palette.values())
        for v in options:
            palette.setdefault(v, next(free, charts.PENDING))
        total = sum(counts.values())
        return self._donut(counts, palette, options, (str(total), "run" if total == 1 else "runs"))

    # ── page ──────────────────────────────────────────────────────────────────

    def _build(self):
        tabs = self.data["tabs"]
        runs = [t for t in tabs if store.platform_column(t)]
        analysis = [t for t in runs if store.fault_column(t) or store.component_column(t)]
        bugs = [t for t in tabs if store.done_date_column(t) and "bug" in t["title"].lower()]

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
        for tab in analysis:
            body.append(self._heading(f"{tab['title']} — Error Analysis"))
            body.append(self._analysis_section(tab))
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
        plat = store.derived_columns(tab).get(store.PLATFORM)
        if plat:
            cells.append(f'<td valign="top">{self._label("Runs on Coder vs Atlantis")}'
                         f'{self._bars(tab, rows, plat, colors, order, 520)}</td>')
        out.append(f'<table cellspacing="0" cellpadding="4"><tr>{"".join(cells)}</tr></table>')
        if any(w in tab["title"].lower() for w in RUN_LIST_FOR):
            out.append(self._run_list(tab, rows, colors))
        return "".join(out)

    def _run_list(self, tab, rows, colors):
        dcol, name_col = store.date_column(tab), store.platform_column(tab)
        reason = next(iter(store.pie_columns(tab, rows, self.settings)), None)
        fault = store.fault_column(tab)
        head = ["Date", "Run", "Ran on", "Status"] + ([reason["name"]] if reason else []) + \
               ([fault["name"]] if fault else [])
        lines = [f'<p style="margin-top:8px; margin-bottom:2px">{self._label("Runs")}</p>',
                 '<table width="100%" cellspacing="0" cellpadding="5" style="border-collapse:collapse">',
                 "<tr>" + "".join(f"<th>{esc(h)}</th>" for h in head) + "</tr>"]
        key = lambda r: (store.parse_date(r["values"][dcol["pos"]]) if dcol else None) or datetime.min.date()
        for i, r in enumerate(sorted(rows, key=key, reverse=True)):
            name, link = r["values"][name_col["pos"]], r["links"][name_col["pos"]]
            run = f'<a href="{esc(link, quote=True)}">{esc(name)}</a>' if link else esc(name)
            st = store.row_status(tab, r)
            dot = f'<span style="color:{colors.get(st, charts.PENDING)}">●</span> ' if st else ""
            cells = [r["values"][dcol["pos"]] if dcol else "", None, store.row_platform(tab, r), None]
            vals = [esc(cells[0]), run, esc(cells[2]), dot + esc(st or "—")]
            if reason:
                vals.append(esc(r["values"][reason["pos"]] or "—"))
            if fault:
                vals.append(esc(r["values"][fault["pos"]] or "—"))
            bg = ' bgcolor="#F7F7F5"' if i % 2 else ""
            lines.append(f"<tr{bg}>" + "".join(f"<td>{v}</td>" for v in vals) + "</tr>")
        lines.append("</table>")
        linked = sum(bool(r["links"][name_col["pos"]]) for r in rows)
        if linked:
            lines.append(f'<p style="color:{MUTED}; font-size:11px">Run names with links open the run '
                         f'in Atlantis (in the PDF).</p>')
        return "".join(lines)

    def _analysis_section(self, tab):
        rows = _in_range(tab, self.rng)
        failed = sum(charts.classify(store.row_status(tab, r)) in ("blocked", "cancelled", "warning") for r in rows)
        out = [f'<p style="color:{MUTED}; margin-bottom:4px">{_plural(failed, "failed run")} in '
               f'{esc(self.range_text)} · blank cells are left out</p>']
        if not rows:
            out.append(f'<p style="color:{MUTED}">No runs in this date range.</p>')
            return "".join(out)
        fault, comp = store.fault_column(tab), store.component_column(tab)
        subsystems = list(dict.fromkeys(store.split_component(o)[0] for o in (comp or {}).get("options", [])))
        sub_colors = {s: charts.CATEGORICAL[i % len(charts.CATEGORICAL)] for i, s in enumerate(subsystems)}
        cells = [f'<td valign="top" width="400">{self._label(pc["name"])}{self._category_pie(rows, pc)}</td>'
                 for pc in store.pie_columns(tab, rows, self.settings)]
        if fault:
            cells.append(f'<td valign="top">{self._label(fault["name"])}'
                         f'{self._category_pie(rows, fault, sub_colors)}</td>')
        out.append(f'<table cellspacing="0" cellpadding="4"><tr>{"".join(cells)}</tr></table>')
        if comp:
            counts = Counter(p.strip() for r in rows for p in store.parts(comp, r["values"][comp["pos"]]) if p.strip())
            groups = {}                    # only components with at least one run
            for value in list(comp["options"]) + sorted(v for v in counts if v not in comp["options"]):
                if counts[value]:
                    sub, part = store.split_component(value)
                    groups.setdefault(sub, []).append((part or value, counts[value], value))
            label = self._label(comp["name"] + " tally, by subsystem")
            if groups:
                tally = charts.ComponentTally()
                tally.set_data([(s, sub_colors.get(s, charts.PENDING), parts) for s, parts in groups.items()])
                out.append(f'<p style="margin-top:6px">{label}{self._image(tally, WIDTH - 40)}</p>')
            else:
                out.append(f'<p style="margin-top:6px">{label}<span style="color:{MUTED}">'
                           f'No components recorded in this date range.</span></p>')
        return "".join(out)

    def _bugs_section(self, tab):
        rows = _rows(tab)
        done_col = store.done_date_column(tab)
        key_col = next((c for c in tab["columns"] if any(r["links"][c["pos"]] for r in rows)), tab["columns"][0])
        summary = next((c for c in tab["columns"] if "summary" in c["name"].lower()), None)
        closed = [r for r in rows if store.in_range(r["values"][done_col["pos"]], self.rng)]
        donut, status, colors, order = self._status_donut(tab, rows)
        out = [f'<table cellspacing="0" cellpadding="4"><tr>'
               f'<td valign="top" width="400">{self._label("Status now (all bugs)")}{donut}</td>'
               f'<td valign="top"><p style="font-size:34px; font-weight:bold; color:#0B7A0B; margin:0">'
               f'{len(closed)}</p><p style="font-size:14px; margin-top:0">'
               f'{"bug" if len(closed) == 1 else "bugs"} closed in <b>{esc(self.range_text)}</b></p>'
               f'<p style="color:{MUTED}">{len(rows) - sum(bool(store.parse_date(r["values"][done_col["pos"]])) for r in rows)} '
               f'of {len(rows)} bugs have no {esc(done_col["name"])} yet.</p></td></tr></table>']
        if closed:
            out += [f'<p style="margin-top:8px; margin-bottom:2px">{self._label("Closed in this range")}</p>',
                    '<table width="100%" cellspacing="0" cellpadding="5">',
                    "<tr>" + "".join(f"<th>{esc(h)}</th>" for h in
                                     [key_col["name"], summary["name"] if summary else "", "Status",
                                      done_col["name"]]) + "</tr>"]
            for r in sorted(closed, key=lambda r: store.parse_date(r["values"][done_col["pos"]]), reverse=True):
                link = r["links"][key_col["pos"]]
                key = r["values"][key_col["pos"]]
                keyh = f'<a href="{esc(link, quote=True)}">{esc(key)}</a>' if link else esc(key)
                st = store.row_status(tab, r)
                out.append(f'<tr bgcolor="{GOOD_BG}"><td><b>✓</b> {keyh}</td>'
                           f'<td>{esc(r["values"][summary["pos"]]) if summary else ""}</td>'
                           f'<td>{esc(st)}</td><td>{esc(r["values"][done_col["pos"]])}</td></tr>')
            out.append("</table>")
        else:
            out.append(f'<p style="color:{MUTED}">No bugs were closed in {esc(self.range_text)}.</p>')
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
