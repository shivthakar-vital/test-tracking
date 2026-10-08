"""
Snapshot export: the dashboard's cards for a date range, saved as PNG (to paste
into Slack or email) and a matching PDF (where run and bug links are clickable).

Each card is drawn from the same layout as on the dashboard (layout.py, drawn by
blocks.py), so a change in ✎ Customize shows up in the export too. Which cards
and charts go in is the layout's "In export" choice, which the export dialog can
change for one export.

What gets saved:
  • slack – a 16:9 summary image (headline numbers + the key donuts) that reads well
            at Slack's preview size, plus the full report (tall PNG + PDF)
  • tall  – just the full report: every card in one column (PNG + PDF)
  • cards – one PNG per card (and the full-report PDF)
The PDF is always the full report, with clickable links.
"""

import html, math, re
from datetime import datetime

from PyQt5.QtCore import Qt, QUrl, QSizeF, QMarginsF, QRectF
from PyQt5.QtGui import (QTextDocument, QImage, QPainter, QPageSize, QPageLayout, QColor, QFont,
                         QPen, QPainterPath)
from PyQt5.QtPrintSupport import QPrinter
from PyQt5.QtWidgets import QWidget

import blocks
import charts
import layout as lay
import tracker_store as store

SCALE = 2                       # charts and the PNG are drawn at 2× for sharp text
INK, INK2, MUTED, LINE = "#1A1A18", "#52514E", "#8A8984", "#E6E4E0"
PAGE_BG, CARD_BG = "#F5F5F2", "#FFFFFF"
MARGIN, GAP, PAD = 28, 18, 18   # page margin, space between cards, padding inside a card
HEADER_H = 64
STYLES = {"slack": "Slack summary + full report", "tall": "Full report only",
          "cards": "One image per card"}
CARD_W = 1100                   # card width in the full report

esc = html.escape


def _rows(tab):
    return [r for r in tab["rows"] if not store.is_blank(r)]

def _in_range(tab, rng):
    dcol = store.date_column(tab)
    rows = _rows(tab)
    if dcol is None or rng is None:
        return rows
    return [r for r in rows if store.in_range(r["values"][dcol["pos"]], rng)]


# ── what goes in ──────────────────────────────────────────────────────────────

def card_key(tab, card):
    return f"{tab['title']}|{card['id']}"

def block_key(tab, card, b):
    return f"{tab['title']}|{card['id']}|{b['id']}"

def plan(data, layouts, picks=None):
    """[(card, [tabs], [blocks])] in page order. `layouts`: {tab title: layout}.
    `picks`: {key: bool} from the export dialog, overriding the layouts' choices.
    Cards marked "combine" (e.g. Error Analysis) are merged across tabs."""
    picks = picks or {}
    on = lambda key, default: picks.get(key, default)
    out, merged = [], {}
    for tab in data["tabs"]:
        if not tab["rows"]:
            continue
        lo = layouts.get(tab["title"])
        if lo is None:
            continue
        for card in lo["cards"]:
            if card["combine"] and card["id"] in merged:
                merged[card["id"]][1].append(tab)
                continue
            if not on(card_key(tab, card), card["export"]):
                if card["combine"]:
                    merged[card["id"]] = (card, [], [])    # others of this kind stay out too
                continue
            chosen = [b for b in card["blocks"] if on(block_key(tab, card, b), b["export"])]
            entry = (card, [tab], chosen)
            if card["combine"]:
                merged[card["id"]] = entry
            out.append(entry)
    return [e for e in out if e[2]]

def combined_title(card, tabs):
    names = " + ".join(blocks.short_name(t["title"]) for t in tabs)
    if len(tabs) == 1:
        return lay.card_title(tabs[0], card)
    if card["id"] == "analysis":
        return f"Error Analysis — {names}"
    return lay.card_title({"title": names}, card)


# ── drawing ───────────────────────────────────────────────────────────────────

class Snapshot:
    def __init__(self, data, rng, range_text, settings=None, layouts=None, picks=None, style="slack"):
        self.data, self.rng, self.range_text, self.style = data, rng, range_text, style
        self.settings = settings or {}
        charts.set_overrides(store.color_overrides(self.settings))
        charts.register(data)            # same colors as the dashboard
        if layouts is None:
            layouts = {t["title"]: lay.layout_for(t, self.settings) for t in data["tabs"]}
        self.ctx = blocks.Ctx(rng, range_text, export=True, settings=self.settings)
        self.card_w = CARD_W
        self.cards = []                  # [(title, QTextDocument, {name: QImage})]
        self.kpis, self.donuts = [], []  # for the Slack summary
        for card, tabs, chosen in plan(data, layouts, picks):
            self.kpis += [(_kind(card, tabs), k) for k in _kpis(card, tabs, self.ctx)]
            self.cards.append(self._card(card, tabs, chosen))
        self._arrange()

    # ── one card ──────────────────────────────────────────────────────────────

    def _card(self, card, tabs, chosen):
        images = {}
        inner = self.card_w - 2 * PAD
        prefix = f"c{len(self.cards)}i"

        def image(widget, width=None):
            widget.setAttribute(Qt.WA_DontShowOnScreen)
            if width and not isinstance(widget, charts.DonutChart):
                widget.setFixedWidth(int(width))
            widget.ensurePolished()
            if isinstance(widget, charts.ComponentTally):
                widget.resize(int(width or inner), 100)
                widget._relayout()
            w, h = widget.width(), widget.height()
            img = QImage(w * SCALE, h * SCALE, QImage.Format_ARGB32_Premultiplied)
            img.setDevicePixelRatio(SCALE)
            img.fill(QColor(CARD_BG))
            p = QPainter(img)
            widget.render(p, flags=QWidget.RenderFlags(QWidget.DrawChildren))
            p.end()
            name = f"{prefix}{len(images)}.png"
            images[name] = img
            return f'<img src="{name}" width="{w}" height="{h}">'

        def natural(part):
            if part.full:
                return inner
            return part.content.width() if isinstance(part.content, charts.DonutChart) else None

        def part_html(part, width):
            label = (f'<span style="font-size:11px; font-weight:bold; color:{MUTED}">'
                     f'{esc(part.title.upper())}</span><br>') if part.title else ""
            c = part.content
            if isinstance(c, list):
                return "".join(f'<p style="margin-top:0; margin-bottom:6px">{part_html(p, width)}</p>'
                               for p in c)
            if isinstance(c, str):
                return label + c
            tag = image(c, width)
            if isinstance(c, charts.DonutChart):
                self.donuts.append((_kind(card, tabs), len(self.cards), short, part.title,
                                    images[f"{prefix}{len(images) - 1}.png"]))
            return label + tag

        title = combined_title(card, tabs)
        short = blocks.short_name(title).replace(" — ", " · ")
        note = blocks.card_note(card, tabs, self.ctx)
        out = [f'<p style="margin:0 0 4px 0"><b style="font-size:16px; color:{INK}">{esc(title)}</b>'
               f'&nbsp;&nbsp;<span style="font-size:12px; color:{MUTED}">{esc(note)}</span></p>']
        drawn = []
        for b in chosen:
            try:
                parts = blocks.render(b, tabs, self.ctx)
            except Exception as exc:                     # one broken chart doesn't stop the export
                parts = [blocks.Part(lay.auto_title(tabs[0], b),
                                     f'<span style="color:{MUTED}">Couldn\'t draw this '
                                     f'({esc(type(exc).__name__)}: {esc(str(exc))})</span>', wide=True)]
            if parts:
                drawn.append((b, parts))
        if not drawn:
            dated = any(self.ctx.dated(t) for t in tabs)
            out.append(f'<p style="color:{MUTED}">' + ("Nothing in this date range." if dated
                                                      else "Nothing to show.") + "</p>")
        MIN_WIDE, SP = 340, 24
        for row in blocks.rows_of(drawn):
            parts = [p for _, ps in row for p in ps]
            # lay the row's parts out in lines that fit the card: fixed-size ones (donuts)
            # keep their width, the others share what's left (at least MIN_WIDE each)
            lines, cur, used = [], [], 0
            for p in parts:
                w = natural(p) or MIN_WIDE
                if cur and used + SP + w > inner:
                    lines.append(cur)
                    cur, used = [], 0
                cur.append(p)
                used += (SP if used else 0) + w
            if cur:
                lines.append(cur)
            for line in lines:
                fixed = sum(natural(p) or 0 for p in line)
                wide = [p for p in line if natural(p) is None]
                spare = inner - fixed - SP * (len(line) - 1)
                cells = []
                for i, p in enumerate(line):
                    w = natural(p) or max(MIN_WIDE, spare // max(len(wide), 1))
                    pad = SP if i < len(line) - 1 else 0
                    cells.append(f'<td valign="top" width="{int(w) + pad}" style="padding-right:{pad}px">'
                                 f'{part_html(p, w)}</td>')
                out.append(f'<table cellspacing="0" cellpadding="0" style="margin-top:12px"><tr>'
                           f'{"".join(cells)}</tr></table>')

        doc = QTextDocument()
        font = QFont()
        font.setPixelSize(13)
        doc.setDefaultFont(font)
        doc.setDocumentMargin(PAD)
        doc.setDefaultStyleSheet(f'''
            a {{ color: #2F5E9E; text-decoration: underline; }}
            th {{ color: {MUTED}; font-size: 11px; font-weight: bold; text-align: left; }}
            td {{ color: {INK}; font-size: 13px; }}''')
        for name, img in images.items():
            doc.addResource(QTextDocument.ImageResource, QUrl(name), img)
        doc.setHtml("".join(out))
        doc.setTextWidth(self.card_w)
        return title, doc, images

    # ── page ──────────────────────────────────────────────────────────────────

    def _arrange(self):
        """The full report: cards stacked in one column."""
        self.heights = [math.ceil(doc.size().height()) for _, doc, _ in self.cards]
        self.spots, y = [], MARGIN + HEADER_H
        for h in self.heights:
            self.spots.append((MARGIN, y))
            y += h + GAP
        self.width = 2 * MARGIN + self.card_w
        self.page_h = y - (GAP if self.cards else 0) + MARGIN

    def _synced(self):
        try:
            return f"{datetime.fromisoformat(self.data['fetched_at']):%b %-d, %-I:%M %p}"
        except (KeyError, ValueError):
            return "the last sync"

    def _header_doc(self, width):
        doc = QTextDocument()
        font = QFont()
        font.setPixelSize(13)
        doc.setDefaultFont(font)
        doc.setDocumentMargin(0)
        doc.setHtml(f'''
<table width="100%" cellspacing="0" cellpadding="0"><tr>
  <td><span style="font-size:24px; font-weight:bold; color:{INK}">{esc(self.data["title"])}</span><br>
      <span style="font-size:15px; color:{INK2}">Testing snapshot · <b>{esc(self.range_text)}</b></span></td>
  <td align="right" valign="bottom" style="color:{MUTED}; font-size:11px">
      Generated {datetime.now():%b %-d, %Y at %-I:%M %p}<br>Test Tracker · sheet data from {self._synced()}</td>
</tr></table>''')
        doc.setTextWidth(width)
        return doc

    def _paint(self, p, cards, spots, width, height):
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(QRectF(0, 0, width, height), QColor(PAGE_BG))
        p.save()
        p.translate(MARGIN, MARGIN)
        self._header_doc(width - 2 * MARGIN).drawContents(p)
        p.restore()
        for (title, doc, _), (x, y) in zip(cards, spots):
            h = math.ceil(doc.size().height())
            path = QPainterPath()
            path.addRoundedRect(QRectF(x + 0.5, y + 0.5, self.card_w - 1, h - 1), 10, 10)
            p.setPen(QPen(QColor(LINE), 1))
            p.setBrush(QColor(CARD_BG))
            p.drawPath(path)
            p.save()
            p.translate(x, y)
            doc.drawContents(p, QRectF(0, 0, self.card_w, h))
            p.restore()

    def image(self, cards=None, spots=None, size=None):
        cards = self.cards if cards is None else cards
        spots = self.spots if spots is None else spots
        w, h = size or (self.width, self.page_h)
        img = QImage(int(w * SCALE), int(h * SCALE), QImage.Format_ARGB32_Premultiplied)
        img.fill(QColor(PAGE_BG))
        p = QPainter(img)
        p.scale(SCALE, SCALE)
        self._paint(p, cards, spots, w, h)
        p.end()
        return img

    def single_cards(self):
        """[(title, QImage)] — each card on its own, with the page header."""
        out = []
        for i, card in enumerate(self.cards):
            size = (self.card_w + 2 * MARGIN, HEADER_H + self.heights[i] + 2 * MARGIN)
            out.append((card[0], self.image([card], [(MARGIN, MARGIN + HEADER_H)], size)))
        return out

    # ── saving ────────────────────────────────────────────────────────────────

    def save_png(self, path):
        """Saves the PNG (or one per card); returns the paths written."""
        if self.style == "slack":
            full = path[:-4] + " - full report.png"
            if not self.summary_image().save(path, "PNG") or not self.image().save(full, "PNG"):
                raise OSError(f"Couldn't save {path}")
            return [path, full]
        if self.style == "cards" and self.cards:
            base, paths = path[:-4], []
            for i, (title, img) in enumerate(self.single_cards(), start=1):
                safe = re.sub(r'[\\/:*?"<>|]', "-", title)
                p = f"{base} - {i} {safe}.png"
                if not img.save(p, "PNG"):
                    raise OSError(f"Couldn't save {p}")
                paths.append(p)
            return paths
        if not self.image().save(path, "PNG"):
            raise OSError(f"Couldn't save {path}")
        return [path]

    def save_pdf(self, path):
        """One page the same shape as the PNG, with clickable links."""
        printer = QPrinter(QPrinter.HighResolution)
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setOutputFileName(path)
        pt = 0.75                                  # CSS pixels → PDF points
        size = QPageSize(QSizeF(self.width * pt, self.page_h * pt), QPageSize.Point, "Snapshot")
        printer.setPageLayout(QPageLayout(size, QPageLayout.Portrait, QMarginsF(0, 0, 0, 0)))
        p = QPainter(printer)
        page = printer.pageRect(QPrinter.DevicePixel)
        p.scale(page.width() / self.width, page.height() / self.page_h)
        self._paint(p, self.cards, self.spots, self.width, self.page_h)
        p.end()

# ── Slack summary ─────────────────────────────────────────────────────────────

SUM_W, SUM_H, SUM_SCALE = 1280, 720, 2.5       # 16:9, saved at 3200 × 1800
MAX_TILES, MAX_CHARTS = 6, 6

def _kind(card, tabs):
    """Reading order on the summary: runs, then error analysis, then bugs, then the rest."""
    if card["id"] == "analysis":
        return 1
    if store.is_run_tab(tabs[0]):
        return 0
    return 2 if store.is_bug_tab(tabs[0]) else 3

def _kpis(card, tabs, ctx):
    """Headline numbers for a card: [(label, value, sub, color)]."""
    tab = tabs[0]
    classes = lambda t, rows: [charts.classify(store.row_status(t, r)) for r in rows]
    if card["id"] == "analysis":
        if store.is_ticket_tab(tab):
            rows = store.issues(tab, ctx.rows(tab))
            open_ = sum(c != "done" for c in classes(tab, rows))
            return [("Open tickets", open_, f"of {len(rows)} · {blocks.short_name(tab['title'])}", INK)]
        per = {t["title"]: sum(c in store.FAILED for c in classes(t, store.issues(t, ctx.rows(t)))) for t in tabs}
        fixes = [e for t in tabs for e in store.resolutions(t, ctx.all_rows(t), ctx.rng)]
        open_ = sum(e[1] is None for e in fixes)
        split = " · ".join(f"{blocks.short_name(k)} {v}" for k, v in per.items()) if len(per) > 1 else ctx.range_text
        return [("Failed-run issues", sum(per.values()), split, INK),
                ("Unresolved issues", open_, f"{len(fixes) - open_} of {len(fixes)} resolved",
                 charts.SERIOUS if open_ else charts.GOOD)]
    if store.is_bug_tab(tab):
        bugs = blocks._Bugs(tab, ctx)
        return [("New bugs", len(bugs.new), ctx.range_text, charts.PROGRESS),
                ("Bugs closed", len(bugs.closed), ctx.range_text, "#0B7A0B"),
                ("Open bugs", len(bugs.open), f"{len(bugs.qa)} waiting on QA", INK)]
    if store.is_run_tab(tab):
        out = []
        for t in tabs:
            rows = ctx.rows(t)
            cl = classes(t, rows)
            done, failed = cl.count("done"), sum(c in store.FAILED for c in cl)
            sub = f"{done} done · {failed} failed" if rows else f"none · {ctx.range_text}"
            out.append((f"{blocks.short_name(t['title'])} runs", len(rows), sub, INK))
        return out
    n = sum(len(ctx.rows(t)) for t in tabs)
    return [(combined_title(card, tabs), n, "rows", INK)]


def _summary_paint(snap, p):
    from charts import _font
    W, H, X = SUM_W, SUM_H, 40
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.setRenderHint(QPainter.TextAntialiasing)
    p.fillRect(QRectF(0, 0, W, H), QColor(PAGE_BG))

    def text(rect, s, px, color, bold=False, flags=Qt.AlignLeft | Qt.AlignVCenter):
        p.setFont(_font(px, bold))
        p.setPen(QColor(color))
        fm = p.fontMetrics()
        p.drawText(rect, flags, fm.elidedText(s, Qt.ElideRight, int(rect.width())))

    def panel(rect):
        path = QPainterPath()
        path.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)
        p.setPen(QPen(QColor(LINE), 1))
        p.setBrush(QColor(CARD_BG))
        p.drawPath(path)

    # header
    text(QRectF(X, 26, W * 0.6, 36), snap.data["title"], 28, INK, True)
    text(QRectF(X, 62, W * 0.6, 24), f"Testing snapshot · {snap.range_text}", 17, INK2)
    right = Qt.AlignRight | Qt.AlignVCenter
    text(QRectF(W * 0.5, 34, W * 0.5 - X, 20), f"Generated {datetime.now():%b %-d, %Y at %-I:%M %p}", 12, MUTED,
         flags=right)
    text(QRectF(W * 0.5, 54, W * 0.5 - X, 20), f"Sheet data from {snap._synced()}", 12, MUTED, flags=right)

    # headline numbers
    tiles = [k for _, k in sorted(snap.kpis, key=lambda x: x[0])][:MAX_TILES]
    top, th, gap = 104, 116, 14
    if tiles:
        tw = (W - 2 * X - gap * (len(tiles) - 1)) / len(tiles)
        for i, (label, value, sub, color) in enumerate(tiles):
            r = QRectF(X + i * (tw + gap), top, tw, th)
            panel(r)
            text(QRectF(r.x() + 18, r.y() + 14, tw - 36, 20), label, 14, INK2, True)
            text(QRectF(r.x() + 18, r.y() + 36, tw - 36, 50), str(value), 44, color, True)
            text(QRectF(r.x() + 18, r.y() + 86, tw - 36, 18), sub, 13, MUTED)

    # key donuts: one from each card in turn, then seconds…
    by_card = {}
    for kind, card_i, card_name, title, img in snap.donuts:
        by_card.setdefault((kind, card_i), []).append((kind, card_i, card_name, title, img))
    picked, k = [], 0
    while len(picked) < MAX_CHARTS and any(len(v) > k for v in by_card.values()):
        for v in by_card.values():
            if len(v) > k and len(picked) < MAX_CHARTS:
                picked.append(v[k])
        k += 1
    picked = [x[2:] for x in sorted(picked, key=lambda x: (x[0], x[1]))]
    y0 = top + th + 18
    y1 = H - 44
    if picked:
        cols = min(3, len(picked))
        rows = math.ceil(len(picked) / cols)
        ph = (y1 - y0 - gap * (rows - 1)) / rows
        for i, (card_name, title, img) in enumerate(picked):
            row, col = divmod(i, cols)
            in_row = min(cols, len(picked) - row * cols)        # a short last row fills the width
            pw = (W - 2 * X - gap * (in_row - 1)) / in_row
            r = QRectF(X + col * (pw + gap), y0 + row * (ph + gap), pw, ph)
            panel(r)
            text(QRectF(r.x() + 18, r.y() + 12, pw - 36, 16), card_name.upper(), 11, MUTED, True)
            text(QRectF(r.x() + 18, r.y() + 29, pw - 36, 20), title or "", 15, INK, True)
            iw, ih = img.width() / SCALE, img.height() / SCALE
            box = QRectF(r.x() + 12, r.y() + 54, pw - 24, ph - 62)
            s = min(box.width() / iw, box.height() / ih, 1.5)
            dst = QRectF(box.x() + (box.width() - iw * s) / 2, box.y() + (box.height() - ih * s) / 2,
                         iw * s, ih * s)
            p.drawImage(dst, img)
    else:
        text(QRectF(X, y0, W - 2 * X, 40), "No charts ticked for the export.", 15, MUTED)

    text(QRectF(X, H - 34, W * 0.7, 20),
         "Full details, run lists and clickable links are in the full report (PDF).", 12, MUTED)
    text(QRectF(W * 0.5, H - 34, W * 0.5 - X, 20), "Test Tracker", 12, MUTED, flags=right)


def _summary_image(snap):
    img = QImage(int(SUM_W * SUM_SCALE), int(SUM_H * SUM_SCALE), QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor(PAGE_BG))
    p = QPainter(img)
    p.scale(SUM_SCALE, SUM_SCALE)
    _summary_paint(snap, p)
    p.end()
    return img

Snapshot.summary_image = _summary_image
