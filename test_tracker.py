"""
Test Tracker — a desktop dashboard for software release testing.

It reads a shared Google Sheet (one tab per tracker: features, end-to-end runs,
bugs…), shows a dashboard of where testing stands, and lets you edit cells,
which are written straight back to the sheet. Set it up under ⚙ Settings;
see README.md.

Run from source:
    pip install -r requirements.txt
    python test_tracker.py

Build a double-clickable app:
    ./build_mac.sh
"""

import json, re, subprocess, sys, urllib.request
from collections import Counter, defaultdict
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QTableView, QHeaderView, QMessageBox, QFrame,
    QDialog, QFormLayout, QDialogButtonBox, QFileDialog, QShortcut, QComboBox,
    QCheckBox, QTabWidget, QScrollArea, QStyledItemDelegate, QDateEdit, QMenu,
    QToolButton, QWidgetAction, QListWidget, QListWidgetItem, QPlainTextEdit,
    QAbstractItemView, QListView, QSizePolicy
)
from PyQt5.QtCore import (
    Qt, QObject, QRunnable, QThreadPool, QTimer, QUrl, QDate, pyqtSignal,
    QAbstractTableModel, QModelIndex, QSortFilterProxyModel
)
from PyQt5.QtGui import (
    QFont, QColor, QPalette, QKeySequence, QDesktopServices, QPixmap, QPainter, QIcon
)

import charts
import tracker_store as store

APP_TITLE   = "Test Tracker"
APP_VERSION = "1.0.1"         # bump this for each release, then push a matching tag (v1.0.1)
REFRESH_MS  = 60_000          # pull changes from the sheet every minute
REPO        = "shivthakar-vital/test-tracking"
INSTALL_CMD = f"curl -fsSL https://raw.githubusercontent.com/{REPO}/main/install.sh | bash"

INK, MUTED = "#1A1A18", "#999996"
GREEN, AMBER, RED = "#3B6D11", "#854F0B", "#A32D2D"
ACCENT, LINK = "#1D9E75", "#2F5E9E"

STYLESHEET = """
    QMainWindow, QWidget#central, QDialog, QScrollArea, QWidget#dash { background: #F5F5F2; }
    QLabel { color: #1A1A18; background: transparent; }
    QLineEdit, QDateEdit, QPlainTextEdit {
        background: #FFFFFF; color: #1A1A18;
        border: 1.5px solid #CCCCC8; border-radius: 6px;
        padding: 6px 10px; font-size: 13px;
        selection-background-color: #1D9E75;
    }
    QLineEdit:focus, QDateEdit:focus, QPlainTextEdit:focus { border: 1.5px solid #1D9E75; }
    QComboBox {
        background: #FFFFFF; border: 1px solid #CCCCC8; border-radius: 6px;
        padding: 4px 10px; font-size: 13px;
    }
    QTableView {
        background: #FFFFFF; color: #1A1A18;
        border: 1px solid #E0DDD8; border-radius: 8px;
        gridline-color: #F0EEE9; font-size: 13px;
        alternate-background-color: #FAFAF8;
    }
    QTableView::item { padding: 0 10px; border: none; }
    QTableView::item:selected { background: #E1F5EE; color: #1A1A18; }
    QHeaderView::section {
        background: #EEECEA; color: #666663; font-size: 11px; font-weight: bold;
        padding: 6px 10px; border: none; border-bottom: 1px solid #E0DDD8;
        border-right: 1px solid #E6E4E0;
    }
    QPushButton, QToolButton {
        background: #FFFFFF; color: #1A1A18;
        border: 1px solid #CCCCC8; border-radius: 6px;
        padding: 6px 14px; font-size: 13px;
    }
    QPushButton:hover, QToolButton:hover   { background: #EEECEA; }
    QPushButton:pressed, QToolButton:pressed { background: #E0DDD8; }
    QPushButton:disabled { color: #AAAAA6; }
    QToolButton::menu-indicator { image: none; }
    QToolButton[active="true"] { background: #E1F5EE; border-color: #1D9E75; color: #0F6E56; }
    QPushButton#primary {
        background: #1D9E75; color: #FFFFFF; border: 2px solid #1D9E75; font-weight: bold;
    }
    QPushButton#primary:hover   { background: #17896A; border-color: #17896A; }
    QPushButton#primary:disabled { background: #8CCDB8; border-color: #8CCDB8; }
    QPushButton#update {
        background: #2A78D6; color: #FFFFFF; border: none; font-weight: bold;
        padding: 3px 12px; font-size: 11px;
    }
    QPushButton#link { border: none; background: transparent; color: #2F5E9E; padding: 2px 4px; }
    QPushButton#link:hover { text-decoration: underline; }
    QFrame#card { background: #FFFFFF; border: 1px solid #E6E4E0; border-radius: 10px; }
    QFrame#tile { background: #FFFFFF; border: 1px solid #E6E4E0; border-radius: 10px; }
    QFrame#tile:hover { border-color: #1D9E75; }
    QFrame#banner { background: #FFF6E5; border: 1px solid #F3D9A4; border-radius: 6px; }
    QTabWidget::pane { border: none; }
    QTabBar::tab {
        background: transparent; color: #666663; padding: 8px 16px; font-size: 13px;
        border: none; border-bottom: 2px solid transparent;
    }
    QTabBar::tab:selected { color: #1A1A18; border-bottom: 2px solid #1D9E75; font-weight: bold; }
    QTabBar::tab:hover { color: #1A1A18; }
    QCheckBox { font-size: 12px; color: #52514E; }
    QListWidget { background: #FFFFFF; border: 1px solid #E0DDD8; border-radius: 6px; font-size: 13px; }
    QScrollBar:vertical { background: #F5F5F2; width: 8px; border-radius: 4px; }
    QScrollBar::handle:vertical { background: #CCCCC8; border-radius: 4px; min-height: 30px; }
    QScrollBar:horizontal { background: #F5F5F2; height: 8px; border-radius: 4px; }
    QScrollBar::handle:horizontal { background: #CCCCC8; border-radius: 4px; min-width: 30px; }
"""

BLANK = "(blank)"


# ── small helpers ─────────────────────────────────────────────────────────────

def _small_label(text, color="#666663", size=11):
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color:{color}; font-size:{size}px;")
    return lbl

def _natural(text):
    """Sort key that orders numbers numerically: PROD-2 before PROD-10."""
    return [(0, int(t), "") if t.isdigit() else (1, 0, t)
            for t in re.split(r"(\d+)", (text or "").lower()) if t]

def _open_link(url):
    if url:
        QDesktopServices.openUrl(QUrl(url))

_dots = {}
def _dot(color):
    """A small colored circle shown next to status values."""
    if color not in _dots:
        pm = QPixmap(20, 20)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(3, 3, 14, 14)
        p.end()
        _dots[color] = QIcon(pm)
    return _dots[color]

def _version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3])

def _visible_rows(tab):
    return [r for r in tab["rows"] if not store.is_blank(r)]

def _colors_for(tab, rows=None):
    col = store.status_column(tab)
    if not col:
        return None, {}
    rows = tab["rows"] if rows is None else rows
    values = {r["values"][col["pos"]] for r in rows}
    return col, charts.status_colors(values, col["options"])


# ── background work ───────────────────────────────────────────────────────────
# Google Sheets calls take a moment, so they run off the UI thread. The pool has
# a single thread, which keeps operations in the order they were requested.

class _TaskSignals(QObject):
    done   = pyqtSignal(object)
    failed = pyqtSignal(object)

class _Task(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.setAutoDelete(False)
        self.fn = fn
        self.signals = _TaskSignals()

    def run(self):
        try:
            result = self.fn()
        except Exception as exc:
            self.signals.failed.emit(exc)
        else:
            self.signals.done.emit(result)


# ── settings ──────────────────────────────────────────────────────────────────

class SettingsDialog(QDialog):
    """Releases (one Google Sheet link each) and the service-account key."""

    def __init__(self, parent, cfg, first_run=False):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(620)
        self._new_creds = None
        self.sheets = [dict(s) for s in cfg["sheets"]]

        intro = None
        if first_run:
            intro = QLabel("<b>Welcome!</b> Paste the link to your team's test-tracking Google "
                           "Sheet below and click <b>Add</b>. Ask Shiv for the link and the key "
                           "file if you don't have them.")
            intro.setWordWrap(True)

        self.list = QListWidget()
        self.list.setMinimumHeight(110)
        self._fill_list()
        self.url = QLineEdit()
        self.url.setPlaceholderText("Paste a Google Sheet link: https://docs.google.com/spreadsheets/d/…")
        self.url.returnPressed.connect(self._add)
        add = QPushButton("Add")
        add.clicked.connect(self._add)
        remove = QPushButton("Remove selected")
        remove.clicked.connect(self._remove)
        add_row = QHBoxLayout()
        add_row.addWidget(self.url, 1)
        add_row.addWidget(add)
        add_row.addWidget(remove)

        self.creds_lbl = QLabel()
        self.creds_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.creds_lbl.setWordWrap(True)
        pick = QPushButton("Choose key file…")
        pick.clicked.connect(self._pick)
        creds_row = QHBoxLayout()
        creds_row.addWidget(self.creds_lbl, 1)
        creds_row.addWidget(pick)
        self._show_creds(store.CREDS_FILE)

        hint = _small_label(
            "Each release can be its own Google Sheet (File → Make a copy), or a new tab in "
            "the same sheet. New tabs show up by themselves; new sheets are added here.\n"
            "Share every sheet with the service-account email above as an Editor.", MUTED)
        hint.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.setSpacing(10)
        if intro:
            root.addWidget(intro)
        root.addWidget(_small_label("Release sheets"))
        root.addWidget(self.list)
        root.addLayout(add_row)
        root.addSpacing(6)
        root.addWidget(_small_label("Service account (the robot account the app signs in with)"))
        root.addLayout(creds_row)
        root.addWidget(hint)
        root.addWidget(buttons)

    def _fill_list(self):
        self.list.clear()
        for s in self.sheets:
            item = QListWidgetItem(f"{s.get('title') or 'New sheet (loads when you save)'}\n{s['url']}")
            self.list.addItem(item)

    def _add(self):
        url = self.url.text().strip()
        if not url:
            return
        if not store.sheet_id(url):
            QMessageBox.warning(self, "Not a sheet link",
                                "That doesn't look like a Google Sheets link. It should start with "
                                "https://docs.google.com/spreadsheets/d/…")
            return
        if any(store.sheet_id(s["url"]) == store.sheet_id(url) for s in self.sheets):
            self.url.clear()
            return
        self.sheets.append({"url": url, "title": ""})
        self.new_url = url
        self.url.clear()
        self._fill_list()

    def _remove(self):
        row = self.list.currentRow()
        if row >= 0:
            del self.sheets[row]
            self._fill_list()

    def _show_creds(self, path):
        email = store.service_account_email(path)
        self.creds_lbl.setText(f"✓ {email}" if email else "No key file yet")

    def _pick(self):
        path, _ = QFileDialog.getOpenFileName(self, "Service account key", "", "JSON key (*.json)")
        if not path:
            return
        if not store.service_account_email(path):
            QMessageBox.warning(self, "Not a key file",
                                "That file doesn't look like a Google service-account key.")
            return
        self._new_creds = path
        self._show_creds(path)

    def accept(self):
        if self.url.text().strip():
            self._add()
        super().accept()


# ── table model, sorting and filtering ────────────────────────────────────────

SORT_ROLE = Qt.UserRole + 1

class TabModel(QAbstractTableModel):
    """One sheet tab. Edits call `on_edit(snapshot, col, new, old)` and show at once."""

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.tab = {"columns": [], "rows": []}
        self.colors, self.status = {}, None

    def set_tab(self, tab):
        self.beginResetModel()
        self.tab = tab
        self.status, self.colors = _colors_for(tab)
        self.multi = {c["pos"] for c in tab["columns"] if store.is_multi(c, tab["rows"])}
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.tab["rows"])

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.tab["columns"])

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.tab["columns"][section]["name"]
        if orientation == Qt.Horizontal and role == Qt.ToolTipRole:
            c = self.tab["columns"][section]
            kind = {"dropdown": "Dropdown", "date": "Date", "number": "Number",
                    "checkbox": "Checkbox"}.get(c["kind"], "Text")
            if c["link"]:
                kind += " with link"
            return f"{c['name']} · {kind}\nClick to sort, click again to reverse."
        return None

    def row(self, r):
        return self.tab["rows"][r]

    def editable(self, index):
        row, c = self.row(index.row()), index.column()
        return self.page.win.can_edit and not store.readonly_tab(self.tab) and not row["locked"][c]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row, c = self.row(index.row()), index.column()
        col = self.tab["columns"][c]
        value, link = row["values"][c], row["links"][c]
        if role == Qt.DisplayRole:
            first = value.split("\n", 1)[0]
            return first + (" …" if "\n" in value else "")
        if role == Qt.EditRole:
            return value
        if role == Qt.ToolTipRole:
            tip = value
            if link:
                tip += f"\n\n🔗 {link}\nDouble-click to open the link."
            if row["locked"][c]:
                tip += "\n\nThis cell has a formula. Change it in the sheet."
            return tip.strip() or None
        if role == Qt.ForegroundRole:
            if link:
                return QColor(LINK)
            if row["locked"][c]:
                return QColor(MUTED)
            return None
        if role == Qt.FontRole and link:
            f = QFont()
            f.setUnderline(True)
            return f
        if role == Qt.DecorationRole and self.status is col and value:
            return _dot(self.colors.get(value, charts.PENDING))
        if role == SORT_ROLE:
            if not value.strip():
                return (1, [])                    # blanks sort last
            if col["kind"] == "date":
                d = store.parse_date(value)
                return (0, [(0, d.toordinal(), "")] if d else _natural(value))
            if self.status is col:
                return (0, [(0, charts.CLASS_ORDER.index(charts.classify(value)), ""), *_natural(value)])
            return (0, _natural(value))
        return None

    def flags(self, index):
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        return f | Qt.ItemIsEditable if index.isValid() and self.editable(index) else f

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole or not index.isValid():
            return False
        row, col = self.row(index.row()), self.tab["columns"][index.column()]
        old = row["values"][col["pos"]]
        value = "" if value is None else str(value)
        if value == old:
            return False
        snapshot = dict(row, values=list(row["values"]))
        row["values"][col["pos"]] = value
        self.dataChanged.emit(index, index)
        self.page.win.write_cell(self.tab, snapshot, col, value, old)
        return True


class FilterProxy(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.words, self.filters, self.show_empty = [], {}, False
        self.setSortRole(SORT_ROLE)

    def set_search(self, text):
        self.words = text.lower().split()
        self.invalidateFilter()

    def set_filter(self, pos, allowed):
        """allowed: a set of values to show, or None for all."""
        if allowed is None:
            self.filters.pop(pos, None)
        else:
            self.filters[pos] = allowed
        self.invalidateFilter()

    def filterAcceptsRow(self, r, parent):
        m = self.sourceModel()
        row = m.row(r)
        if not self.show_empty and store.is_blank(row):
            return False
        for pos, allowed in self.filters.items():
            col = m.tab["columns"][pos]
            if not any(p.strip() in allowed for p in store.parts(col, row["values"][pos])):
                return False
        if self.words:
            hay = " ".join(row["values"]).lower()
            if not all(w in hay for w in self.words):
                return False
        return True

    def lessThan(self, a, b):
        ka, kb = a.data(SORT_ROLE), b.data(SORT_ROLE)
        try:
            return ka < kb
        except TypeError:
            return str(ka) < str(kb)


class FilterButton(QToolButton):
    """ "Status ▾" — a popup checklist of the column's values. All checked = no filter."""
    changed = pyqtSignal(int, object)

    def __init__(self, col):
        super().__init__()
        self.col, self.values = col, []
        self.setPopupMode(QToolButton.InstantPopup)
        self.menu_ = QMenu(self)
        self.list = QListWidget()
        self.list.setMinimumWidth(240)
        self.list.itemChanged.connect(self._emit)
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(8, 8, 8, 8)
        btns = QHBoxLayout()
        for text, state in (("Select all", Qt.Checked), ("Clear", Qt.Unchecked)):
            b = QPushButton(text)
            b.clicked.connect(lambda _, s=state: self._set_all(s))
            btns.addWidget(b)
        lay.addLayout(btns)
        lay.addWidget(self.list)
        act = QWidgetAction(self.menu_)
        act.setDefaultWidget(box)
        self.menu_.addAction(act)
        self.setMenu(self.menu_)
        self._refresh_text()

    def set_values(self, values, counts):
        """Keep the user's choices across syncs; new values start checked."""
        was = self.selected()
        known = set(self.values)
        self.values = values
        self.list.blockSignals(True)
        self.list.clear()
        for v in values:
            item = QListWidgetItem(f"{v or BLANK}   ({counts.get(v, 0)})")
            item.setData(Qt.UserRole, v)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            checked = was is None or v in was or v not in known
            item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            self.list.addItem(item)
        self.list.setFixedHeight(min(26 * len(values) + 6, 320))
        self.list.blockSignals(False)
        self._refresh_text()

    def selected(self):
        """The checked values, or None when everything is checked."""
        vals = [self.list.item(i) for i in range(self.list.count())]
        on = {i.data(Qt.UserRole) for i in vals if i.checkState() == Qt.Checked}
        return None if len(on) == len(vals) else on

    def select_only(self, values):
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setCheckState(Qt.Checked if item.data(Qt.UserRole) in values else Qt.Unchecked)
        self.list.blockSignals(False)
        self._emit()

    def _set_all(self, state):
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(state)
        self.list.blockSignals(False)
        self._emit()

    def _emit(self, *_):
        self._refresh_text()
        self.changed.emit(self.col["pos"], self.selected())

    def _refresh_text(self):
        sel = self.selected() if self.list.count() else None
        name = self.col["name"]
        if sel is None:
            text = f"{name}  ▾"
        elif len(sel) == 1:
            text = f"{name}: {next(iter(sel)) or BLANK}  ▾"
        else:
            text = f"{name}: {len(sel)} of {self.list.count()}  ▾"
        self.setText(text)
        self.setProperty("active", sel is not None)
        self.style().unpolish(self)
        self.style().polish(self)


# ── editing ───────────────────────────────────────────────────────────────────

def _is_long_text(col, rows):
    return col["kind"] == "text" and (
        "note" in col["name"].lower() or any("\n" in r["values"][col["pos"]] for r in rows))

class CellDelegate(QStyledItemDelegate):
    """Dropdowns for dropdown columns, a calendar for dates, a text box otherwise."""

    def __init__(self, page):
        super().__init__(page)
        self.page = page

    def createEditor(self, parent, option, index):
        col = self.page.model.tab["columns"][index.column()]
        value = index.data(Qt.EditRole)
        src = self.page.proxy.mapToSource(index)
        if self.page._needs_dialog(self.page.model.row(src.row()), col):
            QTimer.singleShot(0, self.page.edit_current_in_dialog)
            return None
        if col["kind"] == "dropdown" or col["kind"] == "checkbox":
            combo = QComboBox(parent)
            opts = col["options"] if col["kind"] == "dropdown" else ["TRUE", "FALSE"]
            combo.addItem("— clear —", "")
            for o in opts:
                combo.addItem(o, o)
            if value and value not in opts:
                combo.addItem(value, value)
            if col["kind"] == "dropdown":
                combo.addItem("Select several…", "__multi__")
            colors = charts.status_colors(opts, opts) if col is self.page.model.status else {}
            for i in range(combo.count()):
                v = combo.itemData(i)
                if v in colors:
                    combo.setItemIcon(i, _dot(colors[v]))
            combo.activated.connect(lambda _: self._commit(combo))
            QTimer.singleShot(0, combo.showPopup)
            return combo
        if col["kind"] == "date":
            ed = QDateEdit(parent)
            ed.setCalendarPopup(True)
            ed.setDisplayFormat("M/d/yyyy")
            return ed
        return super().createEditor(parent, option, index)

    def _commit(self, editor):
        if editor.currentData() == "__multi__":
            self.closeEditor.emit(editor, QStyledItemDelegate.RevertModelCache)
            QTimer.singleShot(0, self.page.edit_current_in_dialog)
            return
        self.commitData.emit(editor)
        self.closeEditor.emit(editor)

    def setEditorData(self, editor, index):
        value = index.data(Qt.EditRole)
        if isinstance(editor, QComboBox):
            i = editor.findData(value)
            editor.setCurrentIndex(max(i, 0))
        elif isinstance(editor, QDateEdit):
            d = store.parse_date(value)
            editor.setDate(QDate(d.year, d.month, d.day) if d else QDate.currentDate())
        else:
            super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):
        if isinstance(editor, QComboBox):
            model.setData(index, editor.currentData())
        elif isinstance(editor, QDateEdit):
            d = editor.date()
            model.setData(index, f"{d.month()}/{d.day()}/{d.year()}")
        else:
            super().setModelData(editor, model, index)


class CellDialog(QDialog):
    """Editing that doesn't fit in a cell: links (text + URL), several dropdown
    choices at once, and long notes."""

    def __init__(self, parent, col, value, link, rows, multi=False):
        super().__init__(parent)
        self.setWindowTitle(f"Edit {col['name']}")
        self.setMinimumWidth(480)
        self.col = col
        root = QVBoxLayout(self)
        self.text = self.url = self.checks = self.notes = None
        if col["kind"] == "dropdown":
            root.addWidget(_small_label("Pick one or more:"))
            self.checks = QListWidget()
            chosen = set(store.parts(col, value)) if value else set()
            for o in col["options"]:
                item = QListWidgetItem(o)
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if o in chosen else Qt.Unchecked)
                self.checks.addItem(item)
            self.checks.setFixedHeight(min(26 * len(col["options"]) + 6, 300))
            root.addWidget(self.checks)
        elif col["link"] or link:
            form = QFormLayout()
            self.text = QLineEdit(value)
            self.url = QLineEdit(link)
            self.url.setPlaceholderText("https://…")
            form.addRow("Text", self.text)
            form.addRow("Link", self.url)
            root.addLayout(form)
        else:
            self.notes = QPlainTextEdit(value)
            self.notes.setMinimumHeight(140)
            root.addWidget(self.notes)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def result_value(self):
        """(text, link or None)"""
        if self.checks is not None:
            on = [self.checks.item(i).text() for i in range(self.checks.count())
                  if self.checks.item(i).checkState() == Qt.Checked]
            return ", ".join(on), None
        if self.url is not None:
            return self.text.text().strip(), self.url.text().strip()
        return self.notes.toPlainText().rstrip(), None


class AddRowDialog(QDialog):
    def __init__(self, parent, tab, rows):
        super().__init__(parent)
        self.setWindowTitle(f"Add to {tab['title']}")
        self.setMinimumWidth(560)
        self.tab = tab
        self.widgets = {}
        form = QFormLayout()
        form.setSpacing(8)
        first = tab["columns"][0]
        nums = [int(r["values"][0]) for r in rows if r["values"][0].strip().isdigit()]
        filled_nums = [int(r["values"][0]) for r in rows
                       if r["values"][0].strip().isdigit() and not store.is_blank(r)]
        for col in tab["columns"]:
            multi = store.is_multi(col, rows)
            if col["kind"] == "dropdown" and not multi:
                w = QComboBox()
                w.addItem("", "")
                for o in col["options"]:
                    w.addItem(o, o)
            elif col["kind"] == "dropdown":
                w = QListWidget()
                w.setFlow(QListView.LeftToRight)
                w.setWrapping(True)
                w.setFixedHeight(60)
                for o in col["options"]:
                    item = QListWidgetItem(o)
                    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                    item.setCheckState(Qt.Unchecked)
                    w.addItem(item)
            elif col["kind"] == "date":
                w = QDateEdit(QDate.currentDate())
                w.setCalendarPopup(True)
                w.setDisplayFormat("M/d/yyyy")
            elif _is_long_text(col, rows):
                w = QPlainTextEdit()
                w.setFixedHeight(64)
            else:
                w = QLineEdit()
                if col is first and nums:
                    # next run number: the first unused one, e.g. fill placeholder row 4
                    w.setText(str(max(filled_nums, default=0) + 1))
            self.widgets[col["pos"]] = w
            if col["link"]:
                url = QLineEdit()
                url.setPlaceholderText("Link (optional): https://…")
                self.widgets[("link", col["pos"])] = url
                box = QVBoxLayout()
                box.setSpacing(4)
                box.addWidget(w)
                box.addWidget(url)
                form.addRow(col["name"], box)
            else:
                form.addRow(col["name"], w)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        ok = buttons.addButton("Add row", QDialogButtonBox.AcceptRole)
        ok.setObjectName("primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        inner.setLayout(form)
        scroll.setWidget(inner)
        root = QVBoxLayout(self)
        root.addWidget(scroll)
        root.addWidget(buttons)
        self.resize(600, min(120 + 44 * len(tab["columns"]), 720))

    def values(self):
        vals, links = {}, {}
        for key, w in self.widgets.items():
            if isinstance(key, tuple):
                if w.text().strip():
                    links[key[1]] = w.text().strip()
                continue
            if isinstance(w, QComboBox):
                v = w.currentData()
            elif isinstance(w, QListWidget):
                v = ", ".join(w.item(i).text() for i in range(w.count())
                              if w.item(i).checkState() == Qt.Checked)
            elif isinstance(w, QDateEdit):
                d = w.date()
                v = f"{d.month()}/{d.day()}/{d.year()}"
            elif isinstance(w, QPlainTextEdit):
                v = w.toPlainText().strip()
            else:
                v = w.text().strip()
            if v:
                vals[key] = v
        return vals, links


# ── a tab of the sheet ────────────────────────────────────────────────────────

class TabPage(QWidget):
    def __init__(self, win, tab):
        super().__init__()
        self.win = win
        self.title = tab["title"]
        self.model = TabModel(self)
        self.proxy = FilterProxy()
        self.proxy.setSourceModel(self.model)
        self.proxy.show_empty = win.cfg.get("show_empty_rows", False)
        self.filter_btns = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 10, 0, 0)
        root.setSpacing(8)

        self.banner = QFrame()
        self.banner.setObjectName("banner")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(12, 6, 12, 6)
        bl.addWidget(QLabel("🔒 This tab is filled in automatically (from Jira), so it's "
                            "read-only here. Change these in Jira."))
        root.addWidget(self.banner)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search this tab…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._on_search)
        self.filter_row = QHBoxLayout()
        self.filter_row.setSpacing(6)
        self.extra_lbl = _small_label("", LINK)
        self.clear_btn = QPushButton("✕ Clear filters")
        self.clear_btn.setObjectName("link")
        self.clear_btn.clicked.connect(self.clear_filters)
        self.empty_chk = QCheckBox("Show empty rows")
        self.empty_chk.setToolTip("Rows with nothing filled in yet, e.g. run numbers waiting for a run")
        self.empty_chk.setChecked(self.proxy.show_empty)
        self.empty_chk.toggled.connect(self._on_show_empty)
        self.count_lbl = _small_label("", MUTED)
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(self.search, 2)
        top.addLayout(self.filter_row)
        top.addWidget(self.extra_lbl)
        top.addWidget(self.clear_btn)
        top.addStretch(1)
        top.addWidget(self.empty_chk)
        top.addWidget(self.count_lbl)
        root.addLayout(top)

        self.view = QTableView()
        self.view.setModel(self.proxy)
        self.view.setItemDelegate(CellDelegate(self))
        self.view.setSortingEnabled(True)
        self.view.sortByColumn(-1, Qt.AscendingOrder)       # sheet order until a header is clicked
        self.view.horizontalHeader().setSortIndicatorShown(True)
        self.view.horizontalHeader().setSectionsMovable(True)
        self.view.horizontalHeader().setHighlightSections(False)
        self.view.horizontalHeader().setMinimumSectionSize(60)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(34)
        self.view.setAlternatingRowColors(True)
        self.view.setShowGrid(True)
        self.view.setWordWrap(False)
        self.view.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.view.setEditTriggers(QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed)
        self.view.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.view.doubleClicked.connect(self._on_double_click)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context_menu)
        self.view.selectionModel().currentChanged.connect(lambda *_: self._update_buttons())
        root.addWidget(self.view, 1)

        actions = QHBoxLayout()
        self.add_btn = QPushButton("+ Add row")
        self.add_btn.setObjectName("primary")
        self.add_btn.clicked.connect(self.add_row)
        self.edit_btn = QPushButton("Edit cell…")
        self.edit_btn.clicked.connect(self.edit_current)
        self.link_btn = QPushButton("Open link ↗")
        self.link_btn.clicked.connect(self.open_current_link)
        sheet_btn = QPushButton("Open this tab in Google Sheets")
        sheet_btn.clicked.connect(lambda: _open_link(store.tab_url(self.win.url, self.model.tab)))
        self.hint = _small_label("Double-click a cell to edit it · double-click a link to open it · "
                                 "right-click for more · click a column header to sort", MUTED)
        for b in (self.add_btn, self.edit_btn, self.link_btn):
            actions.addWidget(b)
        actions.addSpacing(10)
        actions.addWidget(self.hint, 1)
        actions.addWidget(sheet_btn)
        root.addLayout(actions)

        QShortcut(QKeySequence("Ctrl+F"), self, self.search.setFocus)
        self.set_tab(tab, first=True)

    # ── data ──────────────────────────────────────────────────────────────────

    def set_tab(self, tab, first=False):
        # keep the selected cell and scroll position across syncs
        cur = self.view.currentIndex()
        keep = None
        if cur.isValid():
            src = self.proxy.mapToSource(cur)
            keep = (self.model.row(src.row())["r"], src.column())
        vscroll = self.view.verticalScrollBar().value()
        hscroll = self.view.horizontalScrollBar().value()

        # Filters, sorting, widths and the selection are tied to columns by name, so
        # they stay on the right column when columns are added, moved or removed.
        old_cols = self.model.tab["columns"]
        new_pos = {c["name"]: c["pos"] for c in tab["columns"]}
        moved = {c["pos"]: new_pos.get(c["name"]) for c in old_cols}
        widths = {c["name"]: self.view.columnWidth(c["pos"]) for c in old_cols}
        sort_col, sort_order = self.proxy.sortColumn(), self.proxy.sortOrder()
        if any(old != new for old, new in moved.items()):
            self.proxy.filters = {moved[p]: v for p, v in self.proxy.filters.items()
                                  if moved.get(p) is not None}
            btns = {}
            for p, btn in self.filter_btns.items():
                if moved.get(p) is None:
                    btn.deleteLater()
                else:
                    btns[moved[p]] = btn
            self.filter_btns = btns
            if keep:
                keep = (keep[0], moved.get(keep[1]))
            sort_col = moved.get(sort_col, -1) if sort_col >= 0 else -1

        self.model.set_tab(tab)
        self._build_filters(tab)
        if first:
            self._size_columns()
        else:
            for c in tab["columns"]:
                if c["name"] in widths:
                    self.view.setColumnWidth(c["pos"], widths[c["name"]])
                else:                                  # a new column: fit it to its contents
                    self.view.resizeColumnToContents(c["pos"])
                    w = self.view.columnWidth(c["pos"])
                    self.view.setColumnWidth(c["pos"], max(90, min(w + 16, 340)))
            if sort_col != self.proxy.sortColumn():
                self.view.sortByColumn(sort_col, sort_order)
        self.proxy.invalidateFilter()
        if keep and keep[1] is not None:
            for r, row in enumerate(tab["rows"]):
                if row["r"] == keep[0]:
                    idx = self.proxy.mapFromSource(self.model.index(r, keep[1]))
                    if idx.isValid():
                        self.view.setCurrentIndex(idx)
                    break
        self.view.verticalScrollBar().setValue(vscroll)
        self.view.horizontalScrollBar().setValue(hscroll)
        ro = store.readonly_tab(tab)
        self.banner.setVisible(ro)
        self.add_btn.setVisible(not ro)
        self.edit_btn.setVisible(not ro)
        self.empty_chk.setVisible(any(store.is_blank(r) for r in tab["rows"]))
        self._update_counts()
        self._update_buttons()

    def _size_columns(self):
        hdr = self.view.horizontalHeader()
        self.view.resizeColumnsToContents()
        for c in range(self.model.columnCount()):
            w = self.view.columnWidth(c)
            self.view.setColumnWidth(c, max(90, min(w + 16, 340)))
        hdr.setStretchLastSection(True)

    def _build_filters(self, tab):
        rows = _visible_rows(tab)
        status = store.status_column(tab)
        cols = ([status] if status else []) + store.group_columns(tab, rows)
        wanted = {c["pos"] for c in cols}
        for pos in list(self.filter_btns):
            if pos not in wanted:
                btn = self.filter_btns.pop(pos)
                self.proxy.set_filter(pos, None)
                btn.deleteLater()
        for c in cols:
            counts = Counter(p.strip() for r in rows for p in store.parts(c, r["values"][c["pos"]]))
            values = list(c["options"]) + sorted(v for v in counts if v not in c["options"])
            if c is status:
                values = charts.status_order(values, c["options"])
            if "" in counts and "" not in values:
                values.append("")
            btn = self.filter_btns.get(c["pos"])
            if btn is None:
                btn = FilterButton(c)
                btn.changed.connect(self._on_filter)
                self.filter_btns[c["pos"]] = btn
                self.filter_row.addWidget(btn)
            btn.col = c
            btn.set_values(values, counts)

    def _on_search(self, text):
        self.proxy.set_search(text)
        self._update_counts()

    def _on_filter(self, pos, allowed):
        self.proxy.set_filter(pos, allowed)
        self._update_counts()

    def _on_show_empty(self, on):
        self.proxy.show_empty = on
        self.proxy.invalidateFilter()
        self.win.cfg["show_empty_rows"] = on
        store.save_config(self.win.cfg)
        self._update_counts()

    def clear_filters(self):
        self.search.clear()
        for pos, btn in self.filter_btns.items():
            btn._set_all(Qt.Checked)
        for pos in list(self.proxy.filters):
            self.proxy.set_filter(pos, None)
        self._update_counts()

    def show_only(self, filters):
        """filters: {column pos: set of values} — used when a chart is clicked."""
        self.clear_filters()
        for pos, values in filters.items():
            if pos in self.filter_btns:
                self.filter_btns[pos].select_only(values)
            else:
                self.proxy.set_filter(pos, set(values))
        self._update_counts()

    def _update_counts(self):
        total = len([r for r in self.model.tab["rows"] if self.proxy.show_empty or not store.is_blank(r)])
        shown = self.proxy.rowCount()
        self.count_lbl.setText(f"Showing {shown} of {total}" if shown != total else f"{total} rows")
        extra = [self.model.tab["columns"][p]["name"] + ": " + ", ".join(sorted(v or BLANK for v in vals))
                 for p, vals in self.proxy.filters.items() if p not in self.filter_btns]
        self.extra_lbl.setText("Filtered by " + "; ".join(extra) if extra else "")
        self.clear_btn.setVisible(bool(self.proxy.filters or self.proxy.words))

    # ── editing ───────────────────────────────────────────────────────────────

    def _current(self):
        idx = self.view.currentIndex()
        if not idx.isValid():
            return None, None, None
        src = self.proxy.mapToSource(idx)
        return src, self.model.row(src.row()), self.model.tab["columns"][src.column()]

    def _update_buttons(self):
        src, row, col = self._current()
        self.link_btn.setEnabled(bool(row and row["links"][col["pos"]]))
        self.edit_btn.setEnabled(bool(src and self.model.editable(src)))
        self.add_btn.setEnabled(self.win.can_edit)

    def _needs_dialog(self, row, col):
        return (col["link"] or row["links"][col["pos"]] or col["pos"] in self.model.multi
                or _is_long_text(col, self.model.tab["rows"]))

    def _on_double_click(self, idx):
        src, row, col = self._current()
        if row is None:
            return
        link = row["links"][col["pos"]]
        if link:
            _open_link(link)
            return
        self.edit_current()

    def edit_current(self):
        src, row, col = self._current()
        if row is None:
            return
        if not self.model.editable(src):
            if not self.win.can_edit:
                QMessageBox.information(self, "Can't edit right now",
                                        "Editing is paused until the app reconnects to Google Sheets.")
            elif row["locked"][col["pos"]]:
                QMessageBox.information(self, "Formula cell",
                                        "This cell is calculated by a formula. Change it in the sheet.")
            return
        if self._needs_dialog(row, col):
            self.edit_current_in_dialog()
        else:
            self.view.edit(self.view.currentIndex())

    def edit_current_in_dialog(self):
        src, row, col = self._current()
        if row is None or not self.model.editable(src):
            return
        pos = col["pos"]
        dlg = CellDialog(self, col, row["values"][pos], row["links"][pos], self.model.tab["rows"])
        if dlg.exec_() != QDialog.Accepted:
            return
        value, link = dlg.result_value()
        if value == row["values"][pos] and (link is None or link == row["links"][pos]):
            return
        snapshot = dict(row, values=list(row["values"]))
        old = row["values"][pos]
        row["values"][pos] = value
        if link is not None:
            row["links"][pos] = link
        self.model.dataChanged.emit(src, src)
        self.win.write_cell(self.model.tab, snapshot, col, value, old, link)

    def open_current_link(self):
        _, row, col = self._current()
        if row:
            _open_link(row["links"][col["pos"]])

    def _context_menu(self, pos):
        idx = self.view.indexAt(pos)
        if not idx.isValid():
            return
        self.view.setCurrentIndex(idx)
        src, row, col = self._current()
        value, link = row["values"][col["pos"]], row["links"][col["pos"]]
        menu = QMenu(self)
        if self.model.editable(src):
            menu.addAction("Edit…", self.edit_current)
            if value:
                menu.addAction("Clear cell", lambda: self.model.setData(src, ""))
        if link:
            menu.addAction("Open link ↗", lambda: _open_link(link))
        menu.addAction("Copy", lambda: QApplication.clipboard().setText(link if link and not value else value))
        if col["pos"] in self.filter_btns or col["kind"] in ("dropdown", "date") or value:
            menu.addSeparator()
            show = set(store.parts(col, value)) if value else {""}
            menu.addAction(f"Show only “{value or BLANK}” in {col['name']}",
                           lambda: self.show_only({col["pos"]: show}))
        menu.exec_(self.view.viewport().mapToGlobal(pos))

    def add_row(self):
        if not self.win.can_edit:
            return
        dlg = AddRowDialog(self, self.model.tab, self.model.tab["rows"])
        if dlg.exec_() != QDialog.Accepted:
            return
        values, links = dlg.values()
        if not values:
            return
        self.win.add_row(self.model.tab, values, links)


# ── dashboard ─────────────────────────────────────────────────────────────────

class Dashboard(QScrollArea):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.body = None

    def set_data(self, data):
        pos = self.verticalScrollBar().value()
        body = QWidget()
        body.setObjectName("dash")
        root = QVBoxLayout(body)
        root.setContentsMargins(0, 14, 8, 20)
        root.setSpacing(14)

        title = QLabel(data["title"])
        title.setStyleSheet("font-size:20px; font-weight:bold;")
        when = data.get("fetched_at", "")
        try:
            when = datetime.fromisoformat(when).strftime("%b %-d, %H:%M")
        except ValueError:
            pass
        head = QHBoxLayout()
        head.addWidget(title)
        head.addSpacing(10)
        head.addWidget(_small_label(f"Data as of {when} · hover over a chart for details, click it "
                                    "to see those rows", MUTED), 1, Qt.AlignBottom)
        root.addLayout(head)

        tiles = QGridLayout()
        tiles.setSpacing(10)
        tabs = [t for t in data["tabs"] if _visible_rows(t) or t["rows"]]
        for i, tab in enumerate(tabs):
            tiles.addWidget(self._tile(tab), i // 4, i % 4)
        for c in range(min(4, len(tabs))):
            tiles.setColumnStretch(c, 1)
        root.addLayout(tiles)

        for tab in tabs:
            root.addWidget(self._section(tab))
        root.addStretch(1)
        self.setWidget(body)
        self.body = body
        QTimer.singleShot(0, lambda: self.verticalScrollBar().setValue(pos))

    def _tile(self, tab):
        rows = _visible_rows(tab)
        status, colors = _colors_for(tab, rows)
        tile = QFrame()
        tile.setObjectName("tile")
        tile.setCursor(Qt.PointingHandCursor)
        tile.setToolTip(f"Open {tab['title']}")
        tile.mousePressEvent = lambda e, t=tab["title"]: self.win.show_tab(t)
        lay = QVBoxLayout(tile)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(4)
        lay.addWidget(_small_label(tab["title"], "#666663", 11))
        mini = charts.MiniBar()
        if status and rows:
            counts = Counter(r["values"][status["pos"]].strip() for r in rows)
            cls = Counter()
            for v, n in counts.items():
                cls[charts.classify(v)] += n
            done, total = cls["done"], len(rows)
            big = QLabel(f"{round(100 * done / total)}%")
            big.setStyleSheet("font-size:24px; font-weight:bold;")
            bits = [f"{done} of {total} done"]
            if cls["blocked"]:
                bits.append(f"{cls['blocked']} blocked")
            if cls["active"]:
                bits.append(f"{cls['active']} in progress")
            sub = _small_label(" · ".join(bits), "#52514E", 11)
            order = charts.status_order(list(counts), status["options"])
            mini.set_data([(v or "No status", counts[v], colors.get(v, charts.PENDING)) for v in order])
        else:
            big = QLabel(str(len(rows)))
            big.setStyleSheet("font-size:24px; font-weight:bold;")
            sub = _small_label("rows", "#52514E", 11)
        lay.addWidget(big)
        lay.addWidget(sub)
        lay.addWidget(mini)
        return tile

    def _section(self, tab):
        rows = _visible_rows(tab)
        status, colors = _colors_for(tab, rows)
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 14, 18, 16)
        lay.setSpacing(10)
        head = QHBoxLayout()
        t = QLabel(tab["title"])
        t.setStyleSheet("font-size:15px; font-weight:bold;")
        head.addWidget(t)
        detail = f"{len(rows)} row{'s' if len(rows) != 1 else ''}" + (f" · by {status['name']}" if status else "")
        head.addWidget(_small_label(detail, MUTED), 0, Qt.AlignBottom)
        head.addStretch(1)
        open_btn = QPushButton("Open table →")
        open_btn.setObjectName("link")
        open_btn.clicked.connect(lambda: self.win.show_tab(tab["title"]))
        head.addWidget(open_btn)
        lay.addLayout(head)

        if not rows:
            lay.addWidget(_small_label("Nothing filled in yet.", MUTED))
            return card
        if not status:
            lay.addWidget(_small_label("No status column found, so there's nothing to chart. "
                                       "Add a dropdown column named “Status” to get charts.", MUTED))
            return card

        pos = status["pos"]
        counts = Counter(r["values"][pos].strip() for r in rows)
        order = charts.status_order(list(counts), status["options"])
        done = sum(n for v, n in counts.items() if charts.classify(v) == "done")

        charts_row = QHBoxLayout()
        charts_row.setSpacing(28)
        donut = charts.DonutChart()
        donut.set_data([(v, counts[v], colors.get(v, charts.PENDING)) for v in order],
                       (f"{round(100 * done / len(rows))}%", "done"))
        donut.picked.connect(lambda v, t=tab["title"]: self.win.show_tab(t, {pos: {v}}))
        charts_row.addWidget(self._titled(status["name"], donut), 0, Qt.AlignTop)

        for g in store.group_columns(tab, rows, limit=3):
            groups = defaultdict(Counter)
            for r in rows:
                for part in store.parts(g, r["values"][g["pos"]]):
                    groups[part.strip()][r["values"][pos].strip()] += 1
            if len(groups) < 2 and "" in groups:
                continue                       # nobody has filled this column in yet
            names = sorted(groups, key=lambda k: (k == "", -sum(groups[k].values()), k))[:12]
            bars = charts.StackedBars()
            bars.set_data([(n, [(v, groups[n][v], colors.get(v, charts.PENDING)) for v in order])
                           for n in names])
            bars.picked.connect(lambda gv, t=tab["title"], gp=g["pos"]:
                                self.win.show_tab(t, {gp: {gv[0]}, pos: {gv[1]}}))
            charts_row.addWidget(self._titled(f"{status['name']} by {g['name']}", bars), 1, Qt.AlignTop)
        charts_row.addStretch(0)
        lay.addLayout(charts_row)

        dcol = store.date_column(tab)
        if dcol:
            days = defaultdict(Counter)
            raw = defaultdict(set)
            for r in rows:
                d = store.parse_date(r["values"][dcol["pos"]])
                if d:
                    days[d][r["values"][pos].strip()] += 1
                    raw[d].add(r["values"][dcol["pos"]])
            if days:
                col_chart = charts.DayColumns()
                col_chart.set_data([(d, [(v, days[d][v], colors.get(v, charts.PENDING)) for v in order])
                                    for d in sorted(days)])
                col_chart.picked.connect(lambda ds, t=tab["title"], dp=dcol["pos"]:
                                         self.win.show_tab(t, {dp: raw[ds[0]], pos: {ds[1]}}))
                lay.addWidget(self._titled(f"Per day ({dcol['name']})", col_chart))
        return card

    @staticmethod
    def _titled(title, widget):
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(_small_label(title.upper(), "#888885", 10))
        lay.addWidget(widget)
        return box


# ── main window ───────────────────────────────────────────────────────────────

class TrackerApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1280, 820)
        self.setMinimumSize(980, 600)
        self.setStyleSheet(STYLESHEET)

        self.adopted_key = store.adopt_inventory_key()
        self.cfg = store.load_config()
        self.url = self.cfg.get("current") or (self.cfg["sheets"][0]["url"] if self.cfg["sheets"] else "")
        self.data = store.load_cache(self.url) if self.url else None
        self.source = None
        self.can_edit = False
        self.last_sync = None
        self.pages = {}
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.net_pool = QThreadPool(self)          # update checks, kept off the sheet queue
        self._tasks = set()

        self._build_ui()
        if self.data:
            self._show_data(self.data)

        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self._sync(quiet=True))
        self.timer.start(REFRESH_MS)
        QTimer.singleShot(0, self._start)
        QTimer.singleShot(1500, self._check_for_update)

    def _build_ui(self):
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(20, 14, 20, 10)
        root.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(_small_label("Release"))
        self.release = QComboBox()
        self.release.setMinimumWidth(260)
        self.release.setMaximumWidth(420)
        self.release.setMinimumHeight(32)
        self.release.activated.connect(self._on_release_picked)
        top.addWidget(self.release)
        top.addStretch(1)
        self.open_btn = QPushButton("Open in Google Sheets ↗")
        self.open_btn.clicked.connect(self._open_sheet)
        self.sync_btn = QPushButton("↻ Sync")
        self.sync_btn.setToolTip("Pull the latest data from the Google Sheet (⌘R)")
        self.sync_btn.clicked.connect(lambda: self._sync(quiet=False))
        settings_btn = QPushButton("⚙ Settings")
        settings_btn.clicked.connect(lambda: self._open_settings())
        for b in (self.open_btn, self.sync_btn, settings_btn):
            top.addWidget(b)
        root.addLayout(top)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.dashboard = Dashboard(self)
        self.tabs.addTab(self.dashboard, "📊 Dashboard")
        self.empty = QLabel("Add your test-tracking Google Sheet in ⚙ Settings to get started.")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet(f"color:{MUTED}; font-size:14px;")
        self.dashboard.setWidget(self.empty)
        root.addWidget(self.tabs, 1)

        status_row = QHBoxLayout()
        self.status_lbl = _small_label("Ready.", MUTED)
        self.conn_lbl = _small_label("")
        self.update_btn = QPushButton()
        self.update_btn.setObjectName("update")
        self.update_btn.hide()
        self.update_btn.clicked.connect(self._install_update)
        status_row.addWidget(self.status_lbl, 1)
        status_row.addWidget(self.update_btn)
        status_row.addSpacing(10)
        status_row.addWidget(self.conn_lbl)
        version_lbl = _small_label(f"v{APP_VERSION}", MUTED)
        version_lbl.setStyleSheet(f"color:{MUTED}; font-size:11px; margin-left:10px;")
        status_row.addWidget(version_lbl)
        root.addLayout(status_row)

        QShortcut(QKeySequence("Ctrl+R"), self, lambda: self._sync(quiet=False))
        self._fill_releases()

    # ── helpers ───────────────────────────────────────────────────────────────

    def _set_status(self, msg):
        self.status_lbl.setText(msg)

    def _set_conn(self, state, detail=""):
        text, color = {
            "none":       ("● No sheet yet", MUTED),
            "connecting": ("● Connecting to Google Sheets…", AMBER),
            "sheets":     (f"● Google Sheets · synced {self.last_sync:%H:%M}" if self.last_sync
                           else "● Google Sheets", GREEN),
            "offline":    ("● Offline — showing last synced data", RED),
        }[state]
        self.can_edit = state == "sheets"
        self.conn_lbl.setText(text)
        self.conn_lbl.setStyleSheet(f"color:{color}; font-size:11px;")
        self.conn_lbl.setToolTip(detail)
        for page in self.pages.values():
            page._update_buttons()

    def _fill_releases(self):
        self.release.blockSignals(True)
        self.release.clear()
        for s in self.cfg["sheets"]:
            self.release.addItem(s.get("title") or s["url"], s["url"])
        self.release.addItem("+ Add another release…", "__add__")
        i = self.release.findData(self.url)
        self.release.setCurrentIndex(max(i, 0) if self.cfg["sheets"] else 0)
        self.release.blockSignals(False)

    def _on_release_picked(self, i):
        url = self.release.itemData(i)
        if url == "__add__":
            self._fill_releases()
            self._open_settings()
            return
        if url and url != self.url:
            self._switch(url)

    def _switch(self, url):
        self.url = url
        self.cfg["current"] = url
        store.save_config(self.cfg)
        self.source = None
        self.data = store.load_cache(url)
        for page in self.pages.values():
            self.tabs.removeTab(self.tabs.indexOf(page))
            page.deleteLater()
        self.pages = {}
        if self.data:
            self._show_data(self.data)
        else:
            self.dashboard.setWidget(QLabel())
        self._fill_releases()
        self._connect()

    def _open_sheet(self):
        page = self.tabs.currentWidget()
        if isinstance(page, TabPage):
            _open_link(store.tab_url(self.url, page.model.tab))
        elif self.url:
            _open_link(self.url)

    def show_tab(self, title, filters=None):
        page = self.pages.get(title)
        if page is None:
            return
        self.tabs.setCurrentWidget(page)
        if filters:
            page.show_only(filters)

    # ── background runner ─────────────────────────────────────────────────────

    def _run(self, fn, on_done, on_error=None, busy_msg=None):
        if busy_msg:
            self._set_status(busy_msg)
            self.sync_btn.setEnabled(False)
        task = _Task(fn)

        def finish():
            self._tasks.discard(task)
            if busy_msg:
                self.sync_btn.setEnabled(True)

        task.signals.done.connect(lambda r: (finish(), on_done(r)))
        task.signals.failed.connect(lambda e: (finish(), (on_error or self._on_error)(e)))
        self._tasks.add(task)
        self.pool.start(task)

    def _on_error(self, exc, note="Your change was not saved."):
        msg = store.describe_error(exc)
        self._set_status(msg.splitlines()[0])
        if not isinstance(exc, store.TrackerError):
            self._set_conn("offline", msg)
        QMessageBox.warning(self, "Problem", f"{msg}\n\n{note}".strip())

    # ── connecting & syncing ──────────────────────────────────────────────────

    def _start(self):
        if not self.cfg["sheets"]:
            self._set_conn("none")
            self._open_settings(first_run=True)
            return
        self._connect()

    def _connect(self):
        if not self.url:
            self._set_conn("none")
            return
        url = self.url
        self._set_conn("connecting")
        self._run(lambda: store.SheetSource(url), self._on_connected, self._on_connect_failed,
                  busy_msg="Connecting to Google Sheets…")

    def _on_connected(self, source):
        if source.url != self.url:
            return                              # the user switched release meanwhile
        self.source = source
        self._run(source.fetch, lambda d: self._on_fetched(d, "Loaded from Google Sheets."),
                  lambda e: self._on_connect_failed(e), busy_msg="Loading…")

    def _on_connect_failed(self, exc):
        msg = store.describe_error(exc)
        self._set_conn("offline", msg)
        self._set_status("Not connected to Google Sheets. Click ↻ Sync to retry.")
        extra = ("\n\nShowing the last synced data. Editing is paused until the connection works."
                 if self.data else "")
        QMessageBox.warning(self, "Couldn't connect to Google Sheets", msg + extra)

    def _sync(self, quiet):
        if self.source is None:
            if not quiet:
                self._connect()
            return
        if quiet and (self._tasks or self._busy_editing()):
            return            # something is running, or someone is mid-edit; skip this tick
        src = self.source
        self._run(src.fetch,
                  lambda d: self._on_fetched(d, None if quiet else "Synced."),
                  on_error=(lambda e: self._set_conn("offline", store.describe_error(e))) if quiet
                           else (lambda e: self._on_error(e, note="")),
                  busy_msg=None if quiet else "Syncing…")

    def _busy_editing(self):
        return QApplication.activeModalWidget() is not None or QApplication.activePopupWidget() is not None \
            or any(p.view.state() == QAbstractItemView.EditingState for p in self.pages.values())

    def _on_fetched(self, data, msg=None):
        if data["url"] != self.url:
            return
        self.last_sync = datetime.now()
        self._set_conn("sheets")
        for s in self.cfg["sheets"]:
            if s["url"] == data["url"] and s.get("title") != data["title"]:
                s["title"] = data["title"]
                store.save_config(self.cfg)
                self._fill_releases()
        self._show_data(data)
        store.save_cache(self.url, data)
        if msg:
            self._set_status(msg)

    def _show_data(self, data):
        self.data = data
        self.setWindowTitle(f"{APP_TITLE} — {data['title']}")
        titles = [t["title"] for t in data["tabs"]]
        for title in list(self.pages):
            if title not in titles:
                page = self.pages.pop(title)
                self.tabs.removeTab(self.tabs.indexOf(page))
                page.deleteLater()
        for i, tab in enumerate(data["tabs"]):
            page = self.pages.get(tab["title"])
            if page is None:
                page = TabPage(self, tab)
                self.pages[tab["title"]] = page
                self.tabs.insertTab(i + 1, page, tab["title"])
            else:
                page.set_tab(tab)
                if self.tabs.indexOf(page) != i + 1:
                    self.tabs.removeTab(self.tabs.indexOf(page))
                    self.tabs.insertTab(i + 1, page, tab["title"])
            if store.readonly_tab(tab):
                self.tabs.setTabToolTip(i + 1, "Filled in automatically; read-only here")
        self.dashboard.set_data(data)

    # ── writing ───────────────────────────────────────────────────────────────

    def _after_write(self, msg):
        self._set_status(msg)
        store.save_cache(self.url, self.data)
        self.dashboard.set_data(self.data)
        QTimer.singleShot(1500, lambda: self._sync(quiet=True))    # pick up the sheet's formatting

    def write_cell(self, tab, snapshot, col, new, old, link=None, force=False):
        if self.source is None:
            return
        src = self.source
        label = f"{col['name']} → “{new or 'blank'}”"
        self._run(lambda: src.write_cell(tab, snapshot, col, new, old, link, force),
                  lambda _: self._after_write(f"Saved {label}."),
                  lambda exc: self._write_failed(exc, tab, snapshot, col, new, link),
                  busy_msg=f"Saving {label}…")

    def _write_failed(self, exc, tab, snapshot, col, new, link):
        if isinstance(exc, store.ConflictError) and exc.current is not None:
            reply = QMessageBox.question(
                self, "Changed by someone else",
                f"Since your last sync, someone changed {col['name']} in this row to:\n\n"
                f"   “{exc.current or 'blank'}”\n\nReplace it with yours?\n\n   “{new or 'blank'}”",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.write_cell(tab, snapshot, col, new, exc.current, link, force=True)
                return
            self._set_status("Kept their change.")
        elif isinstance(exc, store.ConflictError):
            QMessageBox.information(self, "Row changed", str(exc))
        else:
            self._on_error(exc)
        self._sync(quiet=False)          # show what's really in the sheet

    def add_row(self, tab, values, links):
        if self.source is None:
            return
        src = self.source
        self._run(lambda: src.add_row(tab, values, links),
                  lambda how: (self._set_status("Row added." if how == "appended"
                                                else f"Filled in row {values.get(0, '')}."),
                               self._sync(quiet=False)),
                  lambda exc: (self._on_error(exc), self._sync(quiet=True)),
                  busy_msg="Adding row…")

    # ── settings ──────────────────────────────────────────────────────────────

    def _open_settings(self, first_run=False):
        dlg = SettingsDialog(self, self.cfg, first_run)
        if self.adopted_key and first_run:
            self._set_status("Using the service-account key from Qave Inventory.")
        if dlg.exec_() != QDialog.Accepted:
            return
        if dlg._new_creds:
            store.install_credentials(dlg._new_creds)
        self.cfg["sheets"] = dlg.sheets
        store.save_config(self.cfg)
        urls = [s["url"] for s in dlg.sheets]
        new = getattr(dlg, "new_url", None)
        target = new if new in urls else (self.url if self.url in urls else (urls[0] if urls else ""))
        self._fill_releases()
        if target != self.url or dlg._new_creds or self.source is None:
            if target:
                self._switch(target)
            else:
                self.url = ""
                self._set_conn("none")

    # ── updates ───────────────────────────────────────────────────────────────

    def _check_for_update(self):
        def latest():
            req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                         headers={"Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=8) as r:
                return json.load(r).get("tag_name", "")

        task = _Task(latest)

        def got(tag):
            self._tasks.discard(task)
            if _version_tuple(tag) > _version_tuple(APP_VERSION):
                self.latest = tag
                self.update_btn.setText(f"⬆ Update to {tag}")
                self.update_btn.setToolTip("A newer version is available. Click to install it.")
                self.update_btn.show()

        task.signals.done.connect(got)
        task.signals.failed.connect(lambda _: self._tasks.discard(task))   # offline: try next launch
        self._tasks.add(task)
        self.net_pool.start(task)
        QTimer.singleShot(6 * 3600 * 1000, self._check_for_update)

    def _install_update(self):
        if not (getattr(sys, "frozen", False) and sys.platform == "darwin"):
            _open_link(f"https://github.com/{REPO}/releases/latest")
            return
        reply = QMessageBox.question(
            self, "Update Test Tracker",
            f"Install {self.latest} now?\n\nThe app will close, update, and reopen by itself "
            "in about a minute. Nothing in the sheet is affected.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if reply != QMessageBox.Yes:
            return
        subprocess.Popen(["/bin/bash", "-c", INSTALL_CMD], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._set_status("Updating… the app will reopen by itself.")
        QTimer.singleShot(800, QApplication.quit)

    def closeEvent(self, event):
        self.pool.waitForDone(5000)     # let an in-flight save finish
        super().closeEvent(event)


def _light_palette():
    """Keep the app readable even when macOS is in dark mode."""
    pal = QPalette()
    for role, color in [(QPalette.Window, "#F5F5F2"), (QPalette.WindowText, INK),
                        (QPalette.Base, "#FFFFFF"), (QPalette.AlternateBase, "#FAFAF8"),
                        (QPalette.Text, INK), (QPalette.Button, "#FFFFFF"),
                        (QPalette.ButtonText, INK), (QPalette.ToolTipBase, "#FFFFFF"),
                        (QPalette.ToolTipText, INK), (QPalette.Highlight, ACCENT),
                        (QPalette.HighlightedText, "#FFFFFF"), (QPalette.PlaceholderText, MUTED)]:
        pal.setColor(role, QColor(color))
    return pal


if __name__ == "__main__":
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setApplicationVersion(APP_VERSION)
    app.setStyle("Fusion")   # consistent look on all platforms including macOS
    app.setPalette(_light_palette())
    window = TrackerApp()
    window.show()
    sys.exit(app.exec_())
