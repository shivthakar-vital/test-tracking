"""
Export sections drawn directly with QPainter, to look like the design mockups: small
donuts with compact legends, rounded number tiles, thin rounded bars, pill tags, and
plenty of space. Light and dark themes. Links (runs, bug keys) stay clickable in the PDF,
because they're drawn as rich text, which Qt's PDF writer turns into link annotations.

Each section is built as boxes that know their height for a given width, then drawn:
    item = build_section(...);  item.h;  item.draw(painter, x, y)
"""

import html, math
from collections import Counter, defaultdict
from datetime import date, timedelta

from PyQt5.QtCore import Qt, QRectF, QPointF
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QTextDocument

import blocks
import changelog
import charts
import export_sections as es
import layout as lay
import tracker_store as store

esc = html.escape

THEMES = {
    "light": dict(page="#F5F5F2", card="#FFFFFF", border="#E6E4E0", ink="#1A1A18", ink2="#52514E",
                  muted="#8A8984", tile="#F4F3EF", pill="#EFEDE7", pill_ink="#52514E", link="#2F6FD0",
                  warn="#B26B00", good="#0B7A0B", neutral="#B4B2A9", sep="#ECEAE6"),
    "dark": dict(page="#171716", card="#232321", border="#3A3936", ink="#EDEBE6", ink2="#C7C5BE",
                 muted="#9A9891", tile="#1B1B1A", pill="#33322F", pill_ink="#C7C5BE", link="#7DB0FF",
                 warn="#E3A33B", good="#5CC45C", neutral="#C9C8C2", sep="#33322F"),
}


def font(px, bold=False):
    f = QFont()
    f.setPixelSize(int(px))
    f.setBold(bold)
    return f

ACRONYMS = {"QA", "UI", "UX", "IA", "CC", "HT", "LC", "CI", "SW", "FW", "HW", "E2E", "OTA", "POST", "PLLD",
            "GUI", "API", "TBD", "N/A", "ID", "SIFT", "TBOX"}

def pretty(label):
    """ "RUN _COMPLETED, NO RESULTS" → "Run completed, no results" (sheet values in capitals).
    Acronyms like QA, IA, CC stay in capitals."""
    if not label:
        return "No status"
    if label.upper() != label or not any(ch.isalpha() for ch in label):
        return label
    words = " ".join(label.replace("_", " ").split()).split(" ")
    out = []
    for i, w in enumerate(words):
        core = w.strip(",.()/:;")
        if core in ACRONYMS:
            out.append(w)
        else:
            out.append(w.lower() if i else w[:1] + w[1:].lower())
    return " ".join(out)


# ── boxes ─────────────────────────────────────────────────────────────────────

class Item:
    def __init__(self, h, draw=lambda p, x, y: None):
        self.h, self._draw = h, draw

    def draw(self, p, x, y):
        self._draw(p, x, y)

def vstack(items, gap=12):
    items = [i for i in items if i is not None and i.h > 0]
    h = sum(i.h for i in items) + gap * max(len(items) - 1, 0)

    def draw(p, x, y):
        for i in items:
            i.draw(p, x, y)
            y += i.h + gap
    return Item(h, draw)

def hstack(builders, width, gap=28, weights=None):
    """Side-by-side columns: builders are fn(width) → Item."""
    builders = [b for b in builders if b is not None]
    if not builders:
        return None
    weights = weights or [1] * len(builders)
    avail = width - gap * (len(builders) - 1)
    widths = [int(avail * w / sum(weights)) for w in weights]
    items = [b(w) for b, w in zip(builders, widths)]
    h = max((i.h for i in items if i is not None), default=0)

    def draw(p, x, y):
        for i, w in zip(items, widths):
            if i is not None:
                i.draw(p, x, y)
            x += w + gap
    return Item(h, draw)

def spacer(h):
    return Item(h)


class Kit:
    """Drawing pieces in one theme."""

    def __init__(self, theme):
        self.t = THEMES.get(theme, THEMES["light"])

    def c(self, key):
        return QColor(self.t[key])

    # text ────────────────────────────────────────────────────────────────────

    def text(self, s, width, px=14, color="ink", bold=False, elide=True):
        f = font(px, bold)
        fm = QFontMetrics(f)
        s = fm.elidedText(s, Qt.ElideRight, int(width)) if elide else s
        h = fm.height() + 2

        def draw(p, x, y):
            p.setFont(f)
            p.setPen(self.c(color) if color in self.t else QColor(color))
            p.drawText(QRectF(x, y, width, h), Qt.AlignLeft | Qt.AlignVCenter, s)
        return Item(h, draw)

    def rich(self, body, width, px=14, color="ink"):
        """Rich text (links, wrapping), via QTextDocument so PDF links work."""
        doc = QTextDocument()
        doc.setDefaultFont(font(px))
        doc.setDocumentMargin(0)
        doc.setDefaultStyleSheet(f"a {{ color: {self.t['link']}; text-decoration: none; }}")
        doc.setHtml(f'<span style="color:{self.t[color] if color in self.t else color}">{body}</span>')
        doc.setTextWidth(width)
        h = doc.size().height()

        def draw(p, x, y):
            p.save()
            p.translate(x, y)
            doc.drawContents(p, QRectF(0, 0, width, h))
            p.restore()
        item = Item(h, draw)
        item.w = doc.idealWidth()
        return item

    def heading(self, title, sub, width):
        f1, f2 = font(21, True), font(15)
        w1 = QFontMetrics(f1).horizontalAdvance(title)
        h = QFontMetrics(f1).height() + 2

        def draw(p, x, y):
            p.setFont(f1)
            p.setPen(self.c("ink"))
            p.drawText(QRectF(x, y, w1 + 4, h), Qt.AlignLeft | Qt.AlignBottom, title)
            p.setFont(f2)
            p.setPen(self.c("muted"))
            p.drawText(QRectF(x + w1 + 12, y, width - w1 - 12, h - 2), Qt.AlignLeft | Qt.AlignBottom,
                       QFontMetrics(f2).elidedText(sub, Qt.ElideRight, int(width - w1 - 12)))
        return Item(h, draw)

    def product(self, name, sub, width):
        f1, f2 = font(17, True), font(13)
        w1 = QFontMetrics(f1).horizontalAdvance(name)
        h = QFontMetrics(f1).height() + 2

        def draw(p, x, y):
            p.setFont(f1)
            p.setPen(self.c("ink"))
            p.drawText(QRectF(x, y, w1 + 4, h), Qt.AlignLeft | Qt.AlignBottom, name)
            if sub:
                p.setFont(f2)
                p.setPen(self.c("muted"))
                p.drawText(QRectF(x + w1 + 10, y, width - w1 - 10, h - 2), Qt.AlignLeft | Qt.AlignBottom,
                           QFontMetrics(f2).elidedText(sub, Qt.ElideRight, int(width - w1 - 10)))
        return Item(h, draw)

    def label(self, s, width):
        return self.text(s, width, 15, "ink2")

    def note(self, s, width):
        return self.text(s, width, 13, "muted")

    # charts ──────────────────────────────────────────────────────────────────

    def donut(self, slices, big, small, width, d=156):
        """A donut with a legend of label, count and % — beside it, or under it when the
        column is too narrow."""
        slices = [(l or "No status", n, c) for l, n, c in slices if n > 0]      # exactly as in the sheet
        total = sum(n for _, n, _ in slices) or 1
        lf, cf = font(15), font(15)
        fm, cfm = QFontMetrics(lf), QFontMetrics(cf)
        row = 30
        count_w = max((cfm.horizontalAdvance(f"{n}  ·  {round(100 * n / total)}%") for _, n, _ in slices), default=40)
        label_w = max((fm.horizontalAdvance(l) for l, _, _ in slices), default=60)
        beside = width >= d + 28 + 22 + min(label_w, 260) + 24 + count_w
        legend_h = row * len(slices)
        h = max(d, legend_h) if beside else d + 16 + legend_h

        def draw(p, x, y):
            p.setRenderHint(QPainter.Antialiasing)
            cy = y + (h / 2 if beside else d / 2)
            rect = QRectF(x + 1, cy - d / 2, d, d)
            ring = 26
            start = 90 * 16
            for _, n, color in slices:
                span = -int(round(360 * 16 * n / total))
                pen = QPen(QColor(color), ring)
                pen.setCapStyle(Qt.FlatCap)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawArc(rect.adjusted(ring / 2, ring / 2, -ring / 2, -ring / 2), start, span)
                if len(slices) > 1:                       # a thin gap between slices
                    p.setPen(QPen(self.c("card"), 2))
                    ang = math.radians((start + span) / 16)
                    c0 = rect.center()
                    p.drawLine(QPointF(c0.x() + (d / 2 - ring) * math.cos(ang), c0.y() - (d / 2 - ring) * math.sin(ang)),
                               QPointF(c0.x() + d / 2 * math.cos(ang), c0.y() - d / 2 * math.sin(ang)))
                start += span
            p.setPen(self.c("ink"))
            p.setFont(font(26, True))
            p.drawText(QRectF(rect.x(), cy - 24, d, 30), Qt.AlignCenter, big)
            p.setPen(self.c("ink2"))
            p.setFont(font(13))
            p.drawText(QRectF(rect.x(), cy + 6, d, 18), Qt.AlignCenter, small)
            if beside:
                lx, ly, lw = x + d + 28, cy - legend_h / 2, width - d - 28
            else:
                lx, ly, lw = x, y + d + 16, width
            for label, n, color in slices:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(color))
                p.drawRoundedRect(QRectF(lx, ly + row / 2 - 7, 14, 14), 3, 3)
                p.setPen(self.c("ink"))
                p.setFont(lf)
                p.drawText(QRectF(lx + 24, ly, lw - 24 - count_w - 12, row), Qt.AlignVCenter | Qt.AlignLeft,
                           fm.elidedText(label, Qt.ElideRight, int(lw - 24 - count_w - 12)))
                p.setPen(self.c("ink2"))
                p.setFont(cf)
                p.drawText(QRectF(lx, ly, lw, row), Qt.AlignVCenter | Qt.AlignRight,
                           f"{n}  ·  {round(100 * n / total)}%")
                ly += row
        return Item(h, draw)

    def tiles(self, tiles, width, gap=12):
        """tiles: [(label, value, sub, color key)]"""
        if not tiles:
            return None
        h = 92
        tw = (width - gap * (len(tiles) - 1)) / len(tiles)

        def draw(p, x, y):
            p.setRenderHint(QPainter.Antialiasing)
            for i, (label, value, sub, color) in enumerate(tiles):
                tx = x + i * (tw + gap)
                p.setPen(Qt.NoPen)
                p.setBrush(self.c("tile"))
                p.drawRoundedRect(QRectF(tx, y, tw, h), 10, 10)
                p.setFont(font(14))
                p.setPen(self.c("ink2"))
                p.drawText(QRectF(tx + 16, y + 10, tw - 24, 20), Qt.AlignLeft | Qt.AlignVCenter, label)
                p.setFont(font(27, True))
                p.setPen(self.c(color) if color in self.t else QColor(color))
                p.drawText(QRectF(tx + 16, y + 30, tw - 24, 34), Qt.AlignLeft | Qt.AlignVCenter, str(value))
                p.setFont(font(13))
                p.setPen(self.c("muted"))
                p.drawText(QRectF(tx + 16, y + 64, tw - 24, 18), Qt.AlignLeft | Qt.AlignVCenter,
                           QFontMetrics(font(13)).elidedText(sub, Qt.ElideRight, int(tw - 24)))
        return Item(h, draw)

    def bars(self, rows, width, neutral=False):
        """rows: [(label, [(n, color)], total)] — thin rounded bars, count on the right."""
        if not rows:
            return None
        lf = font(15)
        fm = QFontMetrics(lf)
        lw = min(max(fm.horizontalAdvance(r[0]) for r in rows) + 16, int(width * 0.38))
        cw = max(38, QFontMetrics(font(15, True)).horizontalAdvance(str(max(r[2] for r in rows))) + 14)
        row_h, bar_h = 40, 16
        top = max(r[2] for r in rows) or 1
        h = row_h * len(rows)

        def draw(p, x, y):
            p.setRenderHint(QPainter.Antialiasing)
            area = width - lw - cw - 10
            for k, (label, segs, total) in enumerate(rows):
                ry = y + k * row_h
                p.setFont(lf)
                p.setPen(self.c("ink"))
                p.drawText(QRectF(x, ry, lw - 12, row_h), Qt.AlignVCenter | Qt.AlignLeft,
                           fm.elidedText(label, Qt.ElideRight, lw - 12))
                bx = x + lw
                full = area * total / top
                clip = QPainterPath()
                clip.addRoundedRect(QRectF(bx, ry + (row_h - bar_h) / 2, max(full, 2), bar_h), 4, 4)
                p.save()
                p.setClipPath(clip)
                for n, color in segs:
                    if n <= 0:
                        continue
                    sw = area * n / top
                    p.fillRect(QRectF(bx, ry + (row_h - bar_h) / 2, sw, bar_h),
                               self.c("neutral") if neutral else QColor(color))
                    bx += sw
                p.restore()
                p.setFont(font(15, True))
                p.setPen(self.c("ink"))
                p.drawText(QRectF(x + width - cw, ry, cw, row_h), Qt.AlignVCenter | Qt.AlignRight, str(total))
        return Item(h, draw)

    def pill(self, p, x, cy, s, px=13):
        f = font(px)
        w = QFontMetrics(f).horizontalAdvance(s) + 20
        p.setPen(Qt.NoPen)
        p.setBrush(self.c("pill"))
        p.drawRoundedRect(QRectF(x, cy - 12, w, 24), 12, 12)
        p.setPen(self.c("pill_ink"))
        p.setFont(f)
        p.drawText(QRectF(x, cy - 12, w, 24), Qt.AlignCenter, s)
        return w

    def table(self, rows, width):
        """rows: [dict(link_html, tag, text, note, right, right_color)] — one line each,
        separated by hairlines; the text is shortened to fit."""
        if not rows:
            return None
        row_h = 44
        h = row_h * len(rows)
        firsts = [self.rich(r["link_html"], 120, 15) for r in rows]
        tag_w = max((QFontMetrics(font(13)).horizontalAdvance(r.get("tag") or "") + 20 for r in rows), default=0)
        rf = font(15)
        rfm = QFontMetrics(rf)
        right_w = max((rfm.horizontalAdvance(r.get("right") or "") for r in rows), default=0) + 8

        def draw(p, x, y):
            p.setRenderHint(QPainter.Antialiasing)
            first_w = max(66, max(f.w for f in firsts) + 16)
            for k, r in enumerate(rows):
                ry = y + k * row_h
                p.setPen(QPen(self.c("sep"), 1))
                p.drawLine(QPointF(x, ry), QPointF(x + width, ry))
                cy = ry + row_h / 2
                firsts[k].draw(p, x, cy - firsts[k].h / 2)
                tx = x + first_w
                if r.get("tag"):
                    self.pill(p, tx, cy, r["tag"])
                    tx += tag_w + 14
                avail = x + width - right_w - 16 - tx
                main = " ".join((r.get("text") or "").split())
                note = " ".join((r.get("note") or "").split())
                p.setFont(rf)
                mw = rfm.horizontalAdvance(main)
                if mw > avail:
                    main, note = rfm.elidedText(main, Qt.ElideRight, int(avail)), ""
                    mw = avail
                p.setPen(self.c("ink"))
                p.drawText(QRectF(tx, ry, mw + 2, row_h), Qt.AlignVCenter | Qt.AlignLeft, main)
                if note and avail - mw > 40:
                    nt = rfm.elidedText(" · " + note, Qt.ElideRight, int(avail - mw))
                    p.setPen(self.c("muted"))
                    p.drawText(QRectF(tx + mw, ry, avail - mw, row_h), Qt.AlignVCenter | Qt.AlignLeft, nt)
                if r.get("right"):
                    rc = r.get("right_color", "muted")
                    p.setPen(self.c(rc) if rc in self.t else QColor(rc))
                    p.setFont(rf)
                    p.drawText(QRectF(x + width - right_w, ry, right_w, row_h), Qt.AlignVCenter | Qt.AlignRight,
                               r["right"])
        return Item(h, draw)


# ── sections ──────────────────────────────────────────────────────────────────

class Painter(es.Renderer):
    """Builds the export sections as drawn boxes (same data and options as export_sections)."""

    def __init__(self, data, ctx, cfg, theme="light"):
        super().__init__(data, ctx, cfg, img=None, inner_width=0)
        self.k = Kit(theme)

    def build(self, sid, width):
        made = getattr(self, "b_" + sid)(width)
        if made is None:
            return None
        title, sub, body = made
        return vstack([self.k.heading(title, sub, width), body], 18)

    # helpers ─────────────────────────────────────────────────────────────────

    def link(self, text, url):
        return f'<a href="{esc(url, quote=True)}">{esc(text)}</a>' if url else esc(text)

    def status_slices(self, tabs, rows_of):
        counts, colors, options = Counter(), {}, []
        for t in tabs:
            rows = rows_of(t)
            col, cols = charts.tab_colors(t, rows)
            if not col:
                continue
            colors.update(cols)
            options += [o for o in col["options"] if o not in options]
            counts.update(store.row_status(t, r) for r in rows)
        order = charts.status_order(list(counts), options)
        return [(v, counts[v], colors.get(v, charts.PENDING)) for v in order if counts[v]], colors, order

    def done_pct(self, slices):
        total = sum(n for _, n, _ in slices)
        done = sum(n for v, n, _ in slices if charts.classify(v) == "done")
        return f"{round(100 * done / total)}%" if total else "–"

    def groups_cols(self, width, build):
        groups = self.groups()
        if not groups:
            return None
        if len(groups) == 1:
            return build(groups[0][0], groups[0][1], width)
        return hstack([lambda w, g=g: build(g[0], g[1], w) for g in groups], width, 36)

    # End-to-End Runs ─────────────────────────────────────────────────────────

    def b_e2e(self, width):
        if not self.runs:
            return None
        k, ctx = self.k, self.ctx

        def product(name, tabs, w):
            parts = [k.product(name, "", w)] if name else []
            rows = [r for t in tabs for r in ctx.rows(t)]
            if self.opt("e2e", "status"):
                slices, _, _ = self.status_slices(tabs, ctx.rows)
                if slices:
                    parts.append(k.donut(slices, self.done_pct(slices), "passed", w))
                else:
                    parts.append(k.note(f"No runs in {ctx.range_text}.", w))
            if self.opt("e2e", "tiles"):
                week = self._week_runs(tabs)
                fixes = [e for t in tabs for e in store.resolutions(t, ctx.all_rows(t), ctx.rng)]
                open_ = [e for e in fixes if e[1] is None]
                as_of = min(ctx.rng[1], date.today()) if ctx.rng else date.today()
                oldest = max(((as_of - e[3]).days for e in open_ if e[3]), default=None)
                parts.append(k.tiles([
                    ("Runs", len(rows), f"7 days: {week}" if week is not None else "", "ink"),
                    ("Unresolved", len(open_), f"oldest {es._plural(oldest, 'day')}" if oldest is not None
                     else "none open", "warn" if open_ else "ink")], w))
            return vstack(parts, 16)

        body = [self.groups_cols(width, product)] if self.opt("e2e", "status") or self.opt("e2e", "tiles") else []
        if self.opt("e2e", "versions"):
            body.append(self._version_bars(width))
        if self.opt("e2e", "days"):
            body.append(self._day_bars(width))
        if self.opt("e2e", "platform"):
            body.append(self._platform_bars(width))
        if self.opt("e2e", "failed"):
            body.append(self._failed(width))
        if not body:
            return None
        names = " and ".join(n for n, _ in self.runs)
        return "End-to-End Runs", names, vstack(body, 22)

    def _status_bars(self, tabs, key_of, title, width, newest_first=False, limit=8, natural=None):
        groups, colors, options = defaultdict(Counter), {}, []
        for t in tabs:
            col, cols = charts.tab_colors(t, self.ctx.rows(t))
            colors.update(cols)
            options += [o for o in (col or {}).get("options", []) if o not in options]
            for r in self.ctx.rows(t):
                key = key_of(t, r)
                if key:
                    groups[key][store.row_status(t, r)] += 1
        if not groups:
            return None
        order = charts.status_order({s for g in groups.values() for s in g}, options)
        if natural:
            names = sorted(groups, key=natural, reverse=newest_first)[:limit]
        else:
            names = sorted(groups, key=lambda g: -sum(groups[g].values()))[:limit]
        def shown(n):
            if len(n) == 10 and n[4] == "-" and n[7] == "-":        # a day: Tue Oct 20
                try:
                    return date.fromisoformat(n).strftime("%a %b %-d")
                except ValueError:
                    pass
            return n
        rows = [(shown(n), [(groups[n][s], colors.get(s, charts.PENDING)) for s in order], sum(groups[n].values()))
                for n in names]
        return vstack([self.k.label(title, width), self.k.bars(rows, width)], 6)

    def _version_bars(self, width):
        def key(t, r):
            ver = es._find(t, "software", "version")
            return r["values"][ver["pos"]].strip() if ver else ""
        return self._status_bars([t for _, t in self.runs], key, "Runs Per Software Version", width,
                                 newest_first=True, natural=blocks._natural)

    def _day_bars(self, width):
        def key(t, r):
            d = store.date_column(t)
            v = store.parse_date(r["values"][d["pos"]]) if d else None
            return v.isoformat() if v else ""
        item = self._status_bars([t for _, t in self.runs], key, "Runs By Day", width, newest_first=True,
                                 limit=14, natural=lambda s: s)
        return item

    def _platform_bars(self, width):
        tabs = [t for _, t in self.runs if store.platform_column(t)]
        if not tabs:
            return None
        return self._status_bars(tabs, lambda t, r: store.row_platform(t, r), "Coder vs Atlantis", width)

    def _failed(self, width):
        ctx, items = self.ctx, []
        for name, t in self.runs:
            reason, fault = es._reason_col(t), store.fault_column(t)
            notes = next((c for c in t["columns"] if store._words(c["name"]) & {"note", "notes"}), None)
            res, d = store.resolved_column(t), store.date_column(t)
            run_col = store.run_name_column(t)
            as_of = min(ctx.rng[1], date.today()) if ctx.rng else date.today()
            for r in ctx.rows(t):
                if charts.classify(store.row_status(t, r)) not in store.FAILED:
                    continue
                ran = store.parse_date(r["values"][d["pos"]]) if d else None
                fixed = store.parse_date(r["values"][res["pos"]]) if res else None
                if fixed and ran:
                    days = (fixed - ran).days
                    right, rc = ("same day" if days <= 0 else f"fixed in {es._plural(days, 'day')}"), "good"
                elif ran:
                    right, rc = f"open {es._plural((as_of - ran).days, 'day')}", "muted"
                else:
                    right, rc = "open", "muted"
                bits = [r["values"][c["pos"]].strip() for c in (reason, fault) if c and r["values"][c["pos"]].strip()]
                num = r["values"][t["columns"][0]["pos"]].strip().lstrip("#")
                link = r["links"][run_col["pos"]] if run_col else ""
                items.append((ran or date.min, dict(link_html=self.link(f"#{num}" if num else "run", link), tag=name,
                                                    text=" · ".join(bits) or "—",
                                                    note=r["values"][notes["pos"]].strip() if notes else "",
                                                    right=right, right_color=rc)))
        k = self.k
        if not items:
            return vstack([k.label("Failed Runs", width), k.note(f"No failed runs in {ctx.range_text}.", width)], 6)
        items.sort(key=lambda x: x[0], reverse=True)
        return vstack([k.label(f"Failed Runs ({len(items)})", width), k.table([r for _, r in items], width)], 6)

    # Error Analysis ──────────────────────────────────────────────────────────

    def b_errors(self, width):
        if not self.runs:
            return None
        k, ctx = self.k, self.ctx
        total = [0]

        def reason_item(tabs, w):
            counts, opts, name = Counter(), [], None
            for t in tabs:
                rc = es._reason_col(t)
                if not rc:
                    continue
                name = name or rc["name"]
                opts += [o for o in rc["options"] if o not in opts]
                for r in store.issues(t, ctx.rows(t)):
                    v = r["values"][rc["pos"]].strip()
                    if v:
                        counts[v] += 1
            if not counts:
                return vstack([k.label("Reason", w), k.note("None recorded.", w)], 6)
            order = [o for o in opts if o in counts] + sorted(v for v in counts if v not in opts)
            slices = [(v, counts[v], charts.category_color(name, v)) for v in order]
            n = sum(counts.values())
            return vstack([k.label("Reason", w), k.donut(slices, str(n), "issue" if n == 1 else "issues", w)], 8)

        def subsystem_item(tabs, w):
            counts = Counter()
            for t in tabs:
                fault = store.fault_column(t)
                for r in store.issues(t, ctx.rows(t)) if fault else []:
                    if charts.classify(store.row_status(t, r)) in store.FAILED and r["values"][fault["pos"]].strip():
                        counts[r["values"][fault["pos"]].strip()] += 1
            if not counts:
                return vstack([k.label("Subsystem", w), k.note("None recorded.", w)], 6)
            rows = [(s, [(n, "")], n) for s, n in counts.most_common(8)]
            return vstack([k.label("Subsystem", w), k.bars(rows, w, neutral=True)], 6)

        def product(name, tabs, w):
            total[0] += sum(charts.classify(store.row_status(t, r)) in store.FAILED
                            for t in tabs for r in store.issues(t, ctx.rows(t)))
            parts = [k.product(name, "", w)] if name else []
            want_r, want_s = self.opt("errors", "reason"), self.opt("errors", "subsystem")
            if not name and want_r and want_s:             # combined: reason and subsystem side by side
                parts.append(hstack([lambda ww: reason_item(tabs, ww), lambda ww: subsystem_item(tabs, ww)], w, 40))
            else:
                if want_r:
                    parts.append(reason_item(tabs, w))
                if want_s:
                    parts.append(subsystem_item(tabs, w))
            return vstack(parts, 16)

        body = [self.groups_cols(width, product)]
        if self.opt("errors", "notes"):
            body.append(self._unresolved(width))
        return "Error Analysis", f"{es._plural(total[0], 'issue')} · repeats counted once", vstack(body, 22)

    def _unresolved(self, width):
        ctx, items = self.ctx, []
        as_of = min(ctx.rng[1], date.today()) if ctx.rng else date.today()
        for name, t in self.runs:
            notes = next((c for c in t["columns"] if store._words(c["name"]) & {"note", "notes"}), None)
            run_col = store.run_name_column(t)
            for e in store.resolutions(t, ctx.all_rows(t), ctx.rng):
                if e[1] is not None or not e[5]:
                    continue
                first = e[0].get("_runs", [e[0]])[0]
                num = first["values"][t["columns"][0]["pos"]].strip().lstrip("#")
                link = first["links"][run_col["pos"]] if run_col else ""
                comp = store.split_component(e[5])[1] or e[5]
                items.append((e[3] or date.max, dict(
                    link_html=self.link(f"#{num}", link), tag=name,
                    text=" · ".join(x for x in (e[4], comp) if x) or "—",
                    note=first["values"][notes["pos"]].strip() if notes else "",
                    right=f"open {es._plural((as_of - e[3]).days, 'day')}" if e[3] else "open", right_color="warn")))
        k = self.k
        if not items:
            return vstack([k.label("Unresolved", width), k.note("Nothing unresolved.", width)], 6)
        items.sort(key=lambda x: x[0])
        return vstack([k.label(f"Unresolved ({len(items)})", width), k.table([r for _, r in items], width)], 6)

    # New Bugs ────────────────────────────────────────────────────────────────

    def b_bugs(self, width):
        tab = self.bug_tab
        if tab is None:
            return None
        k = self.k
        groups = lay.split_groups(tab, {"split": "viking"}) or []
        picked = [(n, test) for n, test in groups if es.chosen(self.cfg, n)]
        if not groups:
            parts = [(None, self.ctx)]
        elif self.cfg["split"]:
            parts = [(n, self.ctx.section(test)) for n, test in picked]
        else:
            tests = [test for _, test in picked]
            parts = [(None, self.ctx.section(lambda r, ts=tests: any(t(r) for t in ts)))]
        if not parts:
            return None
        product_of = dict(groups)

        def column(name, ctx, w):
            bugs = blocks._Bugs(tab, ctx)
            done = lambda r: charts.classify(bugs.st[id(r)]) == "done" or bool(bugs.when(r, bugs.done_col))
            new_open = [r for r in bugs.new if not done(r)]
            sub = f"{len(bugs.new)} new · {len(new_open)} still open · {len(bugs.closed)} closed"
            items = [k.product(name or "All products", sub, w)]
            if self.opt("bugs", "open"):
                items.append(self._bug_table(bugs, new_open, bugs.made, w, product_of if not name else None,
                                             f"No open new bugs in {ctx.range_text}."))
            if self.opt("bugs", "closed"):
                items.append(k.label("Closed", w))
                items.append(self._bug_table(bugs, bugs.closed, bugs.done_col, w, product_of if not name else None,
                                             f"No bugs closed in {ctx.range_text}.", done=True))
            if self.opt("bugs", "allstatus"):
                slices, _, _ = self.status_slices([tab], ctx.rows)
                if slices:
                    items.append(vstack([k.label("All Bugs By Status", w),
                                         k.donut(slices, self.done_pct(slices), "done", w)], 8))
            return vstack(items, 10)

        cols = [lambda w, n=n, c=c: column(n, c, w) for n, c in parts]
        body = cols[0](width) if len(cols) == 1 else hstack(cols, width, 36)
        return "New Bugs", f"opened and still open · {self.ctx.range_text}", body

    def _bug_table(self, bugs, items, date_col, width, product_of, empty, done=False):
        k = self.k
        if not items:
            return k.note(empty, width)
        rows = []
        for r in sorted(items, key=lambda r: bugs.when(r, date_col) or date.min, reverse=True):
            key, link = r["values"][bugs.key_col["pos"]], r["links"][bugs.key_col["pos"]]
            tag = next((n for n, test in product_of.items() if test(r)), None) if product_of else None
            rows.append(dict(link_html=("✓ " if done else "") + self.link(key, link), tag=None,
                             text=bugs.val(r, bugs.summary_col) + (f"  ({tag})" if tag else ""),
                             right=bugs.st[id(r)], right_color="ink2"))
        return self._bug_rows(rows, width)

    def _bug_rows(self, rows, width):
        """Like table(), but the first column is wider (bug keys) and the status is a pill."""
        k = self.k
        firsts = [k.rich(r["link_html"], 130, 15) for r in rows]
        fw = 112
        rf = font(15)
        rfm = QFontMetrics(rf)
        pill_w = max(QFontMetrics(font(13)).horizontalAdvance(r["right"]) + 20 for r in rows)
        avail = width - fw - pill_w - 20
        line = rfm.lineSpacing()

        def wrapped(text):
            """Up to two lines, the second shortened with … if needed."""
            words, lines, cur = text.split(), [], ""
            for w in words:
                trial = (cur + " " + w).strip()
                if rfm.horizontalAdvance(trial) <= avail or not cur:
                    cur = trial
                else:
                    lines.append(cur)
                    cur = w
                    if len(lines) == 1:
                        continue
            if cur:
                lines.append(cur)
            if len(lines) > 2:
                lines = [lines[0], " ".join(lines[1:])]
            if lines:
                lines[-1] = rfm.elidedText(lines[-1], Qt.ElideRight, int(avail))
            return lines or [""]

        texts = [wrapped(r["text"]) for r in rows]
        heights = [max(44, len(t) * line + 18) for t in texts]

        def draw(p, x, y):
            p.setRenderHint(QPainter.Antialiasing)
            ry = y
            for i, r in enumerate(rows):
                row_h = heights[i]
                p.setPen(QPen(k.c("sep"), 1))
                p.drawLine(QPointF(x, ry), QPointF(x + width, ry))
                top = ry + (row_h - len(texts[i]) * line) / 2
                firsts[i].draw(p, x, top + (line - firsts[i].h) / 2)
                p.setFont(rf)
                p.setPen(k.c("ink"))
                for j, t in enumerate(texts[i]):
                    p.drawText(QRectF(x + fw, top + j * line, avail, line), Qt.AlignVCenter | Qt.AlignLeft, t)
                k.pill(p, x + width - pill_w, top + line / 2, r["right"])
                ry += row_h
        return Item(sum(heights), draw)

    # Feature Testing ─────────────────────────────────────────────────────────

    def b_testing(self, width):
        import feature_testing as ft
        testing = self.data.get("testing")
        if not testing or not testing.get("tickets"):
            return None
        k, ctx = self.k, self.ctx
        keep = lambda e: es.chosen(self.cfg, e["product"])
        efforts = [e for e in ft.efforts(testing) if keep(e)]
        tested = [e for e in ft.efforts_in(testing, ctx.rng) if keep(e)]
        shown = [(e, s) for e, s in ft.sessions_in(testing, ctx.rng) if keep(e)]
        active = [e for e in efforts if e["status_category"] != "done"]
        today = date.today()
        stale = [e for e in active if not e["last"] or (today - date.fromisoformat(e["last"])).days > 7]
        bug_info = testing.get("bug_info") or {}
        settings = self.data.get("settings") or {}
        kind = lambda s, b: ft.bug_kind(settings, s["ticket"], s, b)
        new_bugs = list(dict.fromkeys(b for _, s in shown for b in s["bugs"] if kind(s, b) == "new"))
        seen_bugs = [b for b in dict.fromkeys(b for _, s in shown for b in s["bugs"] if kind(s, b) == "seen")
                     if b not in new_bugs]
        info = testing.get("support") or {}
        hw = list(dict.fromkeys(x for _, s in shown for x in s.get("support", [])))
        hw_open = [x for x in hw if (info.get(x) or {}).get("status_category") != "done"]
        body = []
        if self.opt("testing", "overview"):
            body.append(k.tiles([("Sessions", len(shown), ctx.range_text, "ink"),
                         ("New bugs", len(new_bugs), f"{len(seen_bugs)} seen again" if seen_bugs else "found in testing",
                          "link" if new_bugs else "ink"),
                         ("HW support requests", len(hw), f"{len(hw_open)} still open" if hw else "none made",
                          "warn" if hw_open else "ink"),
                         ("Efforts tested", len(tested), f"{len(active)} active in JIRA", "ink")], width))
        jira = lambda key, label: self.link(label, f"{changelog.JIRA_SITE}/browse/{key}")
        insts = ft.instruments(testing, ctx.rng, keep) if self.opt("testing", "instruments") else []
        if insts:
            rows = []
            for inst in insts:
                name = f"<b>{html.escape(inst['name'])}</b>"
                last_d = (f"last tested {date.fromisoformat(inst['last'][1]['date']):%b %-d}" if inst["last"]
                          else "not tested")
                n = len(inst["open"])
                if n:
                    rows.append(dict(link_html=name, tag="HW Support Requested",
                                     text=f"{n} open support request{'s' if n != 1 else ''}",
                                     note=", ".join(r["key"] for r in inst["open"]), right=last_d, right_color="ink2"))
                else:
                    rows.append(dict(link_html=name, tag="No open requests", text="", right=last_d, right_color="ink2"))
            body.append(vstack([k.label("Instruments", width), k.table(rows, width)], 6))
        if self.opt("testing", "sessions"):
            if shown:
                rows = []
                for e, s in shown:
                    day = date.fromisoformat(s["date"]).strftime("%a %b %-d")
                    first = next((ln.strip().lstrip("• ").strip() for ln in s["notes"] if ln.strip()), "")
                    meta = " · ".join(x for x in (", ".join(s["testers"]), s["build"] and "Build " + s["build"],
                                                  s["system"]) if x)
                    hw_s = s.get("support", [])
                    n_new = sum(kind(s, b) == "new" for b in s["bugs"])
                    n_seen = len(s["bugs"]) - n_new
                    extra = [x for x in (f"{n_new} new bug" + ("s" if n_new != 1 else "") if n_new else "",
                                         f"{n_seen} seen" if n_seen else "",
                                         f"{len(hw_s)} HW request" + ("s" if len(hw_s) != 1 else "") if hw_s else "") if x]
                    rows.append(dict(link_html=jira(s["ticket"], f"S{s['n']}"), tag=e["product"],
                                     text=e["name"], note=meta + (f" · {first}" if first else ""),
                                     right=" · ".join([day] + extra),
                                     right_color="warn" if hw_s else ("link" if n_new else "ink2")))
                body.append(vstack([k.label("What We Tested", width), k.table(rows, width)], 6))
            else:
                body.append(vstack([k.label("What We Tested", width),
                                    k.note(f"No test sessions logged in {ctx.range_text}.", width)], 6))
        if self.opt("testing", "efforts") and tested:
            rows = []
            for e in tested:
                if e["last"]:
                    ago = (today - date.fromisoformat(e["last"])).days
                    last = "last session today" if ago == 0 else f"last session {es._plural(ago, 'day')} ago"
                else:
                    last = "no sessions logged yet"
                rows.append(dict(link_html=jira(e["key"], e["key"]), tag=e["product"], text=e["name"],
                                 note=f"{es._plural(len(e['in_range']), 'session')} in these dates · {last}",
                                 right=e["status"],
                                 right_color="good" if e["status_category"] == "done" else "ink2"))
            body.append(vstack([k.label("Test Efforts", width), self._effort_rows(rows, width)], 6))
        found = []                                   # bugs from these sessions: new first, then seen again
        for b in new_bugs + seen_bugs:
            e0, s0 = next((e, s) for e, s in reversed(shown) if b in s["bugs"])
            b_ = bug_info.get(b) or {}
            found.append(dict(link_html=self.link(b, f"{changelog.JIRA_SITE}/browse/{b}"),
                              tag="New" if b in new_bugs else "Seen again", text=b_.get("summary") or e0["name"],
                              note=f"{e0['name']} · S{s0['n']}", right=b_.get("status") or "",
                              right_color="good" if b_.get("status_category") == "done" else "ink2"))
        if found and self.opt("testing", "found"):
            body.append(vstack([k.label("Bugs From Testing", width), k.table(found, width)], 6))
        if hw and self.opt("testing", "hw"):
            rows = []
            for x in hw:
                t_ = info.get(x) or {}
                sess = [(e, s) for e, s in shown if x in s.get("support", [])]
                e0, s0 = sess[-1]                     # the session it was first logged in (in these dates)
                rows.append(dict(link_html=self.link(x, f"{changelog.JIRA_SITE}/browse/{x}"), tag=e0["product"],
                                 text=t_.get("summary") or e0["name"],
                                 note=f"{e0['name']} · S{s0['n']}", right=t_.get("status") or "",
                                 right_color="good" if t_.get("status_category") == "done" else "warn"))
            body.append(vstack([k.label("Hardware Support Requests", width), k.table(rows, width)], 6))
        if not body:
            return None
        return "Feature Testing", "from JIRA Testing tickets", vstack(body, 20)

    def _effort_rows(self, rows, width):
        # like table(), but the key column is wider (PROD-1234)
        item = self.k.table(rows, width)
        return item

    # Change Log ──────────────────────────────────────────────────────────────

    def b_changelog(self, width):
        logs = changelog.saved(self.data.get("settings"))
        if not logs:
            return None
        k, log = self.k, logs[0]
        counts = Counter(e["category"] for e in log["entries"])
        tiles = [(c, counts.get(c, 0), "", "ink") for c in changelog.CATEGORIES]
        qa = [(t, e) for e in changelog.qa_entries(log) for t in e["tickets"] if changelog.in_qa(t)]
        body = [k.text(f"{log['from']}  →  {log['to']}   ·   {log['commits']} commits · {len(log['entries'])} PRs",
                       width, 15, "ink2")]
        if self.opt("changelog", "counts"):
            body.append(vstack([k.tiles(tiles[:3], width), k.tiles(tiles[3:], width)], 10))
        if qa and self.opt("changelog", "qa"):
            body.append(vstack([k.label(f"In QA ({len(qa)})", width), self._bug_rows(
                [dict(link_html=self.link(t["key"], t["url"]), text=t["summary"], right=t["status"]) for t, _ in qa],
                width)], 6))
        return "Change Log", f"latest · made {log['generated'][:10]}", vstack(body, 16)
