"""
Snapshot export: the dashboard's cards for a date range, saved as PNG (to paste
into Slack or email) and a matching PDF (where run and bug links are clickable).

Each card is drawn from the same layout as on the dashboard (layout.py, drawn by
blocks.py), so a change in ✎ Customize shows up in the export too. Which cards
and charts go in is the layout's "In export" choice, which the export dialog can
change for one export.

Page styles:
  • wide  – cards in 2–3 columns, landscape-shaped, so Slack shows it large
  • tall  – one column, like the 1.1 export
  • cards – one PNG per card (and one PDF with all of them)
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
STYLES = {"wide": "Wide — best for Slack", "tall": "Tall — one column", "cards": "One image per card"}
TALL_W, CARD_W = 1040, 860      # card widths for the tall and the wide/cards styles
TARGET_ASPECT = 1.6             # width : height Slack shows large (16:10)

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
    def __init__(self, data, rng, range_text, settings=None, layouts=None, picks=None, style="wide"):
        self.data, self.rng, self.range_text, self.style = data, rng, range_text, style
        self.settings = settings or {}
        charts.set_overrides(store.color_overrides(self.settings))
        charts.register(data)            # same colors as the dashboard
        if layouts is None:
            layouts = {t["title"]: lay.layout_for(t, self.settings) for t in data["tabs"]}
        self.ctx = blocks.Ctx(rng, range_text, export=True, settings=self.settings)
        self.card_w = TALL_W if style == "tall" else CARD_W
        self.cards = []                  # [(title, QTextDocument, {name: QImage})]
        for card, tabs, chosen in plan(data, layouts, picks):
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
            return label + image(c, width)

        title = combined_title(card, tabs)
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
        """Place the cards in columns. Wide: try 1–4 columns and keep the one
        closest to 16:10, filling the shortest column first."""
        heights = [math.ceil(doc.size().height()) for _, doc, _ in self.cards]
        best = None
        n_max = 1 if self.style in ("tall", "cards") else min(4, max(len(self.cards), 1))
        for n in range(1, n_max + 1):
            cols, spots = [0] * n, []
            for h in heights:
                c = cols.index(min(cols))
                spots.append((c, cols[c]))
                cols[c] += h + GAP
            w = 2 * MARGIN + n * self.card_w + (n - 1) * GAP
            h = HEADER_H + max(max(cols) - GAP, 0) + 2 * MARGIN
            score = abs(math.log((w / max(h, 1)) / TARGET_ASPECT))
            if best is None or score < best[0]:
                best = (score, w, h, spots)
        _, self.width, self.page_h, spots = best
        self.spots = [(MARGIN + c * (self.card_w + GAP), MARGIN + HEADER_H + y) for c, y in spots]
        self.heights = heights

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
