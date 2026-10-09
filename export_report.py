"""
Snapshot export: the dashboard's cards for a date range, saved as PNG (to paste
into Slack or email) and a matching PDF (where run and bug links are clickable).

Each card is drawn from the same layout as on the dashboard (layout.py, drawn by
blocks.py), so a change in ✎ Customize shows up in the export too. Which cards
and charts go in is the layout's "In export" choice, which the export dialog can
change for one export.

What gets saved:
  • wide  – cards in columns. Arranged automatically (closest to 16:10), or the way
            you arranged them in the export dialog: which card in which column, and how
            wide each column is. Columns always end flush, so unticking a card never
            leaves a hole.
  • slack – a 16:9 summary image (headline numbers + the key donuts) that reads well
            at Slack's preview size, plus the wide page
  • tall  – every card in one column
  • cards – one PNG per card
The PDF matches the page (with clickable links).
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
STYLES = {"wide": "Wide (arrange it yourself)", "slack": "Slack summary + wide page",
          "tall": "One column", "cards": "One image per card"}
CARD_W = 1100                   # card width in the one-column layouts
WIDE_W = 860                    # card width when the wide layout is arranged automatically
MIN_COL_W = 480                 # narrowest column the wide layout allows

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

def _auto_columns(cards):
    """Split [(key, height)] into columns (keeping their order within each column):
    page shape close to 16:10, and columns of nearly equal height, so little space is
    left empty. Tries every split for up to 8 cards, else fills the shortest column."""
    import itertools
    best = None
    for n in range(1, min(4, max(len(cards), 1)) + 1):
        choices = itertools.product(range(n), repeat=len(cards)) if len(cards) <= 8 else [None]
        for assign in choices:
            cols, tops = [[] for _ in range(n)], [0] * n
            for i, (k, h) in enumerate(cards):
                j = assign[i] if assign else tops.index(min(tops))
                cols[j].append(k)
                tops[j] += h + GAP
            if any(not c for c in cols):
                continue
            bottom = max(tops)
            w = 2 * MARGIN + n * WIDE_W + (n - 1) * GAP
            h = HEADER_H + bottom + 2 * MARGIN
            empty = sum(bottom - t for t in tops) / (n * bottom)
            score = abs(math.log((w / h) / 1.6)) + 2.5 * empty
            if best is None or score < best[0]:
                best = (score, cols)
    return best[1] if best else []


class Snapshot:
    """arrangement (wide style): {"page_width": px, "columns": [{"share": 1.0, "cards": [card keys]}]}
    — which cards go in which column, top to bottom, and how wide each column is.
    Cards it doesn't mention go to the shortest column; columns left empty are dropped,
    and the last card of each column stretches so the columns end flush."""

    def __init__(self, data, rng, range_text, settings=None, layouts=None, picks=None, style="wide",
                 arrangement=None):
        self.data, self.rng, self.range_text, self.style = data, rng, range_text, style
        self.settings = settings or {}
        charts.set_overrides(store.color_overrides(self.settings))
        charts.register(data)            # same colors as the dashboard
        if layouts is None:
            layouts = {t["title"]: lay.layout_for(t, self.settings) for t in data["tabs"]}
        self.ctx = blocks.Ctx(rng, range_text, export=True, settings=self.settings,
                              sheet_url=data.get("url", ""))
        self.kpis, self.donuts = [], []  # for the Slack summary
        self.planned = []
        for i, (card, tabs, chosen) in enumerate(plan(data, layouts, picks)):
            self.kpis += [(_kind(card, tabs), k) for k in _kpis(card, tabs, self.ctx)]
            self.planned.append({"key": card_key(tabs[0], card), "card": card, "tabs": tabs,
                                 "chosen": chosen, "i": i})
        self.cards = []                  # rendered: {key, title, doc, x, y, w, h, draw_h}
        if style in ("wide", "slack"):
            self._arrange_columns(arrangement)
        else:
            self._arrange_single()

    def card_titles(self):
        """{card key: title} for every card going into the export (for the arrange editor)."""
        return {p["key"]: combined_title(p["card"], p["tabs"]) for p in self.planned}

    # ── page layouts ──────────────────────────────────────────────────────────

    def _render(self, p, width):
        title, doc = self._card(p["card"], p["tabs"], p["chosen"], width, p["i"])
        return {"key": p["key"], "title": title, "doc": doc, "w": width,
                "h": math.ceil(doc.size().height())}

    def _arrange_single(self):
        y = MARGIN + HEADER_H
        for p in self.planned:
            c = self._render(p, CARD_W)
            c.update(x=MARGIN, y=y, draw_h=c["h"])
            self.cards.append(c)
            y += c["h"] + GAP
        self.width = 2 * MARGIN + CARD_W
        self.page_h = y - (GAP if self.cards else 0) + MARGIN
        self.arrangement = None

    def _arrange_columns(self, arrangement):
        by_key = {p["key"]: p for p in self.planned}
        cols = []
        if arrangement and arrangement.get("columns"):
            page_w = int(arrangement.get("page_width") or 0) or WIDE_W * len(arrangement["columns"])
            for col in arrangement["columns"]:
                keys = [k for k in col.get("cards", []) if k in by_key]
                cols.append({"share": max(float(col.get("share") or 1), 0.05), "keys": keys})
            placed = {k for c in cols for k in c["keys"]}
            extra = [p["key"] for p in self.planned if p["key"] not in placed]
            cols = [c for c in cols if c["keys"]] or ([{"share": 1.0, "keys": []}] if self.planned else [])
            widths = self._widths(cols, page_w)
            rendered = {}
            for c, w in zip(cols, widths):
                for k in c["keys"]:
                    rendered[k] = self._render(by_key[k], w)
            for k in extra:                          # newly ticked cards: the shortest column
                heights = [sum(rendered[x]["h"] + GAP for x in c["keys"]) for c in cols]
                j = heights.index(min(heights))
                rendered[k] = self._render(by_key[k], widths[j])
                cols[j]["keys"].append(k)
        else:                                        # automatic
            rendered = {p["key"]: self._render(p, WIDE_W) for p in self.planned}
            keys = _auto_columns([(p["key"], rendered[p["key"]]["h"]) for p in self.planned])
            cols = [{"share": 1.0, "keys": k} for k in keys]
            widths = [WIDE_W] * len(cols)
            page_w = 2 * MARGIN + sum(widths) + GAP * max(len(cols) - 1, 0)

        # place, then stretch each column's last card so the columns end flush
        x, bottoms = MARGIN, []
        for c, w in zip(cols, widths):
            y = MARGIN + HEADER_H
            for k in c["keys"]:
                card = rendered[k]
                card.update(x=x, y=y, draw_h=card["h"])
                self.cards.append(card)
                y += card["h"] + GAP
            bottoms.append(y - GAP)
            x += w + GAP
        bottom = max(bottoms or [MARGIN + HEADER_H])
        for c in cols:
            if c["keys"]:
                last = rendered[c["keys"][-1]]
                last["draw_h"] = bottom - last["y"]
        self.width = max(page_w or 0, x - GAP + MARGIN) if cols else 2 * MARGIN + WIDE_W
        self.page_h = bottom + MARGIN
        total = sum(widths) or 1
        self.arrangement = {"page_width": int(self.width),
                            "columns": [{"share": round(w / total, 4), "cards": list(c["keys"])}
                                        for c, w in zip(cols, widths)]}
        self.cards.sort(key=lambda c: next(p["i"] for p in self.planned if p["key"] == c["key"]))

    @staticmethod
    def _widths(cols, page_w):
        avail = page_w - 2 * MARGIN - GAP * (len(cols) - 1)
        total = sum(c["share"] for c in cols) or 1
        return [max(MIN_COL_W, int(avail * c["share"] / total)) for c in cols]

    # ── one card ──────────────────────────────────────────────────────────────

    def _card(self, card, tabs, chosen, width, index):
        images = {}
        inner = width - 2 * PAD
        prefix = f"c{index}w{width}i"

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
            return name, f'<img src="{name}" width="{w}" height="{h}">'

        def natural(part):
            if part.full:
                return inner
            if isinstance(part.content, charts.DonutChart):
                return min(part.content.width(), inner)
            return None

        def part_html(part, width):
            label = (f'<span style="font-size:11px; font-weight:bold; color:{MUTED}">'
                     f'{esc(part.title.upper())}</span><br>') if part.title else ""
            c = part.content
            if isinstance(c, list):
                return "".join(f'<p style="margin-top:0; margin-bottom:6px">{part_html(p, width)}</p>'
                               for p in c)
            if isinstance(c, str):
                return label + c
            name, tag = image(c, width)
            if isinstance(c, charts.DonutChart):
                self.donuts.append((_kind(card, tabs), index, short, part.title, images[name]))
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
        MIN_WIDE, SP = min(340, inner), 24
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
        doc.setTextWidth(width)
        return title, doc

    # ── drawing the page ──────────────────────────────────────────────────────

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

    def _paint(self, p, cards, width, height):
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(QRectF(0, 0, width, height), QColor(PAGE_BG))
        p.save()
        p.translate(MARGIN, MARGIN)
        self._header_doc(width - 2 * MARGIN).drawContents(p)
        p.restore()
        for c in cards:
            path = QPainterPath()
            path.addRoundedRect(QRectF(c["x"] + 0.5, c["y"] + 0.5, c["w"] - 1, c["draw_h"] - 1), 10, 10)
            p.setPen(QPen(QColor(LINE), 1))
            p.setBrush(QColor(CARD_BG))
            p.drawPath(path)
            p.save()
            p.translate(c["x"], c["y"])
            c["doc"].drawContents(p, QRectF(0, 0, c["w"], c["h"]))
            p.restore()

    def image(self, cards=None, size=None):
        cards = self.cards if cards is None else cards
        w, h = size or (self.width, self.page_h)
        img = QImage(int(w * SCALE), int(h * SCALE), QImage.Format_ARGB32_Premultiplied)
        img.fill(QColor(PAGE_BG))
        p = QPainter(img)
        p.scale(SCALE, SCALE)
        self._paint(p, cards, w, h)
        p.end()
        return img

    def single_cards(self):
        """[(title, QImage)] — each card on its own, with the page header."""
        out = []
        for c in self.cards:
            one = dict(c, x=MARGIN, y=MARGIN + HEADER_H, draw_h=c["h"])
            out.append((c["title"], self.image([one], (c["w"] + 2 * MARGIN, HEADER_H + c["h"] + 2 * MARGIN))))
        return out

    # ── saving ────────────────────────────────────────────────────────────────

    def save_png(self, path):
        """Saves the PNG (or one per card, or the summary + full page); returns the paths written."""
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
        self._paint(p, self.cards, self.width, self.page_h)
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
