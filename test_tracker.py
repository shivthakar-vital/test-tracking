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

import html, os, re, subprocess, sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QTableView, QHeaderView, QMessageBox, QFrame,
    QDialog, QFormLayout, QDialogButtonBox, QFileDialog, QShortcut, QComboBox,
    QCheckBox, QTabWidget, QScrollArea, QStyledItemDelegate, QDateEdit, QMenu,
    QToolButton, QWidgetAction, QListWidget, QListWidgetItem, QPlainTextEdit,
    QAbstractItemView, QListView, QSizePolicy, QColorDialog, QInputDialog
)
from PyQt5.QtCore import (
    Qt, QObject, QRunnable, QThreadPool, QTimer, QUrl, QDate, pyqtSignal,
    QAbstractTableModel, QModelIndex, QSortFilterProxyModel
)
from PyQt5.QtGui import (
    QFont, QColor, QPalette, QKeySequence, QDesktopServices, QPixmap, QPainter, QIcon
)

import charts
import export_report
import tracker_store as store

APP_TITLE   = "Test Tracker"
APP_VERSION = "1.1.3"         # bump this for each release, then push a matching tag (v1.1.3)
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
WRAP_MAX_LINES = 8          # taller cells are cut off; the tooltip and Edit… show all of it


# ── small helpers ─────────────────────────────────────────────────────────────

def _small_label(text, color="#666663", size=11):
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color:{color}; font-size:{size}px;")
    return lbl

def _natural(text):
    """Sort key that orders numbers numerically: PROD-2 before PROD-10."""
    return [(0, int(t), "") if t.isdigit() else (1, 0, t)
            for t in re.split(r"(\d+)", (text or "").lower()) if t]

_ZWSP = "\u200b"
def _breakable(text):
    """Let wrapped text break inside long paths, versions and hashes
    (e.g. /tmp/repo/qa/…, staging-808ec07…) by adding invisible break points."""
    text = re.sub(r"([/_\-.:,=])(?=\S)", "\\1" + _ZWSP, text)
    return re.sub(r"(\S{24})(?=\S)", "\\1" + _ZWSP, text)

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

def _words_of(text):
    return set(re.findall(r"[a-z]+", (text or "").lower()))

def _plural_days(n):
    return f"{n} day{'s' if n != 1 else ''}"

def _visible_rows(tab):
    return [r for r in tab["rows"] if not store.is_blank(r)]

_colors_for = charts.tab_colors

def _settings(win):
    return (win.data or {}).get("settings") or {}


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

        colors_btn = QPushButton("🎨 Chart colours…")
        colors_btn.setToolTip("Admin only: change the colour of each status, for everyone")
        colors_btn.clicked.connect(lambda: parent.open_colors(self))
        colors_btn.setEnabled(bool(parent.data) and parent.can_edit)
        columns_btn = QPushButton("📊 Charts & filters…")
        columns_btn.setToolTip("Admin only: choose which columns get filters and dashboard charts")
        columns_btn.clicked.connect(lambda: parent.open_columns(self))
        columns_btn.setEnabled(bool(parent.data) and parent.can_edit)
        admin_row = QHBoxLayout()
        admin_row.addWidget(colors_btn)
        admin_row.addWidget(columns_btn)
        admin_row.addWidget(_small_label("🔒 Needs the admin passcode. Applies to everyone "
                                         "using this release sheet.", MUTED), 1)

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
        root.addSpacing(6)
        root.addWidget(_small_label("Admin"))
        root.addLayout(admin_row)
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


# ── admin: passcode and chart colours ─────────────────────────────────────────

class NewPasscodeDialog(QDialog):
    def __init__(self, parent, title="Set an admin passcode", intro=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        root = QVBoxLayout(self)
        lbl = QLabel(intro or "Choose a passcode for admin settings like chart colours. "
                              "Anyone who knows it can change them, so share it only with "
                              "other admins.")
        lbl.setWordWrap(True)
        root.addWidget(lbl)
        form = QFormLayout()
        self.first, self.second = QLineEdit(), QLineEdit()
        for box in (self.first, self.second):
            box.setEchoMode(QLineEdit.Password)
        form.addRow("New passcode", self.first)
        form.addRow("Type it again", self.second)
        root.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def accept(self):
        if len(self.first.text()) < 4:
            QMessageBox.warning(self, "Too short", "Use at least 4 characters.")
            return
        if self.first.text() != self.second.text():
            QMessageBox.warning(self, "Doesn't match", "The two passcodes are different.")
            return
        super().accept()

    def passcode(self):
        return self.first.text()


class ColorsDialog(QDialog):
    """Pick the colour of anything the charts color: statuses, subsystems, and the
    values of category pies (reasons, workflows…). Applies to every chart, card,
    status dot and export, for everyone."""

    def __init__(self, win, data, overrides):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Chart colours")
        self.setMinimumWidth(660)
        self.overrides = dict(overrides)
        self.changed_passcode = None
        charts.register(data)
        self.entries, self.by_key = [], {}

        def add(section, key, label, tab, default):
            if key in self.by_key:
                if tab not in self.by_key[key]["tabs"]:
                    self.by_key[key]["tabs"].append(tab)
                return
            e = {"section": section, "key": key, "label": label, "tabs": [tab], "default": default}
            self.entries.append(e)
            self.by_key[key] = e

        # 1. statuses, in order of meaning
        for tab in data["tabs"]:
            col = store.derived_columns(tab).get(store.STATUS)
            if not col:
                continue
            values = list(col["options"]) + [store.row_status(tab, r) for r in _visible_rows(tab)]
            for v in charts.status_order(dict.fromkeys(values), col["options"]):
                add("Statuses", v.strip().lower(), v.strip() or "No status", tab["title"],
                    lambda v=v, o=col["options"]: charts.status_colors([v], o, use_overrides=False)[v])
        # 2. subsystems (Subsystem At Fault pie and the component tally)
        sub_tabs = [t["title"] for t in data["tabs"] if store.fault_column(t) or store.component_column(t)]
        for sub in list(charts.SUBSYSTEM_ORDER):
            for t in sub_tabs:
                add("Subsystems", f"{charts.SUBSYSTEM_KEY}:{sub.lower()}", sub, t,
                    lambda sub=sub: charts.subsystem_color(sub, use_overrides=False))
        # 3. each category pie's values (Reason for Error Runs, Workflow Type…)
        for tab in data["tabs"]:
            for c in tab["columns"]:
                if not store.wants_pie(c):
                    continue
                col = c["name"].strip().lower()
                for v in list(charts.CATEGORY_ORDER.get(col, [])):
                    add(c["name"].strip(), f"{col}:{v.lower()}", v, tab["title"],
                        lambda col=col, v=v: charts.category_color(col, v, use_overrides=False))
        self.sections = list(dict.fromkeys(e["section"] for e in self.entries))
        self.preview_section = self.sections[0] if self.sections else None

        intro = QLabel("Click a colour to change it. Your choices are saved in the sheet, so "
                       "everyone sees them after their next sync, on the dashboard and in exports. "
                       "Anything you don't change keeps its built-in colour. Green is kept for "
                       "finished statuses (Done, Completed, Results…).")
        intro.setWordWrap(True)
        self.rows = QGridLayout()
        self.rows.setHorizontalSpacing(12)
        self.rows.setVerticalSpacing(6)
        self.swatches, self.resets = {}, {}
        i = 0
        for section in self.sections:
            head = _small_label(section.upper(), "#888885", 10)
            head.setStyleSheet("color:#888885; font-size:10px; font-weight:bold; margin-top:8px;")
            self.rows.addWidget(head, i, 0, 1, 4)
            i += 1
            for e in [e for e in self.entries if e["section"] == section]:
                key = e["key"]
                sw = QPushButton()
                sw.setFixedSize(46, 26)
                sw.setCursor(Qt.PointingHandCursor)
                sw.clicked.connect(lambda _, k=key: self._pick(k))
                name = QLabel(e["label"])
                name.setStyleSheet("font-size:13px;")
                where = _small_label(", ".join(e["tabs"]), MUTED)
                where.setToolTip("Used in: " + ", ".join(e["tabs"]))
                where.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)   # shrink, keep Reset visible
                reset = QPushButton("Reset")
                reset.setObjectName("link")
                reset.setToolTip("Go back to the built-in colour")
                reset.clicked.connect(lambda _, k=key: self._reset(k))
                self.swatches[key], self.resets[key] = sw, reset
                self.rows.addWidget(sw, i, 0)
                self.rows.addWidget(name, i, 1)
                self.rows.addWidget(where, i, 2)
                self.rows.addWidget(reset, i, 3)
                i += 1
        self.rows.setColumnStretch(2, 1)
        inner = QWidget()
        inner.setLayout(self.rows)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(inner)
        scroll.setMinimumHeight(min(32 * i + 10, 400))

        self.preview = charts.DonutChart()
        preview_box = QFrame()
        preview_box.setObjectName("card")
        pl = QVBoxLayout(preview_box)
        self.preview_lbl = _small_label("PREVIEW", "#888885", 10)
        pl.addWidget(self.preview_lbl)
        pl.addWidget(self.preview)

        reset_all = QPushButton("Reset all to built-in colours")
        reset_all.clicked.connect(self._reset_all)
        passcode_btn = QPushButton("Change admin passcode…")
        passcode_btn.clicked.connect(self._change_passcode)
        extra = QHBoxLayout()
        extra.addWidget(reset_all)
        extra.addWidget(passcode_btn)
        extra.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        save = buttons.addButton("Save for everyone", QDialogButtonBox.AcceptRole)
        save.setObjectName("primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.addWidget(intro)
        if not self.entries:
            root.addWidget(_small_label("Nothing in this sheet is colored yet.", MUTED))
        root.addWidget(scroll)
        root.addWidget(preview_box)
        root.addLayout(extra)
        root.addWidget(buttons)
        self.resize(720, 760)
        self._refresh()

    def color(self, key):
        return self.overrides.get(key) or self.by_key[key]["default"]()

    def _refresh(self):
        for key, sw in self.swatches.items():
            c = self.color(key)
            sw.setStyleSheet(f"QPushButton {{ background:{c}; border:1px solid #BDBBB6; border-radius:5px; }}"
                             f"QPushButton:hover {{ border:2px solid #1A1A18; }}")
            sw.setToolTip(f"{c.upper()} — click to change")
            self.resets[key].setVisible(key in self.overrides)
        shown = [e for e in self.entries if e["section"] == self.preview_section][:10]
        self.preview_lbl.setText(f"PREVIEW · {(self.preview_section or '').upper()}")
        self.preview.set_data([(e["label"], 1, self.color(e["key"])) for e in shown], ("", ""))

    def _pick(self, key):
        e = self.by_key[key]
        self.preview_section = e["section"]
        self._refresh()
        c = QColorDialog.getColor(QColor(self.color(key)), self, f"Colour for {e['label']}")
        if c.isValid():
            self.overrides[key] = c.name().upper()
            self._refresh()

    def _reset(self, key):
        self.overrides.pop(key, None)
        self.preview_section = self.by_key[key]["section"]
        self._refresh()

    def _reset_all(self):
        self.overrides.clear()
        self._refresh()

    def _change_passcode(self):
        dlg = NewPasscodeDialog(self, "Change admin passcode", "Choose a new admin passcode. "
                                "It takes effect when you click Save for everyone.")
        if dlg.exec_() == QDialog.Accepted:
            self.changed_passcode = dlg.passcode()


class ColumnsDialog(QDialog):
    """Admin: which columns get a filter button and a dashboard chart."""

    def __init__(self, win, data, settings):
        super().__init__(win)
        self.setWindowTitle("Charts & filters")
        self.setMinimumWidth(640)
        self.settings = dict(settings)

        # every column in the sheet (by name), with its tabs and built-in defaults
        found, order = {}, []
        for tab in data["tabs"]:
            rows = _visible_rows(tab)
            derived = store.derived_columns(tab)
            cols = ([derived[store.PLATFORM]] if store.PLATFORM in derived else []) + \
                [c for c in tab["columns"] if c not in store.status_columns(tab)]
            for c in cols:
                key = c["name"].strip().lower()
                info = found.setdefault(key, {"name": c["name"].strip(), "tabs": [],
                                              "filter": False, "chart": False, "pie": False,
                                              "dropdown": False})
                if key not in order:
                    order.append(key)
                info["tabs"].append(tab["title"])
                info["dropdown"] |= c["kind"] == "dropdown" and not c.get("derived")
                for purpose in ("filter", "chart", "pie"):
                    info[purpose] |= store._default_on(tab, rows, c, purpose)
        self.found, self.order = found, order

        intro = QLabel("Choose which columns get a <b>filter</b> button on their tab, a bar "
                       "<b>chart</b> (status by that column), or their own <b>pie</b> chart "
                       "(dropdowns only, e.g. “Why?”) on the dashboard. Turn these off "
                       "for columns full of generated or one-off values, like run names or "
                       "patient names. Applies to everyone using this release sheet.")
        intro.setWordWrap(True)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(4)
        for i, text in enumerate(("Column", "Filter", "Chart", "Pie", "Used in")):
            grid.addWidget(_small_label(text.upper(), "#888885", 10), 0, i)
        self.boxes = {}
        for i, key in enumerate(order, start=1):
            info = found[key]
            grid.addWidget(QLabel(info["name"]), i, 0)
            for j, purpose in enumerate(("filter", "chart", "pie"), start=1):
                if purpose == "pie" and not info["dropdown"]:
                    continue                               # pies are for set categories
                box = QCheckBox()
                choice = self.settings.get(self._key(purpose, key))
                box.setChecked(choice == "on" if choice in ("on", "off") else info[purpose])
                box.setToolTip("Built-in default: " + ("on" if info[purpose] else "off"))
                grid.addWidget(box, i, j, Qt.AlignCenter)
                self.boxes[(purpose, key)] = box
            where = _small_label(", ".join(dict.fromkeys(info["tabs"])), MUTED)
            where.setToolTip(where.text())
            grid.addWidget(where, i, 4)
        grid.setColumnStretch(4, 1)
        inner = QWidget()
        inner.setLayout(grid)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(inner)
        scroll.setMinimumHeight(min(30 * len(order) + 40, 360))
        self.resize(720, min(30 * len(order) + 330, 760))

        note = _small_label("Status columns always have a filter and the donut. Each tab shows "
                            "up to 3 side charts, in column order. Charts only appear once a "
                            "column has at least two different values.", MUTED)
        note.setWordWrap(True)
        reset = QPushButton("Reset all to built-in defaults")
        reset.clicked.connect(self._reset)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        save = buttons.addButton("Save for everyone", QDialogButtonBox.AcceptRole)
        save.setObjectName("primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(reset)
        bottom.addStretch(1)
        bottom.addWidget(buttons)

        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.addWidget(intro)
        root.addWidget(scroll)
        root.addWidget(note)
        root.addLayout(bottom)

    @staticmethod
    def _key(purpose, key):
        return {"filter": store.FILTER_PREFIX, "chart": store.CHART_PREFIX,
                "pie": store.PIE_PREFIX}[purpose] + key

    def _reset(self):
        for (purpose, key), box in self.boxes.items():
            box.setChecked(self.found[key][purpose])

    def result_settings(self):
        """Only choices that differ from the built-in default are stored."""
        out = {k: v for k, v in self.settings.items()
               if not k.startswith((store.FILTER_PREFIX, store.CHART_PREFIX, store.PIE_PREFIX))}
        for (purpose, key), box in self.boxes.items():
            if box.isChecked() != self.found[key][purpose]:
                out[self._key(purpose, key)] = "on" if box.isChecked() else "off"
        return out


# ── table model, sorting and filtering ────────────────────────────────────────

SORT_ROLE = Qt.UserRole + 1

class TabModel(QAbstractTableModel):
    """One sheet tab. Edits call `on_edit(snapshot, col, new, old)` and show at once."""

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.tab = {"columns": [], "rows": []}
        self.colors, self.status = {}, None
        self.status_cols, self.derived = [], {}

    def set_tab(self, tab):
        self.beginResetModel()
        self.tab = tab
        self.status, self.colors = _colors_for(tab)
        self.status_cols = store.status_columns(tab)
        self.derived = store.derived_columns(tab)
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

    def column(self, key):
        """A real column by position, or a derived one ("status", "platform") by name."""
        return self.derived[key] if isinstance(key, str) else self.tab["columns"][key]

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
            if self.page.wrap:
                return _breakable(value)
            first = value.split("\n", 1)[0]
            return first + (" …" if "\n" in value else "")
        if role == Qt.TextAlignmentRole:
            return int(Qt.AlignLeft | (Qt.AlignTop if self.page.wrap else Qt.AlignVCenter))
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
        if role == Qt.DecorationRole and col in self.status_cols and value:
            return _dot(self.colors.get(value, charts.PENDING))
        if role == SORT_ROLE:
            if not value.strip():
                return (1, [])                    # blanks sort last
            if col["kind"] == "date":
                d = store.parse_date(value)
                return (0, [(0, d.toordinal(), "")] if d else _natural(value))
            if col in self.status_cols:
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
        for key, allowed in self.filters.items():
            col = m.column(key)
            if not any(p.strip() in allowed for p in store.parts(col, store.value_of(m.tab, row, col))):
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
    changed = pyqtSignal(object, object)       # (column pos or derived name, values)

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
            item = QListWidgetItem(f"{v or store.blank_label(self.col)}   ({counts.get(v, 0)})")
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
            text = f"{name}: {next(iter(sel)) or store.blank_label(self.col)}  ▾"
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

    def sizeHint(self, option, index):
        """Row height for wrapped text: at least one comfortable line, at most WRAP_MAX_LINES."""
        size = super().sizeHint(option, index)
        line = option.fontMetrics.lineSpacing()
        size.setHeight(max(34, min(size.height() + 14, line * WRAP_MAX_LINES + 14)))
        return size

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if self.page.wrap:
            option.rect.adjust(0, 7, 0, -7)       # breathing room above and below wrapped text
            option.decorationAlignment = Qt.AlignLeft | Qt.AlignTop   # status dot beside the first line

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
        self.wrap = win.cfg.get("wrap_text", True)
        self.wrap_chk = QCheckBox("Wrap text")
        self.wrap_chk.setToolTip("Show the whole text of each cell on several lines")
        self.wrap_chk.setChecked(self.wrap)
        self.wrap_chk.toggled.connect(self.win.set_wrap)
        self.count_lbl = _small_label("", MUTED)
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(self.search, 2)
        top.addLayout(self.filter_row)
        top.addWidget(self.extra_lbl)
        top.addWidget(self.clear_btn)
        top.addStretch(1)
        top.addWidget(self.wrap_chk)
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
        self._apply_wrap()
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
            follow = lambda p: p if isinstance(p, str) else moved.get(p)   # derived keys stay
            self.proxy.filters = {follow(p): v for p, v in self.proxy.filters.items()
                                  if follow(p) is not None}
            btns = {}
            for p, btn in self.filter_btns.items():
                if follow(p) is None:
                    btn.deleteLater()
                else:
                    btns[follow(p)] = btn
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
        cols = store.filter_columns(tab, rows, _settings(self.win))
        wanted = {c["pos"] for c in cols}
        for pos in list(self.filter_btns):
            if pos not in wanted:
                btn = self.filter_btns.pop(pos)
                self.proxy.set_filter(pos, None)
                btn.deleteLater()
        for c in cols:
            counts = Counter(p.strip() for r in rows for p in store.parts(c, store.value_of(tab, r, c)))
            values = list(c["options"]) + sorted(v for v in counts if v not in c["options"])
            if c["pos"] == store.STATUS:
                values = charts.status_order(values, c["options"])
            elif store.is_version(c):
                values = sorted(values, key=_natural, reverse=True)       # newest first
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

    def set_wrap(self, on):
        self.wrap = on
        self.wrap_chk.blockSignals(True)
        self.wrap_chk.setChecked(on)
        self.wrap_chk.blockSignals(False)
        self._apply_wrap()
        self.model.layoutChanged.emit()          # re-read the text with or without break points

    def _apply_wrap(self):
        self.view.setWordWrap(self.wrap)
        self.view.setTextElideMode(Qt.ElideRight)
        # wrapped rows grow to fit their text (and re-fit when a column is resized)
        self.view.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents if self.wrap else QHeaderView.Fixed)
        if not self.wrap:
            self.view.verticalHeader().setDefaultSectionSize(34)
            for r in range(self.proxy.rowCount()):
                self.view.setRowHeight(r, 34)

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
        extra = [self.model.column(p)["name"] + ": " +
                 ", ".join(sorted(v or store.blank_label(self.model.column(p)) for v in vals))
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
            menu.addAction(f"Show only “{value or store.blank_label(col)}” in {col['name']}",
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

class ExportDialog(QDialog):
    """Pick the dates and where to save; saves a PNG and a matching PDF."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Export snapshot")
        self.setMinimumWidth(560)
        saved = win.cfg.get("export") or {}

        intro = QLabel("Saves a one-page snapshot of the end-to-end runs, Error Analysis, and "
                       "bugs closed in the dates you pick: a <b>PNG</b> to paste into Slack or "
                       "email, and a matching <b>PDF</b> where run names and bug keys are "
                       "clickable links.")
        intro.setWordWrap(True)
        self.preset = QComboBox()
        self.preset.addItems([p for p in DATE_PRESETS])
        self.preset.setCurrentText(saved.get("preset", "Today"))
        self.start, self.end = QDateEdit(QDate.currentDate()), QDateEdit(QDate.currentDate())
        for ed in (self.start, self.end):
            ed.setCalendarPopup(True)
            ed.setDisplayFormat("MMM d, yyyy")
            ed.dateChanged.connect(self._update)
        self.to_lbl = QLabel("to")
        dates = QHBoxLayout()
        dates.addWidget(self.preset)
        dates.addWidget(self.start)
        dates.addWidget(self.to_lbl)
        dates.addWidget(self.end)
        dates.addStretch(1)
        self.preset.currentIndexChanged.connect(self._update)

        self.folder = saved.get("folder") or os.path.expanduser("~/Downloads")
        self.folder_lbl = QLabel()
        self.folder_lbl.setMinimumWidth(300)
        pick = QPushButton("Choose folder…")
        pick.clicked.connect(self._pick_folder)
        where = QHBoxLayout()
        where.addWidget(self.folder_lbl, 1)
        where.addWidget(pick)
        self.summary = _small_label("", MUTED)
        self.summary.setWordWrap(True)

        form = QFormLayout()
        form.setSpacing(10)
        form.addRow("Dates", dates)
        form.addRow("Save to", where)
        form.addRow("", self.summary)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.go = buttons.addButton("⬇ Export PNG + PDF", QDialogButtonBox.AcceptRole)
        self.go.setObjectName("primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.addWidget(intro)
        root.addLayout(form)
        root.addWidget(buttons)
        self._update()

    def rng(self):
        return range_for(self.preset.currentText(), _qdate(self.start), _qdate(self.end))

    def label(self):
        """e.g. "Today (Sep 30)", "Last 7 days (Sep 24 – Sep 30)", "Sep 24 – Sep 25"."""
        name, rng = self.preset.currentText(), self.rng()
        if rng is None or name in ("On a date…", "Date range…"):
            return range_text(rng)
        return f"{name} ({range_text(rng).replace(f'{rng[0]:%a} ', '')})"

    def base_name(self):
        rng = self.rng()
        when = "all dates" if rng is None else (f"{rng[0]:%Y-%m-%d}" if rng[0] == rng[1]
                                                else f"{rng[0]:%Y-%m-%d} to {rng[1]:%Y-%m-%d}")
        title = re.sub(r'[\\/:*?"<>|]', "-", self.win.data["title"])
        return f"{title} snapshot {when}"

    def paths(self):
        base = os.path.join(self.folder, self.base_name())
        return base + ".png", base + ".pdf"

    def _pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Save snapshots to", self.folder)
        if folder:
            self.folder = folder
            self._update()

    def _update(self, *_):
        name = self.preset.currentText()
        self.start.setVisible(name in ("On a date…", "Date range…"))
        self.to_lbl.setVisible(name == "Date range…")
        self.end.setVisible(name == "Date range…")
        short = self.folder.replace(os.path.expanduser("~"), "~", 1)
        self.folder_lbl.setText(self.folder_lbl.fontMetrics().elidedText(short, Qt.ElideMiddle, 320))
        self.folder_lbl.setToolTip(self.folder)
        rng, bits = self.rng(), []
        for tab in self.win.data["tabs"]:
            if store.is_run_tab(tab):
                n = len(export_report._in_range(tab, rng))
                bits.append(f"{tab['title'].replace(' End-to-End Tracker', '')}: "
                            f"{n} run{'s' if n != 1 else ''}")
            if store.is_bug_tab(tab):
                made, done = store.created_date_column(tab), store.done_date_column(tab)
                count = lambda c: sum(store.in_range(r["values"][c["pos"]], rng) for r in _visible_rows(tab)) if c else 0
                bits.append(f"bugs: {count(made)} new, {count(done)} closed")
        self.summary.setText(f"{self.label()} · " + " · ".join(bits) +
                             f"\nFiles: {self.base_name()}.png and .pdf")

    def accept(self):
        self.win.cfg["export"] = {"preset": self.preset.currentText(), "folder": self.folder}
        store.save_config(self.win.cfg)
        super().accept()


DATE_PRESETS = ["All dates", "Today", "Yesterday", "Last 7 days", "Last 30 days", "This month",
                "On a date…", "Date range…"]

def _qdate(ed):
    d = ed.date()
    return date(d.year(), d.month(), d.day())

def range_for(name, start=None, end=None):
    """(first day, last day) for a preset, or None for all dates. Presets are relative to today."""
    today = date.today()
    if name == "Today":
        return today, today
    if name == "Yesterday":
        return today - timedelta(days=1), today - timedelta(days=1)
    if name == "Last 7 days":
        return today - timedelta(days=6), today
    if name == "Last 30 days":
        return today - timedelta(days=29), today
    if name == "This month":
        return today.replace(day=1), today
    if name == "On a date…":
        return start, start
    if name == "Date range…":
        return (start, end) if start <= end else (end, start)
    return None

def range_text(rng):
    if rng is None:
        return "All dates"
    if rng[0] == rng[1]:
        return f"{rng[0]:%a %b} {rng[0].day}"
    return f"{rng[0]:%b} {rng[0].day} – {rng[1]:%b} {rng[1].day}"


class _FlowColumns(QWidget):
    """Puts its widgets side by side when there's room, otherwise one under another."""

    def __init__(self):
        super().__init__()
        self.items, self.cols = [], 0
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 4, 0, 0)
        self.grid.setHorizontalSpacing(24)
        self.grid.setVerticalSpacing(14)

    def add(self, w):
        w.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.items.append(w)
        self._place()

    def _needed(self):
        return sum(w.sizeHint().width() for w in self.items) + 24 * (len(self.items) - 1)

    def _place(self):
        cols = len(self.items) if self.width() >= self._needed() or not self.width() else 1
        if cols == self.cols and all(self.grid.indexOf(w) >= 0 for w in self.items):
            return
        self.cols = cols
        for w in self.items:
            self.grid.removeWidget(w)
        for i, w in enumerate(self.items):
            self.grid.addWidget(w, 0 if cols > 1 else i, i if cols > 1 else 0, Qt.AlignTop)
        for c, w in enumerate(self.items):          # wider content gets a wider column
            self.grid.setColumnStretch(c, w.sizeHint().width() if cols > 1 else int(c == 0))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place()


class DateBar(QFrame):
    """Dates ▾ [preset] [from] – [to] — narrows the dashboard to runs in that range."""
    PRESETS = DATE_PRESETS

    def __init__(self, win):
        super().__init__()
        self.win = win
        saved = win.cfg.get("dash_dates") or {}
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 10, 8, 0)
        lay.setSpacing(8)
        lay.addWidget(_small_label("Dates"))
        self.preset = QComboBox()
        self.preset.addItems(self.PRESETS)
        self.preset.setMinimumWidth(150)
        self.start, self.end = QDateEdit(), QDateEdit()
        for ed, key in ((self.start, "from"), (self.end, "to")):
            ed.setCalendarPopup(True)
            ed.setDisplayFormat("MMM d, yyyy")
            ed.setMinimumWidth(130)
            d = store.parse_date(saved.get(key, ""))
            ed.setDate(QDate(d.year, d.month, d.day) if d else QDate.currentDate())
            ed.dateChanged.connect(self._changed)
        self.dash_lbl = QLabel("–")
        self.note = _small_label("", MUTED)
        lay.addWidget(self.preset)
        lay.addWidget(self.start)
        lay.addWidget(self.dash_lbl)
        lay.addWidget(self.end)
        lay.addSpacing(6)
        lay.addWidget(self.note, 1)
        i = self.preset.findText(saved.get("preset", "All dates"))
        self.preset.setCurrentIndex(max(i, 0))
        self.preset.currentIndexChanged.connect(self._changed)
        self._apply(save=False)

    def _changed(self, *_):
        self._apply(save=True)
        if self.win.data:
            self.win.dashboard.set_data(self.win.data)

    def current_range(self):
        """(first day, last day), or None for all dates."""
        return range_for(self.preset.currentText(), _qdate(self.start), _qdate(self.end))

    def _apply(self, save):
        name = self.preset.currentText()
        self.start.setVisible(name in ("On a date…", "Date range…"))
        self.dash_lbl.setVisible(name == "Date range…")
        self.end.setVisible(name == "Date range…")
        rng = self.current_range()
        dash = self.win.dashboard
        dash.range = rng
        dash.range_text = range_text(rng)
        tabs = (self.win.data or {}).get("tabs", [])
        dated = [t["title"] for t in tabs if store.date_column(t)]
        bugs = [t["title"] for t in tabs if store.is_bug_tab(t)]
        if rng is None:
            self.note.setText("")
        else:
            parts = [", ".join(dated)] if dated else []
            if bugs:
                parts.append("new and closed bugs on " + ", ".join(bugs))
            self.note.setText(f"Showing {dash.range_text} for " + ("; ".join(parts) or "dated tabs")
                              + ". Other charts show everything.")
        if save:
            self.win.cfg["dash_dates"] = {"preset": name,
                                          "from": self.start.date().toString("M/d/yyyy"),
                                          "to": self.end.date().toString("M/d/yyyy")}
            store.save_config(self.win.cfg)

    def refresh(self):
        """Presets like "Today" follow the clock; update the text after each sync."""
        self._apply(save=False)


class Dashboard(QScrollArea):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.body = None
        self.range, self.range_text = None, "All dates"     # set by the DateBar

    # ── date range ────────────────────────────────────────────────────────────

    def _dated(self, tab):
        """True when the date filter applies to this tab (it has a date column)."""
        return self.range is not None and store.date_column(tab) is not None

    def _rows(self, tab):
        rows = _visible_rows(tab)
        if not self._dated(tab):
            return rows
        dcol, (start, end) = store.date_column(tab), self.range
        keep = []
        for r in rows:
            d = store.parse_date(r["values"][dcol["pos"]])
            if d and start <= d <= end:
                keep.append(r)
        return keep

    def _count_note(self, tab, rows):
        n = len(rows)
        text = f"{n} row{'s' if n != 1 else ''}"
        if self._dated(tab):
            total = len(_visible_rows(tab))
            text = f"{n} of {total} rows · {self.range_text}"
        return text

    def _go(self, title, filters=None):
        """Open a tab's table, filtered like the chart that was clicked — including the dates."""
        filters = dict(filters or {})
        tab = next((t for t in self.win.data["tabs"] if t["title"] == title), None)
        if tab and self._dated(tab):
            dcol = store.date_column(tab)
            if dcol["pos"] not in filters:
                filters[dcol["pos"]] = {r["values"][dcol["pos"]] for r in self._rows(tab)} or {"\0"}
        self.win.show_tab(title, filters or None)

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
            if _visible_rows(tab) and (store.fault_column(tab) or store.component_column(tab)):
                root.addWidget(self._analysis(tab))
        root.addStretch(1)
        self.setWidget(body)
        self.body = body
        QTimer.singleShot(0, lambda: self.verticalScrollBar().setValue(pos))

    def _tile(self, tab):
        rows = self._rows(tab)
        status, colors = _colors_for(tab, rows)
        tile = QFrame()
        tile.setObjectName("tile")
        tile.setCursor(Qt.PointingHandCursor)
        tile.setToolTip(f"Open {tab['title']}")
        tile.mousePressEvent = lambda e, t=tab["title"]: self._go(t)
        lay = QVBoxLayout(tile)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(4)
        lay.addWidget(_small_label(tab["title"], "#666663", 11))
        mini = charts.MiniBar()
        if status and rows:
            counts = Counter(store.row_status(tab, r) for r in rows)
            cls = Counter()
            for v, n in counts.items():
                cls[charts.classify(v)] += n
            done, total = cls["done"], len(rows)
            big = QLabel(f"{round(100 * done / total)}%")
            big.setStyleSheet("font-size:24px; font-weight:bold;")
            bits = [f"{done} of {total} done"]
            if cls["blocked"]:
                bits.append(f"{cls['blocked']} " + ("failed" if store.is_run_tab(tab) else "blocked"))
            if cls["active"]:
                bits.append(f"{cls['active']} in progress")
            if self._dated(tab):
                bits.append(self.range_text)
            sub = _small_label(" · ".join(bits), "#52514E", 11)
            order = charts.status_order(list(counts), status["options"])
            mini.set_data([(v or "No status", counts[v], colors.get(v, charts.PENDING)) for v in order])
        else:
            big = QLabel(str(len(rows)))
            big.setStyleSheet("font-size:24px; font-weight:bold;")
            sub = _small_label("rows" + (f" · {self.range_text}" if self._dated(tab) else ""),
                               "#52514E", 11)
        lay.addWidget(big)
        lay.addWidget(sub)
        lay.addWidget(mini)
        return tile

    def _section(self, tab):
        rows = self._rows(tab)
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
        detail = self._count_note(tab, rows) + (f" · by {status['name']}" if status else "")
        head.addWidget(_small_label(detail, MUTED), 0, Qt.AlignBottom)
        head.addStretch(1)
        open_btn = QPushButton("Open table →")
        open_btn.setObjectName("link")
        open_btn.clicked.connect(lambda: self._go(tab["title"]))
        head.addWidget(open_btn)
        lay.addLayout(head)

        if not rows:
            lay.addWidget(_small_label("No rows in the chosen dates." if self._dated(tab)
                                       else "Nothing filled in yet.", MUTED))
            return card
        if not status:
            lay.addWidget(_small_label("No status column found, so there's nothing to chart. "
                                       "Add a dropdown column named “Status” to get charts.", MUTED))
            return card

        pos = status["pos"]                       # the derived "status" key
        st = {id(r): store.row_status(tab, r) for r in rows}
        counts = Counter(st.values())
        order = charts.status_order(list(counts), status["options"])
        done = sum(n for v, n in counts.items() if charts.classify(v) == "done")

        charts_row = QHBoxLayout()
        charts_row.setSpacing(28)
        donut = charts.DonutChart()
        donut.set_data([(v, counts[v], colors.get(v, charts.PENDING)) for v in order],
                       (f"{round(100 * done / len(rows))}%", "done"))
        donut.picked.connect(lambda v, t=tab["title"]: self._go(t, {pos: {v}}))
        charts_row.addWidget(self._titled(status["name"] + " (all, as of now)" if store.is_bug_tab(tab)
                                          else status["name"], donut), 0, Qt.AlignTop)
        bug_lists = None
        if store.is_bug_tab(tab):
            summary, bug_lists = self._bug_summary(tab, rows, st)
            charts_row.addWidget(summary, 1, Qt.AlignTop)

        has_data = lambda c: any(r["values"][c["pos"]].strip() for r in rows)
        pies = [c for c in store.pie_columns(tab, rows, _settings(self.win)) if has_data(c)]
        for pc in pies:
            charts_row.addWidget(self._pie(tab, rows, st, pc), 0, Qt.AlignTop)
        bars_row = charts_row
        if pies:                                  # donuts on top, bar charts get their own row
            charts_row.addStretch(1)
            lay.addLayout(charts_row)
            bars_row = QHBoxLayout()
            bars_row.setSpacing(28)

        for g in store.chart_columns(tab, rows, _settings(self.win), limit=3):
            groups = defaultdict(Counter)
            for r in rows:
                for part in store.parts(g, store.value_of(tab, r, g)):
                    groups[part.strip()][st[id(r)]] += 1
            if len(groups) < 2 and "" in groups:
                continue                       # nobody has filled this column in yet
            if store.is_version(g):            # newest version first
                names = (sorted((k for k in groups if k), key=_natural, reverse=True)
                         + ([""] if "" in groups else []))[:12]
            else:
                names = sorted(groups, key=lambda k: (k == "", -sum(groups[k].values()), k))[:12]
            bars = charts.StackedBars()
            bars.set_data([(n, [(v, groups[n][v], colors.get(v, charts.PENDING)) for v in order])
                           for n in names], blank=store.blank_label(g))
            bars.picked.connect(lambda gv, t=tab["title"], gp=g["pos"]:
                                self._go(t, {gp: {gv[0]}, pos: {gv[1]}}))
            if g["pos"] == store.PLATFORM:
                title = "Runs on Coder vs Atlantis"
            elif store.is_version(g) and store.is_run_tab(tab):
                title = f"Runs per {g['name']}"
            else:
                title = f"{status['name']} by {g['name']}"
            bars_row.addWidget(self._titled(title, bars), 1, Qt.AlignTop)
        if bars_row is charts_row:
            charts_row.addStretch(0)
        if bars_row.count():
            lay.addLayout(bars_row)
        if bug_lists is not None:
            lay.addWidget(bug_lists)

        dcol = store.date_column(tab)
        if dcol:
            days = defaultdict(Counter)
            raw = defaultdict(set)
            for r in rows:
                d = store.parse_date(r["values"][dcol["pos"]])
                if d:
                    days[d][st[id(r)]] += 1
                    raw[d].add(r["values"][dcol["pos"]])
            if days:
                col_chart = charts.DayColumns()
                col_chart.set_data([(d, [(v, days[d][v], colors.get(v, charts.PENDING)) for v in order])
                                    for d in sorted(days)])
                col_chart.picked.connect(lambda ds, t=tab["title"], dp=dcol["pos"]:
                                         self._go(t, {dp: raw[ds[0]], pos: {ds[1]}}))
                lay.addWidget(self._titled(f"Per day ({dcol['name']})", col_chart))
        return card

    def _bug_summary(self, tab, rows, st):
        """Next to the bug donut: open / new / closed / in QA, a one-line summary,
        and short lists of the newest, recently closed, and waiting-in-QA bugs.
        "New" and "Closed" follow the dashboard's date filter, like the other charts."""
        today = date.today()
        period, period_text = self.range, self.range_text
        made, done_col = store.created_date_column(tab), store.done_date_column(tab)
        key_col = next((c for c in tab["columns"] if any(r["links"][c["pos"]] for r in rows)),
                       tab["columns"][0])
        summary_col = next((c for c in tab["columns"] if "summary" in c["name"].lower()), None)
        who_col = next((c for c in tab["columns"] if "assign" in c["name"].lower()), None)
        spos = store.STATUS
        val = lambda r, c: r["values"][c["pos"]] if c else ""
        is_done = lambda r: charts.classify(st[id(r)]) == "done" or bool(store.parse_date(val(r, done_col)))
        is_qa = lambda r: not is_done(r) and "qa" in _words_of(st[id(r)])

        open_ = [r for r in rows if not is_done(r)]
        new = [r for r in rows if made and store.in_range(val(r, made), period)]
        closed = [r for r in rows if done_col and store.in_range(val(r, done_col), period)]
        qa = [r for r in rows if is_qa(r)]
        age = lambda r: (today - store.parse_date(val(r, made))).days if made and store.parse_date(val(r, made)) else None

        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(_small_label(f"NEW AND CLOSED · {period_text.upper()}", "#888885", 10))

        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        title = tab["title"]
        for label, n, color, sub, filters in [
            ("Open now", len(open_), INK, f"{len(rows) - len(open_)} of {len(rows)} done",
             {spos: {st[id(r)] for r in open_}}),
            ("New", len(new), charts.PROGRESS, f"opened · {period_text}",
             {made["pos"]: {val(r, made) for r in new}} if made else None),
            ("Closed", len(closed), "#0B7A0B", f"done · {period_text}",
             {done_col["pos"]: {val(r, done_col) for r in closed}} if done_col else None),
            ("In QA", len(qa), charts.QA, "waiting on QA now", {spos: {st[id(r)] for r in qa}}),
        ]:
            tile = QFrame()
            tile.setObjectName("tile")
            tl = QVBoxLayout(tile)
            tl.setContentsMargins(12, 8, 12, 8)
            tl.setSpacing(0)
            tl.addWidget(_small_label(label, "#666663", 11))
            big = QLabel(str(n))
            big.setStyleSheet(f"font-size:22px; font-weight:bold; color:{color};")
            tl.addWidget(big)
            tl.addWidget(_small_label(sub, MUTED, 10))
            if filters is not None:
                tile.setCursor(Qt.PointingHandCursor)
                tile.setToolTip(f"See these {n} bugs")
                tile.mousePressEvent = lambda e, f=filters or {spos: {"\0"}}: self._go(title, f)
            tiles.addWidget(tile, 1)
        lay.addLayout(tiles)

        net = len(new) - len(closed)
        first = f"<b>{period_text}:</b> {len(new)} new, {len(closed)} closed"
        if period is not None:
            first += f" (net {'+' if net > 0 else ''}{net})"
        bits = [first, f"{len(open_)} open now", f"{len(qa)} in QA"]
        oldest = max((r for r in open_ if age(r) is not None), key=age, default=None)
        if oldest is not None:
            bits.append(f"oldest open: {self._bug_link(oldest, key_col)} ({_plural_days(age(oldest))})")
        line = QLabel(" · ".join(bits))
        line.setTextFormat(Qt.RichText)
        line.setOpenExternalLinks(True)
        line.setWordWrap(True)
        line.setStyleSheet("font-size:12px; color:#1A1A18;")
        lay.addWidget(line)

        lists = _FlowColumns()                   # full width, under the donut
        newest = sorted(new, key=lambda r: store.parse_date(val(r, made)) or today, reverse=True)
        recent = sorted(closed, key=lambda r: store.parse_date(val(r, done_col)) or today, reverse=True)
        waiting = sorted(qa, key=lambda r: store.parse_date(val(r, made)) or today)
        for heading, items, extra, color in [
            ("New", newest, lambda r: st[id(r)], charts.PROGRESS),
            ("Closed", recent, lambda r: val(r, done_col), "#0B7A0B"),
            ("In QA", waiting, lambda r: val(r, who_col), charts.QA),
        ]:
            lists.add(self._bug_list(heading, items, key_col, summary_col, extra, color))
        return box, lists

    @staticmethod
    def _bug_link(r, key_col):
        key, link = r["values"][key_col["pos"]], r["links"][key_col["pos"]]
        return f'<a href="{html.escape(link, quote=True)}" style="color:{LINK}">{html.escape(key)}</a>' \
            if link else html.escape(key)

    def _bug_list(self, heading, items, key_col, summary_col, extra, color, limit=5):
        rows_html = []
        for r in items[:limit]:
            summ = r["values"][summary_col["pos"]] if summary_col else ""
            summ = summ if len(summ) <= 36 else summ[:34].rstrip() + "…"
            rows_html.append(f'<tr><td style="padding:3px 10px 3px 0; white-space:nowrap">'
                             f'{self._bug_link(r, key_col)}</td>'
                             f'<td style="padding:3px 10px 3px 0; color:#1A1A18; white-space:nowrap">'
                             f'{html.escape(summ)}</td>'
                             f'<td style="padding:3px 0; color:#8A8984; white-space:nowrap">'
                             f'{html.escape(extra(r))}</td></tr>')
        more = f'<br><span style="color:#8A8984">and {len(items) - limit} more</span>' if len(items) > limit else ""
        body = (f'<table cellspacing="0">{"".join(rows_html)}</table>{more}' if items
                else '<span style="color:#8A8984">None</span>')
        lbl = QLabel(f'<span style="color:{color}; font-weight:bold">●</span> '
                     f'<b>{html.escape(heading)}</b> <span style="color:#8A8984">({len(items)})</span><br>{body}')
        lbl.setTextFormat(Qt.RichText)
        lbl.setOpenExternalLinks(True)
        lbl.setStyleSheet("font-size:12px;")
        lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        return lbl

    def _pie(self, tab, rows, st, col, subsystem=False):
        """A donut of categories, e.g. "Reason for Error Runs". Rows where the
        column is blank are left out. Colors are shared across the whole app
        (one per value, or per subsystem for "Subsystem At Fault")."""
        counts = Counter(p.strip() for r in rows
                         for p in store.parts(col, r["values"][col["pos"]]) if p.strip())
        base = charts.SUBSYSTEM_ORDER if subsystem else col["options"]
        options = [v for v in base if v in counts] + sorted(v for v in counts if v not in base)
        palette = {v: charts.subsystem_color(v) if subsystem else charts.category_color(col["name"], v)
                   for v in options}
        pie = charts.DonutChart()
        total = sum(counts.values())
        pie.set_data([(v, counts[v], palette[v]) for v in options if counts[v]],
                     (str(total), "run" if total == 1 else "runs"))
        pie.picked.connect(lambda v, t=tab["title"], p=col["pos"]: self._go(t, {p: {v}}))
        return self._titled(col["name"], pie)

    def _analysis(self, tab):
        """Error Analysis card: why runs failed, which subsystem, and a tally of
        every component (from the dropdown's options) grouped by subsystem."""
        rows = self._rows(tab)
        st = {id(r): store.row_status(tab, r) for r in rows}
        fault, comp = store.fault_column(tab), store.component_column(tab)


        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 14, 18, 16)
        lay.setSpacing(10)
        head = QHBoxLayout()
        t = QLabel(f"{tab['title']} — Error Analysis")
        t.setStyleSheet("font-size:15px; font-weight:bold;")
        head.addWidget(t)
        failed = sum(charts.classify(v) in ("blocked", "cancelled", "warning") for v in st.values())
        note = f"{failed} failed run{'s' if failed != 1 else ''}"
        if self._dated(tab):
            note += " · " + self.range_text
        head.addWidget(_small_label(note + " · blanks are left out", MUTED), 0, Qt.AlignBottom)
        head.addStretch(1)
        open_btn = QPushButton("Open table →")
        open_btn.setObjectName("link")
        open_btn.clicked.connect(lambda: self._go(tab["title"]))
        head.addWidget(open_btn)
        lay.addLayout(head)

        pies = QHBoxLayout()
        pies.setSpacing(28)
        has_data = lambda c: any(r["values"][c["pos"]].strip() for r in rows)
        for pc in store.pie_columns(tab, rows, _settings(self.win)):
            if not store.is_workflow(pc) and has_data(pc):
                pies.addWidget(self._pie(tab, rows, st, pc), 0, Qt.AlignTop)
        if fault and has_data(fault):
            pies.addWidget(self._pie(tab, rows, st, fault, subsystem=True), 0, Qt.AlignTop)
        fixes = store.resolutions(tab, rows)
        if not pies.count() and not (comp and has_data(comp)) and not fixes:
            lay.addWidget(_small_label("No reasons or subsystems recorded yet"
                                       + (" in the chosen dates." if self._dated(tab) else "."), MUTED))
            return card
        pies.addStretch(1)
        lay.addLayout(pies)

        if comp:
            counts = Counter(p.strip() for r in rows
                             for p in store.parts(comp, r["values"][comp["pos"]]) if p.strip())
            groups = {}
            for value in list(comp["options"]) + sorted(v for v in counts if v not in comp["options"]):
                sub, part = store.split_component(value)
                groups.setdefault(sub, []).append((part or value, counts[value], value))
            tally = charts.ComponentTally()
            tally.set_data([(sub, charts.subsystem_color(sub), parts) for sub, parts in groups.items()])
            tally.picked.connect(lambda v, t=tab["title"], p=comp["pos"]: self._go(t, {p: {v}}))
            lay.addWidget(self._titled(f"{comp['name']} tally, by subsystem", tally))
        made = charts.resolution_charts(fixes)
        if made:
            donut, days, summary = made
            res = store.resolved_column(tab)
            donut.picked.connect(lambda v, t=tab["title"], p=res["pos"], f=fixes:
                                 self._go(t, {p: {r["values"][p] for r, fixed, *_ in f
                                                  if (fixed is not None) == (v == "Resolved")} or {"\0"}}))
            row = QHBoxLayout()
            row.setSpacing(28)
            row.addWidget(self._titled("Resolved? (failed runs)", donut), 0, Qt.AlignTop)
            right = QVBoxLayout()
            right.setSpacing(6)
            right.addWidget(self._titled("Days to resolve (run date → Resolved date)", days))
            note = QLabel(summary)
            note.setStyleSheet("font-size:12px; color:#1A1A18;")
            note.setWordWrap(True)
            right.addWidget(note)
            right.addStretch(1)
            row.addLayout(right, 1)
            lay.addLayout(row)
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
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self._check_for_update)
        self.update_timer.start(3600 * 1000)          # and again every hour

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
        self.export_btn = QPushButton("⬇ Export snapshot")
        self.export_btn.setToolTip("Save a PNG + PDF snapshot of the charts for a date range")
        self.export_btn.clicked.connect(self._export)
        self.open_btn = QPushButton("Open in Google Sheets ↗")
        self.open_btn.clicked.connect(self._open_sheet)
        self.sync_btn = QPushButton("↻ Sync")
        self.sync_btn.setToolTip("Pull the latest data from the Google Sheet (⌘R)")
        self.sync_btn.clicked.connect(lambda: self._sync(quiet=False))
        settings_btn = QPushButton("⚙ Settings")
        settings_btn.clicked.connect(lambda: self._open_settings())
        for b in (self.export_btn, self.open_btn, self.sync_btn, settings_btn):
            top.addWidget(b)
        root.addLayout(top)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.dashboard = Dashboard(self)
        self.date_bar = DateBar(self)
        dash_page = QWidget()
        dash_lay = QVBoxLayout(dash_page)
        dash_lay.setContentsMargins(0, 0, 0, 0)
        dash_lay.setSpacing(0)
        dash_lay.addWidget(self.date_bar)
        dash_lay.addWidget(self.dashboard, 1)
        self.tabs.addTab(dash_page, "📊 Dashboard")
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
        version_btn = QPushButton(f"v{APP_VERSION}")
        version_btn.setObjectName("link")
        version_btn.setStyleSheet(f"color:{MUTED}; font-size:11px; margin-left:6px;")
        version_btn.setCursor(Qt.PointingHandCursor)
        version_btn.setToolTip("Check for updates")
        version_btn.clicked.connect(lambda: self._check_for_update(manual=True))
        status_row.addWidget(version_btn)
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

    def set_wrap(self, on):
        """The Wrap text switch applies to every tab and is remembered."""
        self.cfg["wrap_text"] = on
        store.save_config(self.cfg)
        for page in self.pages.values():
            page.set_wrap(on)

    def _export(self):
        if not self.data:
            QMessageBox.information(self, "Nothing to export yet", "Connect to a release sheet first.")
            return
        dlg = ExportDialog(self)
        if dlg.exec_() != QDialog.Accepted:
            return
        png, pdf = dlg.paths()
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            snap = export_report.Snapshot(self.data, dlg.rng(), dlg.label(), _settings(self))
            snap.save_png(png)
            snap.save_pdf(pdf)
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Couldn't export", f"{type(exc).__name__}: {exc}")
            return
        QApplication.restoreOverrideCursor()
        self._set_status(f"Saved {os.path.basename(png)} and .pdf")
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", png])          # show them in Finder
        else:
            _open_link(QUrl.fromLocalFile(os.path.dirname(png)).toString())

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
        charts.set_overrides(store.color_overrides(data.get("settings")))
        charts.register(data)
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
        self.date_bar.refresh()
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

    # ── admin: chart colours ──────────────────────────────────────────────────

    def _admin_unlock(self, parent, what):
        """Ask for the admin passcode (or set one the first time). Returns
        (settings, passcode hash before) or None if cancelled / wrong."""
        if not (self.data and self.can_edit and self.source):
            QMessageBox.information(parent, "Not connected",
                                    "Connect to the Google Sheet first, then try again.")
            return None
        settings = dict(self.data.get("settings") or {})
        stored = settings.get(store.PASSCODE_KEY)
        if not stored:
            dlg = NewPasscodeDialog(parent)
            if dlg.exec_() != QDialog.Accepted:
                return None
            settings[store.PASSCODE_KEY] = store.hash_passcode(dlg.passcode())
            self.admin_unlocked = self.url
        elif getattr(self, "admin_unlocked", None) != self.url:
            code, ok = QInputDialog.getText(parent, "Admin passcode",
                                            f"Enter the admin passcode to change {what}:",
                                            QLineEdit.Password)
            if not ok:
                return None
            if not store.check_passcode(code, stored):
                QMessageBox.warning(parent, "Wrong passcode",
                                    "That passcode isn't right.\n\nForgot it? See “Admin "
                                    "passcode” in the README for how to reset it.")
                return None
            self.admin_unlocked = self.url          # don't ask again until the app restarts
        return settings, stored

    def open_colors(self, parent=None):
        unlocked = self._admin_unlock(parent or self, "chart colours")
        if not unlocked:
            return
        settings, stored = unlocked
        dlg = ColorsDialog(self, self.data, store.color_overrides(settings))
        if dlg.exec_() != QDialog.Accepted:
            if settings.get(store.PASSCODE_KEY) != stored:
                self._save_settings(settings, "Admin passcode set.")   # keep a newly set passcode
            return
        settings = {k: v for k, v in settings.items() if not k.startswith(store.COLOR_PREFIX)}
        settings.update({store.COLOR_PREFIX + k: v for k, v in dlg.overrides.items()})
        if dlg.changed_passcode:
            settings[store.PASSCODE_KEY] = store.hash_passcode(dlg.changed_passcode)
        self._save_settings(settings, "Chart colours saved for everyone.")

    def open_columns(self, parent=None):
        unlocked = self._admin_unlock(parent or self, "charts and filters")
        if not unlocked:
            return
        settings, stored = unlocked
        dlg = ColumnsDialog(self, self.data, settings)
        if dlg.exec_() != QDialog.Accepted:
            if settings.get(store.PASSCODE_KEY) != stored:
                self._save_settings(settings, "Admin passcode set.")
            return
        self._save_settings(dlg.result_settings(), "Charts & filters saved for everyone.")

    def _save_settings(self, settings, msg):
        src = self.source
        def saved(_):
            self.data["settings"] = settings
            self._show_data(self.data)
            store.save_cache(self.url, self.data)
            self._set_status(msg)
        self._run(lambda: src.save_settings(settings), saved, busy_msg="Saving settings…")

    # ── updates ───────────────────────────────────────────────────────────────

    def _check_for_update(self, manual=False):
        """Look for a newer release on GitHub. Runs at launch, every hour, and when
        the version number in the bottom-right corner is clicked."""
        def latest():
            import requests          # bundles its own certificates, unlike urllib in the built app
            try:
                r = requests.get(f"https://api.github.com/repos/{REPO}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json"}, timeout=8)
                r.raise_for_status()
                return r.json()["tag_name"]
            except Exception:
                # GitHub's API allows 60 checks an hour per network; the release page
                # has no such limit, and redirects to …/releases/tag/vX.Y.Z
                r = requests.head(f"https://github.com/{REPO}/releases/latest",
                                  allow_redirects=True, timeout=8)
                r.raise_for_status()
                tag = r.url.rstrip("/").rsplit("/", 1)[-1]
                if not _version_tuple(tag):
                    raise RuntimeError("no release found")
                return tag

        task = _Task(latest)

        def got(tag):
            self._tasks.discard(task)
            if _version_tuple(tag) > _version_tuple(APP_VERSION):
                self.latest = tag
                self.update_btn.setText(f"⬆ Update to {tag}")
                self.update_btn.setToolTip("A newer version is available. Click to install it.")
                self.update_btn.show()
                if manual:
                    self._install_update()
            elif manual:
                QMessageBox.information(self, "No updates",
                                        f"You have the latest version (v{APP_VERSION}).")

        def failed(exc):
            self._tasks.discard(task)
            if manual:
                reply = QMessageBox.question(
                    self, "Couldn't check for updates",
                    f"Couldn't reach GitHub to check for updates ({type(exc).__name__}).\n\n"
                    "Open the releases page in your browser instead?",
                    QMessageBox.Yes | QMessageBox.No)
                if reply == QMessageBox.Yes:
                    _open_link(f"https://github.com/{REPO}/releases/latest")

        task.signals.done.connect(got)
        task.signals.failed.connect(failed)
        self._tasks.add(task)
        self.net_pool.start(task)

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
