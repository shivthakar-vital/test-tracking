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
CATEGORICAL = ["#2A78D6", "#EB6834", "#1BAF7A", "#EDA100", "#E87BA4", "#008300", "#4A3AA7", "#E34948"]

# (keywords, class, color) — checked in order, so "no results" wins over "results"
_RULES = [
    (("block", "error", "fail", "broken"),                          "blocked",  CRITICAL),
    (("cancel", "abort"),                                          "cancelled", SERIOUS),
    (("no result", "warn", "partial", "flaky"),                     "warning",  WARNING),
    (("defer", "next release", "wont", "won't", "descoped"),        "deferred", DEFERRED),
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


def status_colors(values, options=()):
    """{value: color}. Known meanings get their status color; others take the
    categorical slots in the dropdown's option order, so a value keeps its color
    even when filters change which values are on screen."""
    colors, used = {}, 0
    ordered = list(options) + sorted(set(values) - set(options))
    for v in ordered:
        low = (v or "").strip().lower()
        if not low:
            colors[v] = PENDING
            continue
        for words, _, color in _RULES:
            if any(w in low for w in words):
                colors[v] = color
                break
        else:
            colors[v] = CATEGORICAL[used % len(CATEGORICAL)]
            used += 1
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
        self.groups = []
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_data(self, groups):
        self.groups = groups
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
        label_w = min(max(fm.horizontalAdvance(g or "(blank)") for g, _ in self.groups) + 12,
                      int(self.width() * 0.35))
        top = max(sum(n for _, n, _ in segs) for _, segs in self.groups) or 1
        bar_w = self.width() - label_w - 40
        y = 4
        for group, segs in self.groups:
            p.setPen(QColor(INK2))
            p.setFont(_font(12))
            p.drawText(QRectF(0, y, label_w - 8, self.ROW), Qt.AlignVCenter | Qt.AlignRight,
                       fm.elidedText(group or "(blank)", Qt.ElideRight, label_w - 10))
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
                                    f"<b>{group or '(blank)'}</b> · {status or 'No status'}<br>"
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
