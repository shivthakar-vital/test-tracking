"""
Small chart widgets drawn with QPainter (no extra dependencies):
  • DonutChart   – share of rows per status, with a legend of counts
  • StackedBars  – one horizontal bar per group (System, Assignee…), split by status
  • DayColumns   – rows per day (e.g. runs), split by status

Every mark shows a tooltip on hover and emits `picked` when clicked, which the
app uses to jump to the matching rows in the table.
"""

import math

from PyQt5.QtWidgets import QWidget, QToolTip, QSizePolicy
from PyQt5.QtCore import Qt, QRectF, QPointF, pyqtSignal, QSize
from PyQt5.QtGui import QPainter, QColor, QPen, QPainterPath, QFont, QFontMetrics

SURFACE = "#FFFFFF"
INK, INK2, MUTED, GRID = "#1A1A18", "#52514E", "#999996", "#ECEAE6"

# Status colors: meaning first (done / blocked / in progress…), then a fixed
# categorical order for anything the keywords don't recognise.
GOOD, CRITICAL, SERIOUS, WARNING = "#0CA30C", "#D03B3B", "#EC835A", "#FAB219"
PROGRESS, REVIEW, QA = "#2A78D6", "#4A3AA7", "#E87BA4"
PENDING, DEFERRED = "#C9C8C2", "#7A7974"
# Colors for categories (workflows, reasons, subsystems, unrecognised statuses).
# No greens: green means finished (Done, Completed, Results…). The order keeps
# neighbours distinct for normal and red-green colorblind vision (OKLab ΔE ≥ 15
# between any two, ≥ 8 between neighbours as seen with deuteranopia/protanopia).
CATEGORICAL = ["#2A78D6", "#EB6834", "#B5478F", "#EDA100", "#0E8FB3", "#9C5B2E", "#4A3AA7", "#E87BA4",
               "#2C3E50", "#39D4E5"]

# (keywords, class, color) — checked in order, so "no results" wins over "results"
_RULES = [
    (("won't", "wont"),                                             "done",     GOOD),      # Won't Do / Won't Fix: closed
    (("block", "error", "fail", "broken"),                          "blocked",  CRITICAL),
    (("cancel", "abort"),                                          "cancelled", SERIOUS),
    (("no result", "warn", "partial", "flaky"),                     "warning",  WARNING),
    (("defer", "next release", "descoped"),                         "deferred", DEFERRED),
    (("not started", "not run", "to do", "todo", "created", "backlog", "open", "new", "pending"),
                                                                    "pending",  PENDING),
    (("review",),                                                   "active",   REVIEW),
    (("qa", "verif", "testing"),                                    "active",   QA),
    (("progress", "running", "active", "started"),                  "active",   PROGRESS),
    (("complete", "done", "pass", "closed", "resolved", "result", "fixed", "shipped"),
                                                                    "done",     GOOD),
]
CLASS_ORDER = ["done", "active", "warning", "blocked", "cancelled", "pending", "deferred", "other"]


def classify(value):
    """"done", "active", "blocked", … for a status value (by keyword)."""
    v = (value or "").strip().lower()
    if not v:
        return "pending"
    for words, cls, _ in _RULES:
        if any(w in v for w in words):
            return cls
    return "other"


OVERRIDES = {}      # {status value (lower case): color}, chosen by the admin in the app

def set_overrides(colors):
    OVERRIDES.clear()
    OVERRIDES.update(colors or {})


# One color per subsystem and per category value, the same on every card, tab
# and export. The order comes from the dropdown options across the whole sheet.
SUBSYSTEM_ORDER = []          # e.g. ["CC", "Drawer", "Gantry", "HT", "IA"]
CATEGORY_ORDER = {}           # column name (lower case) → its values in order
SUBSYSTEM_KEY = "subsystem"   # admin overrides: "subsystem:ia", "workflow type:cc12n dry"

def register(data):
    """Learn every subsystem and category value in the sheet, so colors don't
    depend on which tab or date range is on screen."""
    import tracker_store as store
    subs, cats = [], {}
    for tab in data.get("tabs", []):
        comp = store.component_column(tab)
        for o in (comp or {}).get("options", []):
            sub = store.split_component(o)[0]
            if sub and sub not in subs:
                subs.append(sub)
        fault = store.fault_column(tab)
        for r in tab["rows"] if fault else []:
            v = r["values"][fault["pos"]].strip()
            if v and v not in subs:
                subs.append(v)
        for c in tab["columns"]:
            if store.wants_pie(c):
                vals = cats.setdefault(c["name"].strip().lower(), [])
                for v in list(c["options"]) + [r["values"][c["pos"]].strip() for r in tab["rows"]]:
                    for part in store.parts(c, v):
                        if part.strip() and part.strip() not in vals:
                            vals.append(part.strip())
    SUBSYSTEM_ORDER[:] = subs
    CATEGORY_ORDER.clear()
    CATEGORY_ORDER.update(cats)

def _slot(order, value):
    if value not in order:
        order.append(value)
    return CATEGORICAL[order.index(value) % len(CATEGORICAL)]

def subsystem_color(name, use_overrides=True):
    key = f"{SUBSYSTEM_KEY}:{name.strip().lower()}"
    if use_overrides and key in OVERRIDES:
        return OVERRIDES[key]
    return _slot(SUBSYSTEM_ORDER, name.strip())

def category_color(column, value, use_overrides=True):
    col = column.strip().lower()
    key = f"{col}:{value.strip().lower()}"
    if use_overrides and key in OVERRIDES:
        return OVERRIDES[key]
    return _slot(CATEGORY_ORDER.setdefault(col, []), value.strip())


def status_colors(values, options=(), use_overrides=True):
    """{value: color}. The admin's choices come first. Otherwise known meanings
    get their status color, and others take the categorical slots in the
    dropdown's option order (skipping colors already on screen), so a value
    keeps its color even when filters change which values are shown."""
    colors, unknown = {}, []
    ordered = list(options) + sorted(set(values) - set(options))
    for v in ordered:
        low = (v or "").strip().lower()
        if use_overrides and low in OVERRIDES:
            colors[v] = OVERRIDES[low]
        elif not low:
            colors[v] = PENDING
        else:
            colors[v] = next((color for words, _, color in _RULES if any(w in low for w in words)), None)
            if colors[v] is None:
                unknown.append(v)
    taken = {c.upper() for c in colors.values() if c}
    free = [c for c in CATEGORICAL if c.upper() not in taken] or CATEGORICAL
    for i, v in enumerate(unknown):
        colors[v] = free[i % len(free)]
    return colors


def status_order(values, options=()):
    """Sort statuses: by meaning (done → deferred), then dropdown order."""
    opt = {o: i for i, o in enumerate(options)}
    return sorted(values, key=lambda v: (CLASS_ORDER.index(classify(v)), opt.get(v, 99), v))


def _font(px, bold=False):
    f = QFont()
    f.setPixelSize(px)
    f.setBold(bold)
    return f


class _Chart(QWidget):
    picked = pyqtSignal(object)          # whatever the mark carries (e.g. (group, status))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self._marks = []                 # [(QPainterPath or QRectF, tooltip, payload)]
        self._hover = None

    def _hit(self, pos):
        for i, (shape, _, _) in enumerate(self._marks):
            if shape.contains(QPointF(pos)):
                return i
        return None

    def mouseMoveEvent(self, e):
        i = self._hit(e.pos())
        if i != self._hover:
            self._hover = i
            self.update()
        if i is None:
            QToolTip.hideText()
            self.setCursor(Qt.ArrowCursor)
        else:
            QToolTip.showText(e.globalPos(), self._marks[i][1], self)
            self.setCursor(Qt.PointingHandCursor)

    def leaveEvent(self, e):
        self._hover = None
        self.update()

    def mousePressEvent(self, e):
        i = self._hit(e.pos())
        if i is not None and e.button() == Qt.LeftButton:
            self.picked.emit(self._marks[i][2])

    def _empty(self, p, text="Nothing to show yet"):
        p.setPen(QColor(MUTED))
        p.setFont(_font(12))
        p.drawText(self.rect(), Qt.AlignCenter, text)


class DonutChart(_Chart):
    """slices: [(label, count, color)] — the legend lists every slice with its count."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.slices, self.center = [], ("", "")
        self.setMinimumHeight(170)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def set_data(self, slices, center=("", "")):
        self.slices = [s for s in slices if s[1] > 0]
        self.center = center
        self.setFixedHeight(max(170, 24 * len(self.slices) + 30))
        fm, bold = QFontMetrics(_font(12)), QFontMetrics(_font(12, bold=True))
        label_w = max((bold.horizontalAdvance(l or "No status") for l, _, _ in self.slices), default=80)
        count_w = max((fm.horizontalAdvance(f"{n}  ·  100%") for _, n, _ in self.slices), default=40)
        d = min(self.height() - 20, 150)
        self.setFixedWidth(int(min(10 + d + 24 + 18 + label_w + 16 + count_w + 14, 560)))
        self.update()

    def sizeHint(self):
        return QSize(380, self.height())

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self._marks = []
        total = sum(n for _, n, _ in self.slices)
        if not total:
            self._empty(p)
            return
        d = min(self.height() - 20, 150)
        ring = QRectF(10, (self.height() - d) / 2, d, d)
        hole = d * 0.62
        start = 90.0
        # a slice and its legend row highlight together
        hv = self._hover % len(self.slices) if self._hover is not None else None
        for i, (label, n, color) in enumerate(self.slices):
            span = -360.0 * n / total
            path = QPainterPath()
            path.arcMoveTo(ring, start)
            path.arcTo(ring, start, span)
            inner = QRectF(ring.center().x() - hole / 2, ring.center().y() - hole / 2, hole, hole)
            path.arcTo(inner, start + span, -span)
            path.closeSubpath()
            c = QColor(color)
            if hv is not None and hv != i:
                c.setAlpha(110)
            p.setPen(QPen(QColor(SURFACE), 2) if len(self.slices) > 1 else Qt.NoPen)
            p.setBrush(c)
            p.drawPath(path)
            pct = round(100 * n / total)
            self._marks.append((path, f"<b>{label or 'No status'}</b><br>{n} of {total} ({pct}%)", label))
            start += span

        big, small = self.center
        p.setPen(QColor(INK))
        p.setFont(_font(22, bold=True))
        mid = ring.center()
        p.drawText(QRectF(mid.x() - hole / 2, mid.y() - 20, hole, 26), Qt.AlignCenter, big)
        p.setPen(QColor(INK2))
        p.setFont(_font(11))
        p.drawText(QRectF(mid.x() - hole / 2, mid.y() + 6, hole, 16), Qt.AlignCenter, small)

        # legend: swatch, label, count, percent — so color is never the only cue
        x0 = ring.right() + 24
        y = (self.height() - 24 * len(self.slices)) / 2
        fm = QFontMetrics(_font(12))
        for i, (label, n, color) in enumerate(self.slices):
            row = QRectF(x0 - 6, y, self.width() - x0, 24)
            hovered = hv == i
            if hovered:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#F3F2EF"))
                p.drawRoundedRect(row, 5, 5)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(color))
            p.drawRoundedRect(QRectF(x0, y + 7, 10, 10), 2, 2)
            p.setPen(QColor(INK))
            p.setFont(_font(12, bold=hovered))
            count = f"{n}  ·  {round(100 * n / total)}%"
            cw = fm.horizontalAdvance(count) + 8
            name = fm.elidedText(label or "No status", Qt.ElideRight, int(row.width() - cw - 30))
            p.drawText(QRectF(x0 + 18, y, row.width(), 24), Qt.AlignVCenter, name)
            p.setPen(QColor(INK2))
            p.setFont(_font(12))
            p.drawText(QRectF(x0, y, row.width() - 8, 24), Qt.AlignVCenter | Qt.AlignRight, count)
            self._marks.append((row, f"<b>{label or 'No status'}</b><br>{n} of {total} — click to see them", label))
            y += 24


class StackedBars(_Chart):
    """groups: [(group label, [(status, count, color), …])] — one bar per group."""
    ROW = 26

    def __init__(self, parent=None):
        super().__init__(parent)
        self.groups, self.blank = [], "(blank)"
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_data(self, groups, blank="(blank)"):
        self.groups, self.blank = groups, blank
        self.setFixedHeight(max(60, self.ROW * len(groups) + 8))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self._marks = []
        if not self.groups:
            self._empty(p)
            return
        fm = QFontMetrics(_font(12))
        label_w = min(max(fm.horizontalAdvance(g or self.blank) for g, _ in self.groups) + 12,
                      int(self.width() * 0.35))
        top = max(sum(n for _, n, _ in segs) for _, segs in self.groups) or 1
        bar_w = self.width() - label_w - 40
        y = 4
        for group, segs in self.groups:
            p.setPen(QColor(INK2))
            p.setFont(_font(12))
            p.drawText(QRectF(0, y, label_w - 8, self.ROW), Qt.AlignVCenter | Qt.AlignRight,
                       fm.elidedText(group or self.blank, Qt.ElideRight, label_w - 10))
            x = label_w
            total = sum(n for _, n, _ in segs)
            for status, n, color in segs:
                if not n:
                    continue
                w = bar_w * n / top
                rect = QRectF(x, y + 6, max(w - 2, 1.5), self.ROW - 12)   # 2px gap between segments
                i = len(self._marks)
                c = QColor(color)
                if self._hover is not None and self._hover != i:
                    c.setAlpha(120)
                p.setPen(Qt.NoPen)
                p.setBrush(c)
                p.drawRoundedRect(rect, 2, 2)
                self._marks.append((rect.adjusted(0, -4, 2, 4),
                                    f"<b>{group or self.blank}</b> · {status or 'No status'}<br>"
                                    f"{n} of {total} — click to see them", (group, status)))
                x += w
            p.setPen(QColor(INK))
            p.setFont(_font(12, bold=True))
            p.drawText(QRectF(x + 6, y, 40, self.ROW), Qt.AlignVCenter, str(total))
            y += self.ROW


class DayColumns(_Chart):
    """days: [(date, [(status, count, color), …])] — one column per day, oldest first."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.days = []
        self.setFixedHeight(170)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_data(self, days):
        self.days = days
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self._marks = []
        if not self.days:
            self._empty(p, "No dated rows yet")
            return
        left, bottom, top_pad = 28, 22, 8
        h = self.height() - bottom - top_pad
        top = max(sum(n for _, n, _ in segs) for _, segs in self.days) or 1
        step = max(1, math.ceil(top / 4))
        # recessive gridlines + y labels
        p.setFont(_font(10))
        for v in range(0, top + 1, step):
            yy = top_pad + h - h * v / top
            p.setPen(QPen(QColor(GRID), 1))
            p.drawLine(QPointF(left, yy), QPointF(self.width(), yy))
            p.setPen(QColor(MUTED))
            p.drawText(QRectF(0, yy - 7, left - 6, 14), Qt.AlignRight | Qt.AlignVCenter, str(v))
        slot = (self.width() - left) / len(self.days)
        col_w = min(28, slot * 0.7)
        label_every = max(1, math.ceil(46 / slot))
        for k, (day, segs) in enumerate(self.days):
            x = left + slot * k + (slot - col_w) / 2
            y = top_pad + h
            total = sum(n for _, n, _ in segs)
            for status, n, color in segs:
                if not n:
                    continue
                seg_h = h * n / top
                rect = QRectF(x, y - seg_h + 1, col_w, max(seg_h - 2, 1.5))
                i = len(self._marks)
                c = QColor(color)
                if self._hover is not None and self._hover != i:
                    c.setAlpha(120)
                p.setPen(Qt.NoPen)
                p.setBrush(c)
                p.drawRoundedRect(rect, 2, 2)
                self._marks.append((rect.adjusted(-3, 0, 3, 0),
                                    f"<b>{day:%a %b %-d}</b> · {status or 'No status'}<br>"
                                    f"{n} of {total} that day", (day, status)))
                y -= seg_h
            if k % label_every == 0:
                p.setPen(QColor(MUTED))
                p.setFont(_font(10))
                p.drawText(QRectF(x - 20, top_pad + h + 4, col_w + 40, 16), Qt.AlignCenter,
                           f"{day.month}/{day.day}")


class MiniBar(QWidget):
    """A thin progress bar split by status class — used on the summary cards."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.segs = []
        self.setFixedHeight(8)

    def set_data(self, segs):
        self.segs = [s for s in segs if s[1] > 0]
        self.setToolTip("<br>".join(f"{label}: {n}" for label, n, _ in self.segs))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#E0DDD8"))
        p.drawRoundedRect(QRectF(self.rect()), 4, 4)
        total = sum(n for _, n, _ in self.segs)
        if not total:
            return
        x = 0.0
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), 4, 4)
        p.setClipPath(clip)
        for _, n, color in self.segs:
            w = self.width() * n / total
            p.setBrush(QColor(color))
            p.drawRect(QRectF(x, 0, max(w - 2, 1), self.height()))
            x += w


class ComponentTally(_Chart):
    """Counts per component, grouped by subsystem, including components with no
    runs yet. groups: [(subsystem, color, [(component label, count, value), …])].
    Laid out in columns that wrap to fit the width."""
    ROW, HEAD, COL_W, GAP = 22, 30, 230, 16

    def __init__(self, parent=None):
        super().__init__(parent)
        self.groups = []
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_data(self, groups):
        self.groups = groups
        self._relayout()
        self.update()

    def resizeEvent(self, e):
        self._relayout()
        super().resizeEvent(e)

    def _columns(self):
        return max(1, int((self.width() + self.GAP) // (self.COL_W + self.GAP)))

    def _relayout(self):
        """Place each subsystem block in the currently shortest column."""
        n = self._columns()
        heights, self._placed = [0] * n, []
        for g in self.groups:
            col = heights.index(min(heights))
            self._placed.append((g, col, heights[col]))
            heights[col] += self.HEAD + self.ROW * len(g[2]) + 14
        self.setFixedHeight(max(heights) if self.groups else 60)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self._marks = []
        if not self.groups:
            self._empty(p)
            return
        top = max((n for _, _, parts in self.groups for _, n, _ in parts), default=0) or 1
        fm = QFontMetrics(_font(12))
        for (sub, color, parts), col, y in self._placed:
            x = col * (self.COL_W + self.GAP)
            total = sum(n for _, n, _ in parts)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(color))
            p.drawRoundedRect(QRectF(x, y + 9, 10, 10), 2, 2)
            p.setPen(QColor(INK))
            p.setFont(_font(13, bold=True))
            p.drawText(QRectF(x + 16, y, self.COL_W, 28), Qt.AlignVCenter, sub)
            p.setPen(QColor(INK2 if total else MUTED))
            p.setFont(_font(12, bold=bool(total)))
            p.drawText(QRectF(x, y, self.COL_W - 4, 28), Qt.AlignVCenter | Qt.AlignRight, str(total))
            p.setPen(QPen(QColor(GRID), 1))
            p.drawLine(QPointF(x, y + self.HEAD - 2), QPointF(x + self.COL_W, y + self.HEAD - 2))
            yy = y + self.HEAD
            for label, n, value in parts:
                row = QRectF(x, yy, self.COL_W, self.ROW)
                i = len(self._marks)
                if self._hover == i:
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor("#F3F2EF"))
                    p.drawRoundedRect(row, 4, 4)
                if n:                                   # a light bar sized by count
                    bar = QColor(color)
                    bar.setAlpha(70)
                    p.setPen(Qt.NoPen)
                    p.setBrush(bar)
                    p.drawRoundedRect(QRectF(x + 2, yy + 4, (self.COL_W - 40) * n / top, self.ROW - 8), 3, 3)
                p.setPen(QColor(INK if n else MUTED))
                p.setFont(_font(12))
                p.drawText(row.adjusted(8, 0, -36, 0), Qt.AlignVCenter,
                           fm.elidedText(label, Qt.ElideRight, int(self.COL_W - 48)))
                p.setFont(_font(12, bold=bool(n)))
                p.drawText(row.adjusted(0, 0, -4, 0), Qt.AlignVCenter | Qt.AlignRight, str(n))
                tip = f"<b>{sub} · {label}</b><br>{n} run{'s' if n != 1 else ''}"
                self._marks.append((row, tip + (" — click to see them" if n else ""), value if n else None))
                yy += self.ROW

    def mousePressEvent(self, e):
        i = self._hit(e.pos())
        if i is not None and e.button() == Qt.LeftButton and self._marks[i][2] is not None:
            self.picked.emit(self._marks[i][2])


def tab_colors(tab, rows=None):
    """(the row-status pseudo-column, {status value: color}) for a sheet tab —
    covers every status column, e.g. both "Coder E2E Status" and "Atlantis Status"."""
    import tracker_store as store
    col = store.derived_columns(tab).get(store.STATUS)
    if not col:
        return None, {}
    rows = tab["rows"] if rows is None else rows
    values = {store.row_status(tab, r) for r in rows}
    values |= {r["values"][c["pos"]].strip() for c in store.status_columns(tab) for r in rows}
    return col, status_colors(values, col["options"])
