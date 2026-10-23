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

import html, json, os, re, subprocess, sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QTableView, QHeaderView, QMessageBox, QFrame,
    QDialog, QFormLayout, QDialogButtonBox, QFileDialog, QShortcut, QComboBox,
    QCheckBox, QTabWidget, QScrollArea, QStyledItemDelegate, QDateEdit, QMenu,
    QToolButton, QWidgetAction, QListWidget, QListWidgetItem, QPlainTextEdit,
    QAbstractItemView, QListView, QSizePolicy, QColorDialog, QInputDialog, QTreeWidget,
    QTreeWidgetItem, QSplitter, QTableWidget, QTableWidgetItem, QTabBar, QTextBrowser, QRadioButton
)
from PyQt5.QtCore import (
    Qt, QObject, QRunnable, QThreadPool, QTimer, QUrl, QDate, pyqtSignal,
    QAbstractTableModel, QModelIndex, QSortFilterProxyModel
)
from PyQt5.QtGui import (
    QFont, QColor, QPalette, QKeySequence, QDesktopServices, QPixmap, QPainter, QIcon
)

import blocks
import changelog
import chartquery
import charts
import export_report
import export_sections
import feature_testing
import layout
import sqldata
import tracker_store as store
import ui_theme
import views

APP_TITLE   = "Test Tracker"
APP_VERSION = "2.3.7"         # bump this for each release, then push a matching tag (v2.3.7)
APP_CHANNEL = "stable"        # "stable" or "testing": testing releases are published as GitHub
                              # pre-releases, so the ⬆ Update button on stable copies never offers them
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
    QFrame#card_edit { background: #FFFFFF; border: 2px dashed #1D9E75; border-radius: 10px; }
    QFrame#tile_edit { background: #FFFFFF; border: 1px dashed #B4B2A9; border-radius: 8px; }
    QPushButton#dashed { border: 1px dashed #B4B2A9; background: #FAFAF8; color: #52514E; padding: 10px; }
    QPushButton#dashed:hover { border-color: #1D9E75; color: #0F6E56; }
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
        columns_btn = QPushButton("🔎 Filters…")
        columns_btn.setToolTip("Admin only: choose which columns get filter buttons. "
                               "Charts are chosen with ✎ Customize on each dashboard card.")
        columns_btn.clicked.connect(lambda: parent.open_columns(self))
        columns_btn.setEnabled(bool(parent.data) and parent.can_edit)
        versions_btn = QPushButton("Versions…")
        versions_btn.setToolTip("Install another version, e.g. go back to the stable build")
        versions_btn.clicked.connect(lambda: parent.open_versions(self))
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
        root.addSpacing(6)
        self.appearance = QComboBox()
        for label, key in (("Light", "light"), ("Dark", "dark"), ("Match macOS", "system")):
            self.appearance.addItem(label, key)
        self.appearance.setCurrentIndex(max(self.appearance.findData(cfg.get("appearance", "light")), 0))
        look = QHBoxLayout()
        look.addWidget(_small_label("Appearance"))
        look.addWidget(self.appearance)
        look.addWidget(_small_label("Applies when the app restarts. Exports have their own theme.", MUTED), 1)
        root.addSpacing(6)
        root.addLayout(look)
        ver_row = QHBoxLayout()
        ver_row.addWidget(_small_label(f"App version v{APP_VERSION}"
                                       + (" (testing build)" if APP_CHANNEL == "testing" else ""), MUTED))
        ver_row.addStretch(1)
        ver_row.addWidget(versions_btn)
        root.addLayout(ver_row)
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
                col = c["name"].strip().lower()
                if col not in charts.CATEGORY_ORDER or c is store.fault_column(tab):
                    continue
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
    """Admin: which columns get a filter button on their tab. (Charts are chosen per
    card with ✎ Customize; the old chart/pie choices still shape un-customized cards.)"""

    def __init__(self, win, data, settings):
        super().__init__(win)
        self.setWindowTitle("Filters")
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

        intro = QLabel("Choose which columns get a <b>filter</b> button on their tab. Turn it off "
                       "for columns full of generated or one-off values, like run names or "
                       "patient names. Applies to everyone using this release sheet.<br><br>"
                       "To choose <b>charts</b>, click <b>✎ Customize</b> on a dashboard card.")
        intro.setWordWrap(True)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(4)
        for i, text in enumerate(("Column", "Filter", "Used in")):
            grid.addWidget(_small_label(text.upper(), "#888885", 10), 0, i)
        self.boxes = {}
        for i, key in enumerate(order, start=1):
            info = found[key]
            grid.addWidget(QLabel(info["name"]), i, 0)
            for j, purpose in enumerate(("filter",), start=1):
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
            grid.addWidget(where, i, 2)
        grid.setColumnStretch(2, 1)
        inner = QWidget()
        inner.setLayout(grid)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(inner)
        scroll.setMinimumHeight(min(30 * len(order) + 40, 360))
        self.resize(720, min(30 * len(order) + 330, 760))

        note = _small_label("Status columns always have a filter.", MUTED)
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
        out = {k: v for k, v in self.settings.items() if not k.startswith(store.FILTER_PREFIX)}
        for (purpose, key), box in self.boxes.items():
            if box.isChecked() != self.found[key][purpose]:
                out[self._key(purpose, key)] = "on" if box.isChecked() else "off"
        return out


def default_spec(tab):
    """What the Add chart form starts with for a tab."""
    short = blocks.short_name(tab["title"])
    if store.is_run_tab(tab):
        reason = next((c["name"].strip() for c in tab["columns"] if store.wants_pie(c)
                       and not store.is_workflow(c)), "status")
        return {"source": "issues", "trackers": [short], "only_failed": True, "group": reason,
                "show": "pie", "dates": "follow"}
    if store.is_bug_tab(tab):
        return {"source": "bugs", "group": "status", "show": "pie", "dates": "ignore"}
    st = store.status_column(tab)
    return {"source": "tab:" + tab["title"], "group": st["name"].strip() if st else tab["columns"][0]["name"],
            "show": "pie", "dates": "follow"}

def spec_from_block(tab, b):
    """The Add chart form's settings for one of the app's own charts (pie, bars, count,
    per day), so it can be edited as a query. None for charts that aren't counts."""
    if b["type"] not in ("pie", "bars", "count", "days"):
        return None
    col = layout.find_column(tab, b.get("column"))
    run = store.is_run_tab(tab)
    status_name = "status" if run else (store.status_column(tab) or {}).get("name", "status").strip()
    name = (status_name if b.get("column") == layout.STATUS_REF else
            "platform" if b.get("column") == layout.PLATFORM_REF else (col["name"].strip() if col else None))
    if run:
        spec = {"source": "issues" if b.get("count") == "issues" else "runs",
                "trackers": [blocks.short_name(tab["title"])], "dates": "follow"}
    else:
        spec = {"source": "tab:" + tab["title"], "dates": "follow"}
    if b["type"] == "pie":
        spec.update(show="pie", group=name)
    elif b["type"] == "bars":
        spec.update(show="bars", group=name, series=status_name)
    elif b["type"] == "count":
        spec.update(show="bars", group=name)
    else:
        spec.update(show="days", series=status_name)
    return spec


class AddChartDialog(QDialog):
    """Add or change a chart: the Simple form writes the SQL for you, or write your own.
    The preview runs the query live, in the dashboard's dates."""

    DATE_CHOICES = {"bugs": [("created", "Created in the dashboard dates"),
                             ("done", "Done in the dashboard dates"), ("ignore", "All dates")],
                    None: [("follow", "Follow the dashboard dates"), ("ignore", "All dates")]}

    def __init__(self, win, tab, block=None):
        super().__init__(win)
        self.win, self.tab = win, tab
        self.setWindowTitle(("Edit chart" if block else "Add chart") + f" — {tab['title']}")
        self.db = sqldata.for_context(win.data, win.dashboard.range)
        self.block = dict(block) if block else {"id": layout.new_id(), "type": "sql", "dash": True,
                                                  "export": True, "new_row": False, "size": 1}
        spec = (block or {}).get("simple") if block else None
        if block and block.get("type") != "sql":
            spec = spec_from_block(tab, block)
        if block is None:
            spec = default_spec(tab)
        self.hand_written = spec is None
        spec = spec or default_spec(tab)
        self._loading = True

        # ── Simple ──
        self.src = QComboBox()
        for key, label in chartquery.sources(self.db):
            self.src.addItem(label, key)
        self.trackers = {}
        tr_row = QHBoxLayout()
        for t in self.win.data["tabs"]:
            if store.is_run_tab(t):
                box = QCheckBox(blocks.short_name(t["title"]))
                box.toggled.connect(self._changed)
                self.trackers[blocks.short_name(t["title"])] = box
                tr_row.addWidget(box)
        self.only_failed = QCheckBox("Only failed runs")
        self.only_failed.toggled.connect(self._changed)
        tr_row.addWidget(self.only_failed)
        tr_row.addStretch(1)
        self.tr_box = QWidget()
        self.tr_box.setLayout(tr_row)
        tr_row.setContentsMargins(0, 0, 0, 0)
        self.product = QComboBox()
        for v in ("Both", "VitalOne", "Viking"):
            self.product.addItem(v, v)
        self.group = QComboBox()
        self.series = QComboBox()
        self.dates = QComboBox()
        self.cols = QListWidget()
        self.cols.setMaximumHeight(130)
        self.filters_box = QVBoxLayout()
        self.filters_box.setSpacing(4)
        add_filter = QPushButton("+ Add filter")
        add_filter.setObjectName("link")
        add_filter.clicked.connect(lambda: self._add_filter())
        filters = QVBoxLayout()
        filters.setContentsMargins(0, 0, 0, 0)
        filters.addLayout(self.filters_box)
        filters.addWidget(add_filter, 0, Qt.AlignLeft)
        self.filters_w = QWidget()
        self.filters_w.setLayout(filters)

        self.form = QFormLayout()
        self.form.setSpacing(8)
        self.form.addRow("Count", self.src)
        self.form.addRow("From", self.tr_box)
        self.form.addRow("Product", self.product)
        self.form.addRow("Group by", self.group)
        self.form.addRow("Split by", self.series)
        self.form.addRow("Columns", self.cols)
        self.form.addRow("Only where", self.filters_w)
        self.show_blanks = QCheckBox("Show blanks, as")
        self.show_blanks.setToolTip("Count rows where this column is empty, under the name you type")
        self.blank_name = QLineEdit()
        self.blank_name.setMaximumWidth(220)
        self.show_blanks.toggled.connect(self._changed)
        self.show_blanks.toggled.connect(lambda on: self.blank_name.setEnabled(on))
        self.blank_name.textChanged.connect(self._changed)
        blanks = QHBoxLayout()
        blanks.setContentsMargins(0, 0, 0, 0)
        blanks.addWidget(self.show_blanks)
        blanks.addWidget(self.blank_name)
        blanks.addStretch(1)
        self.blanks_w = QWidget()
        self.blanks_w.setLayout(blanks)
        self.form.addRow("Blanks", self.blanks_w)
        self.form.addRow("Dates", self.dates)
        simple = QWidget()
        simple.setLayout(self.form)

        # ── SQL ──
        self.sql = QPlainTextEdit()
        mono = QFont("Menlo")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(12)
        self.sql.setFont(mono)
        self.sql.setTabChangesFocus(True)
        self.sql.textChanged.connect(self._sql_typed)
        self.hand_note = QLabel()
        self.hand_note.setWordWrap(True)
        self.hand_note.setStyleSheet(f"color:{AMBER}; font-size:11px;")
        back = QPushButton("Use the Simple form again")
        back.setObjectName("link")
        back.clicked.connect(self._back_to_simple)
        hint = _small_label("Tables: one per tab, by name (e.g. \"Hardware Support Tickets\").  Views: runs, "
                            "issues, all_issues, resolutions, bugs.  :start and :end are the dashboard's "
                            "dates.  Add GROUP_CONCAT(_tab || ':' || _row) AS _rows to make slices "
                            "clickable.", MUTED)
        hint.setWordWrap(True)
        tables = QPushButton("Tables and columns…")
        tables.setObjectName("link")
        tables.clicked.connect(self._show_schema)
        sql_l = QVBoxLayout()
        sql_l.addWidget(self.sql, 1)
        sql_note = QHBoxLayout()
        sql_note.addWidget(self.hand_note, 1)
        sql_note.addWidget(back)
        sql_l.addLayout(sql_note)
        sql_l.addWidget(hint)
        sql_l.addWidget(tables, 0, Qt.AlignLeft)
        sql_w = QWidget()
        sql_w.setLayout(sql_l)
        self.back_btn = back

        self.modes = QTabWidget()
        self.modes.addTab(simple, "Simple")
        self.modes.addTab(sql_w, "SQL")

        # ── shared ──
        self.show_btns = {}
        show_row = QHBoxLayout()
        show_row.setSpacing(4)
        for key, label in chartquery.SHOW:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setStyleSheet("QPushButton:checked { background:#E1F5EE; border-color:#1D9E75; color:#0F6E56; }")
            btn.clicked.connect(lambda _, k=key: self._set_show(k))
            self.show_btns[key] = btn
            show_row.addWidget(btn)
        show_row.addStretch(1)
        self.title = QLineEdit(self.block.get("title", ""))
        self.title.textChanged.connect(self._changed)
        self.on_dash = QCheckBox("Show on the dashboard")
        self.on_dash.setChecked(self.block.get("dash", True))
        self.on_export = QCheckBox("In export")
        self.on_export.setChecked(self.block.get("export", True))
        common = QFormLayout()
        common.setSpacing(8)
        common.addRow("Show as", show_row)
        common.addRow("Title", self.title)
        places = QHBoxLayout()
        places.addWidget(self.on_dash)
        places.addWidget(self.on_export)
        places.addStretch(1)
        common.addRow("", places)

        left = QVBoxLayout()
        left.addWidget(self.modes, 1)
        left.addLayout(common)

        # ── preview ──
        self.pv_chart = QScrollArea()
        self.pv_chart.setWidgetResizable(True)
        self.pv_chart.setFrameShape(QFrame.NoFrame)
        self.pv_chart.setMinimumHeight(240)
        self.pv_table = QTableWidget()
        self.pv_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.pv_table.verticalHeader().hide()
        self.pv_table.setMaximumHeight(190)
        self.pv_status = _small_label("", MUTED)
        right = QVBoxLayout()
        right.addWidget(_small_label(f"LIVE PREVIEW · {win.dashboard.range_text.upper()}", "#888885", 10))
        right.addWidget(self.pv_chart, 1)
        right.addWidget(self.pv_table)
        right.addWidget(self.pv_status)
        pv = QFrame()
        pv.setObjectName("card")
        pv.setLayout(right)

        body = QHBoxLayout()
        body.setSpacing(16)
        body.addLayout(left, 11)
        body.addWidget(pv, 10)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        ok = buttons.addButton("Save chart" if block else "Add chart", QDialogButtonBox.AcceptRole)
        ok.setObjectName("primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root = QVBoxLayout(self)
        root.addLayout(body, 1)
        root.addWidget(buttons)
        self.resize(1160, 720)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._preview)
        for w in (self.src, self.product, self.group, self.series, self.dates):
            w.currentIndexChanged.connect(self._changed)
        self.src.currentIndexChanged.connect(self._source_changed)
        self.cols.itemChanged.connect(self._changed)

        self._load(spec, block)
        self._loading = False
        if self.hand_written:
            self.modes.setCurrentIndex(1)
        self._changed()

    # ── the Simple form ───────────────────────────────────────────────────────

    def _load(self, spec, block):
        self.show_as = (block or {}).get("show") or spec.get("show", "pie")
        self.src.setCurrentIndex(max(self.src.findData(spec.get("source")), 0))
        self._fill_columns()
        for name, box in self.trackers.items():
            box.setChecked(name in (spec.get("trackers") or []) or not spec.get("trackers"))
        self.only_failed.setChecked(bool(spec.get("only_failed")))
        self.product.setCurrentIndex(max(self.product.findData(spec.get("product") or "Both"), 0))
        self.group.setCurrentIndex(max(self.group.findData(spec.get("group")), 0))
        self.series.setCurrentIndex(max(self.series.findData(spec.get("series")), 0))
        self.dates.setCurrentIndex(max(self.dates.findData(spec.get("dates", "follow")), 0))
        chosen = spec.get("columns") or []
        for k in range(self.cols.count()):
            it = self.cols.item(k)
            it.setCheckState(Qt.Checked if (it.data(Qt.UserRole) in chosen or (not chosen and k < 5))
                             else Qt.Unchecked)
        for f in spec.get("filters") or []:
            self._add_filter(*f)
        show_blanks, _ = chartquery.blanks_of(spec)
        self.show_blanks.setChecked(show_blanks)
        self.blank_name.setText((spec.get("blank_label") or "").strip())
        self.blank_name.setEnabled(show_blanks)
        if self.hand_written:
            self.sql.setPlainText((block or {}).get("sql", ""))
        self._set_show(self.show_as, quiet=True)

    def _fill_columns(self):
        spec = {"source": self.src.currentData()}
        cols = chartquery.columns_for(self.db, spec)
        for combo, extra in ((self.group, None), (self.series, "Nothing")):
            keep = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            if extra:
                combo.addItem(extra, None)
            for c in cols:
                combo.addItem(chartquery.friendly(c), c)
            combo.setCurrentIndex(max(combo.findData(keep), 0))
            combo.blockSignals(False)
        self.cols.blockSignals(True)
        self.cols.clear()
        for k, c in enumerate(cols):
            it = QListWidgetItem(chartquery.friendly(c))
            it.setData(Qt.UserRole, c)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if k < 5 else Qt.Unchecked)
            self.cols.addItem(it)
        self.cols.blockSignals(False)
        src = self.src.currentData() or ""
        choices = self.DATE_CHOICES["bugs" if src == "bugs" else None]
        if src.startswith("tab:") and not chartquery.date_columns(self.db, spec):
            choices = [("ignore", "All dates (this tab has no date column)")]
        keep = self.dates.currentData()
        self.dates.blockSignals(True)
        self.dates.clear()
        for v, label in choices:
            self.dates.addItem(label, v)
        self.dates.setCurrentIndex(max(self.dates.findData(keep), 0))
        self.dates.blockSignals(False)
        self._columns = cols

    def _source_changed(self, *_):
        if self._loading:
            return
        self._fill_columns()
        while self.filters_box.count():
            w = self.filters_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        self._changed()

    def _add_filter(self, col=None, op="is", value=""):
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        c = QComboBox()
        for name in getattr(self, "_columns", []):
            c.addItem(chartquery.friendly(name), name)
        c.setCurrentIndex(max(c.findData(col), 0))
        o = QComboBox()
        for k, label in chartquery.OPS:
            o.addItem(label, k)
        o.setCurrentIndex(max(o.findData(op), 0))
        v = QComboBox()
        v.setEditable(True)
        v.setMinimumWidth(140)

        def fill_values(*_):
            keep = v.currentText()
            v.blockSignals(True)
            v.clear()
            try:
                v.addItems([str(x) for x in self.db.distinct(chartquery.table_for(
                    {"source": self.src.currentData(), "dates": "ignore"}), c.currentData())])
            except Exception:
                pass
            v.setCurrentText(keep or value)
            v.blockSignals(False)
        fill_values()
        x = QPushButton("×")
        x.setFixedWidth(30)
        x.clicked.connect(lambda: (row.setParent(None), row.deleteLater(), self._changed()))
        c.currentIndexChanged.connect(fill_values)
        for w in (c, o):
            w.currentIndexChanged.connect(self._changed)
        o.currentIndexChanged.connect(lambda *_: v.setVisible(o.currentData() not in ("is empty", "is not empty")))
        v.currentTextChanged.connect(self._changed)
        for w in (c, o, v, x):
            h.addWidget(w, 1 if w is v else 0)
        v.setVisible(op not in ("is empty", "is not empty"))
        row.parts = (c, o, v)
        self.filters_box.addWidget(row)
        self._changed()

    def spec(self):
        src = self.src.currentData()
        chosen = [n for n, b in self.trackers.items() if b.isChecked()]
        return {
            "source": src,
            "trackers": chosen if len(chosen) < len(self.trackers) else [],
            "only_failed": self.only_failed.isChecked() and src in ("runs", "issues"),
            "product": self.product.currentData() if src == "bugs" else None,
            "group": self.group.currentData(),
            "series": self.series.currentData(),
            "columns": [self.cols.item(k).data(Qt.UserRole) for k in range(self.cols.count())
                        if self.cols.item(k).checkState() == Qt.Checked],
            "filters": [[w.parts[0].currentData(), w.parts[1].currentData(), w.parts[2].currentText()]
                        for w in (self.filters_box.itemAt(k).widget() for k in range(self.filters_box.count()))
                        if w is not None and hasattr(w, "parts")],
            "dates": self.dates.currentData() or "follow",
            "show": self.show_as,
            "show_blanks": self.show_blanks.isChecked(),
            "blank_label": self.blank_name.text().strip(),
        }

    def _visible_fields(self):
        src = self.src.currentData() or ""
        show = {self.tr_box: src in ("runs", "issues", "resolutions") and len(self.trackers) > 0,
                self.product: src == "bugs",
                self.group: self.show_as in ("pie", "bars"),
                self.series: self.show_as in ("bars", "days"),
                self.cols: self.show_as == "table",
                self.blanks_w: self.show_as != "table"}
        for w, on in show.items():
            w.setVisible(on)
            lbl = self.form.labelForField(w)
            if lbl:
                lbl.setVisible(on)
        self.only_failed.setVisible(src in ("runs", "issues"))

    def _set_show(self, key, quiet=False):
        self.show_as = key
        for k, b in self.show_btns.items():
            b.setChecked(k == key)
        self._visible_fields()
        if not quiet:
            self._changed()

    # ── SQL ───────────────────────────────────────────────────────────────────

    def _sql_typed(self):
        if self._loading or getattr(self, "_writing", False):
            return
        self.hand_written = True
        self._changed()

    def _back_to_simple(self):
        self.hand_written = False
        self.modes.setCurrentIndex(0)
        self._changed()

    def _show_schema(self):
        lines = []
        for name in list(self.db.views) + self.db.tables:
            cols = [c for c in self.db.columns_of(name) if c not in ("_tab", "_row")]
            lines.append(f"{sqldata.ident(name)}\n    " + ", ".join(sqldata.ident(c) if not
                         re.fullmatch(r"[a-z_]+", c) else c for c in cols))
        dlg = QDialog(self)
        dlg.setWindowTitle("Tables and columns")
        text = QPlainTextEdit("\n\n".join(lines))
        text.setReadOnly(True)
        text.setFont(self.sql.font())
        QVBoxLayout(dlg).addWidget(text)
        dlg.resize(760, 560)
        dlg.exec_()

    # ── preview ───────────────────────────────────────────────────────────────

    def _changed(self, *_):
        if self._loading:
            return
        self._visible_fields()
        spec = self.spec()
        if not self.hand_written:
            self._writing = True
            try:
                self.sql.setPlainText(chartquery.to_sql(spec, self.db))
            except Exception as exc:
                self.sql.setPlainText(f"-- couldn't write the query: {exc}")
            self._writing = False
        self.hand_note.setText("Written by hand: the Simple form won't change it." if self.hand_written else "")
        self.back_btn.setVisible(self.hand_written)
        self.title.setPlaceholderText(chartquery.default_title(spec) if not self.hand_written else "Query")
        self.blank_name.setPlaceholderText(chartquery.suggested_blank_name(
            spec.get("series") if self.show_as == "days" else spec.get("group")))
        self.timer.start(250)

    def result_block(self):
        spec = self.spec()
        b = dict(self.block)
        b.update(type="sql", sql=self.sql.toPlainText(), show=self.show_as,
                 simple=None if self.hand_written else spec,
                 title=self.title.text().strip() or self.title.placeholderText(),
                 dash=self.on_dash.isChecked(), export=self.on_export.isChecked())
        if not self.hand_written:
            b["unit"] = chartquery.unit_for(spec)
            b["blank_label"] = chartquery.blanks_of(spec)[1]
            b["color_by"] = spec.get("series") if self.show_as in ("bars", "days") and spec.get("series") \
                else spec.get("group")
        for k in ("column", "count", "colors", "which", "columns", "hide_zero"):
            b.pop(k, None)
        return b

    def _preview(self):
        b = self.result_block()
        ctx = blocks.Ctx(self.win.dashboard.range, self.win.dashboard.range_text, settings=_settings(self.win),
                         data=self.win.data)
        try:
            _, cols, rows, ms = blocks.run_query(b, ctx)
        except Exception as exc:
            self.pv_status.setText(f"✗ {exc}")
            self.pv_status.setStyleSheet(f"color:{RED}; font-size:11px;")
            self.pv_table.clear()
            self.pv_table.setRowCount(0)
            self.pv_chart.setWidget(_small_label("Fix the query to see the chart.", MUTED))
            return
        self.pv_status.setText(f"✓ {len(rows)} row{'s' if len(rows) != 1 else ''} · {ms:.0f} ms")
        self.pv_status.setStyleSheet(f"color:{GREEN}; font-size:11px;")
        vis = [i for i, c in enumerate(cols) if not c.startswith("_")]
        self.pv_table.clear()
        self.pv_table.setColumnCount(len(vis))
        self.pv_table.setHorizontalHeaderLabels([cols[i] for i in vis])
        self.pv_table.setRowCount(min(len(rows), 100))
        for r, row in enumerate(rows[:100]):
            for k, i in enumerate(vis):
                self.pv_table.setItem(r, k, QTableWidgetItem("" if row[i] is None else str(row[i])))
        self.pv_table.resizeColumnsToContents()
        try:
            parts = blocks.render(b, [self.tab], ctx) or []
        except Exception as exc:
            parts = []
            self.pv_status.setText(f"✗ {exc}")
        box = QWidget()
        h = QHBoxLayout(box)
        for p in parts:
            h.addWidget(self.win.dashboard._part(self.tab, p, lambda t: None), 1 if p.wide else 0, Qt.AlignTop)
        if not parts:
            spec = self.spec()
            hidden = not self.hand_written and not spec.get("show_blanks") and spec.get("show") != "table"
            dated = spec.get("dates") not in ("ignore",) and self.win.dashboard.range is not None
            msg = "Nothing to chart" + (f" in {self.win.dashboard.range_text}" if dated else "") + "."
            if hidden:
                msg += " Blank cells are hidden — tick “Show blanks” to count them."
            lbl = _small_label(msg, MUTED)
            lbl.setWordWrap(True)
            h.addWidget(lbl, 1)
        h.addStretch(0 if any(p.wide for p in parts) else 1)
        self.pv_chart.setWidget(box)


class CardSettingsDialog(QDialog):
    """A card's own settings: title, where it shows, merging, sections."""

    def __init__(self, win, tab, card, lo, in_view=False):
        super().__init__(win)
        self.setWindowTitle("Card settings")
        self.setMinimumWidth(520)
        self.card, self.tab = card, tab
        self.title = QLineEdit(card.get("title", ""))
        self.title.setPlaceholderText(layout.card_title(tab, dict(card, title="")))
        self.dash = QCheckBox("Show on the dashboard")
        self.dash.setChecked(card["dash"])
        self.export = QCheckBox("Include in the export")
        self.export.setChecked(card["export"])
        self.combine = QCheckBox("In the export, merge with the same card from other tabs "
                                 "(e.g. one Error Analysis for VitalOne + Viking)")
        self.combine.setChecked(card["combine"])
        self.split = QComboBox()
        self.split.addItem("Don't split", None)
        for how, label in layout.SPLITS.items():
            if layout.can_split(tab, how):
                self.split.addItem(label, how)
        self.split.setCurrentIndex(max(self.split.findData(card.get("split")), 0))
        form = QFormLayout()
        form.addRow("Title", self.title)
        form.addRow("", self.dash)
        form.addRow("", self.export)
        form.addRow("", self.combine)
        if self.split.count() > 1:
            form.addRow("Sections", self.split)
        self.action = None
        extra = QHBoxLayout()
        new = QPushButton("+ New card for this tab")
        new.clicked.connect(lambda: self._done("new"))
        extra.addWidget(new)
        if card["id"] not in ("main", "analysis") or in_view:
            delete = QPushButton("Remove this card from the view" if in_view else "Delete this card")
            delete.clicked.connect(lambda: self._done("delete"))
            extra.addWidget(delete)
        if not in_view:
            reset = QPushButton("Reset tab to built-in")
            reset.setToolTip("Go back to the app's own charts for every card of this tab")
            reset.clicked.connect(lambda: self._done("reset"))
            extra.addWidget(reset)
        else:
            self.combine.hide()
        extra.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addLayout(extra)
        root.addWidget(buttons)

    def _done(self, action):
        self.action = action
        self.accept()

    def apply(self):
        self.card.update(title=self.title.text().strip(), dash=self.dash.isChecked(),
                         export=self.export.isChecked(), combine=self.combine.isChecked(),
                         split=self.split.currentData())


def _fetch_releases():
    """[{tag, name, date, testing, zip}] newest first, from GitHub (testing builds included)."""
    import requests          # bundles its own certificates, unlike urllib in the built app
    r = requests.get(f"https://api.github.com/repos/{REPO}/releases?per_page=50",
                     headers={"Accept": "application/vnd.github+json"}, timeout=10)
    r.raise_for_status()
    out = []
    for rel in r.json():
        if rel.get("draft") or not _version_tuple(rel.get("tag_name")):
            continue
        zip_url = next((a["browser_download_url"] for a in rel.get("assets", [])
                        if a["name"].endswith("-mac.zip")), None)
        if zip_url:
            out.append({"tag": rel["tag_name"], "date": (rel.get("published_at") or "")[:10],
                        "testing": bool(rel.get("prerelease")), "zip": zip_url})
    return sorted(out, key=lambda x: _version_tuple(x["tag"]), reverse=True)


def _local_builds():
    """Builds kept on this Mac (~/Library/Application Support/TestTracker/builds/Test-Tracker-X.Y.Z.zip),
    e.g. a testing build to fall back to that was never published."""
    folder = os.path.join(store.DATA_DIR, "builds")
    out = []
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        m = re.match(r"Test-Tracker-(\d+\.\d+\.\d+)\.zip$", name)
        if m:
            out.append({"tag": "v" + m.group(1), "date": "", "testing": True, "local": True,
                        "zip": "file://" + os.path.join(folder, name).replace(" ", "%20")})
    return out


class VersionsDialog(QDialog):
    """Install any published version — to go back to the stable build, or try a testing one."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Versions")
        self.setMinimumWidth(560)
        intro = QLabel(f"You have <b>v{APP_VERSION}</b> ({'testing build' if APP_CHANNEL == 'testing' else 'stable'}). "
                       "Pick a version to install it instead — for example to go back to the stable "
                       "build if something in a testing build isn't right. Your settings and the "
                       "sheet aren't affected.")
        intro.setWordWrap(True)
        self.list = QListWidget()
        self.list.setMinimumHeight(260)
        self.list.addItem("Loading the list of versions from GitHub…")
        self.list.itemDoubleClicked.connect(lambda *_: self.accept())
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.install = buttons.addButton("Install this version", QDialogButtonBox.AcceptRole)
        self.install.setObjectName("primary")
        self.install.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        page = QPushButton("Open releases page ↗")
        page.setObjectName("link")
        page.clicked.connect(lambda: _open_link(f"https://github.com/{REPO}/releases"))
        bottom = QHBoxLayout()
        bottom.addWidget(page)
        bottom.addStretch(1)
        bottom.addWidget(buttons)
        root = QVBoxLayout(self)
        root.addWidget(intro)
        root.addWidget(self.list, 1)
        root.addLayout(bottom)
        self.releases = []
        self.task = _Task(_fetch_releases)
        self.task.signals.done.connect(self._got)
        self.task.signals.failed.connect(self._failed)
        win.net_pool.start(self.task)

    def _got(self, releases):
        releases = releases + _local_builds()
        self.releases = releases
        self.list.clear()
        newest_stable = next((r["tag"] for r in releases if not r["testing"]), None)
        for r in releases:
            bits = [r["tag"], "on this Mac" if r.get("local") else ("testing build" if r["testing"] else "stable")]
            if r["tag"] == newest_stable:
                bits[1] = "latest stable"
            if _version_tuple(r["tag"]) == _version_tuple(APP_VERSION):
                bits.append("installed")
            item = QListWidgetItem(f"{bits[0]}   ·   {' · '.join(bits[1:])}   ·   {r['date']}")
            item.setData(Qt.UserRole, r)
            self.list.addItem(item)
        if not releases:
            self.list.addItem("No published versions found.")
        self.list.currentItemChanged.connect(lambda cur, _: self.install.setEnabled(
            bool(cur and cur.data(Qt.UserRole))))

    def _failed(self, exc):
        if _local_builds():
            self._got([])
            self.list.addItem(f"Couldn't reach GitHub ({type(exc).__name__}) — showing the builds on this Mac.")
            return
        self.list.clear()
        self.list.addItem(f"Couldn't reach GitHub ({type(exc).__name__}). Try again in a minute, "
                          "or use the releases page.")

    def chosen(self):
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else None


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

    def go_to_cell(self, sheet_row, pos):
        """Select one cell (by its sheet row), clearing filters so it's visible."""
        i = next((k for k, r in enumerate(self.model.tab["rows"]) if r["r"] == sheet_row), None)
        if i is None:
            return
        self.clear_filters()
        if self.search.text():
            self.search.clear()
        idx = self.proxy.mapFromSource(self.model.index(i, pos))
        if not idx.isValid():
            return
        self.view.setCurrentIndex(idx)
        self.view.scrollTo(idx, QAbstractItemView.PositionAtCenter)
        self.view.setFocus()

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

class ArrangePanel(QWidget):
    """The export page's columns, as lists you can drag cards between. Drag the
    dividers to make a column wider or narrower. `changed` fires on every edit."""
    changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.custom = False
        self._loading = False
        help_ = _small_label("Drag cards between columns and up or down. Drag the dividers between "
                             "columns to change their width. Columns always end flush, so there are "
                             "no gaps.", MUTED)
        help_.setWordWrap(True)
        add = QPushButton("+ Column")
        add.clicked.connect(self._add_column)
        tidy = QPushButton("Remove empty columns")
        tidy.clicked.connect(self._tidy)
        auto = QPushButton("Auto-arrange")
        auto.setToolTip("Let the app arrange the cards (balanced columns, wide page)")
        auto.clicked.connect(self._auto)
        self.page_w = QComboBox()
        for w in (1400, 1800, 2200, 2600, 3000, 3600):
            self.page_w.addItem(f"{w} px wide", w)
        self.page_w.setToolTip("Page width. Wider pages fit more columns side by side.")
        self.page_w.currentIndexChanged.connect(self._edited)
        row = QHBoxLayout()
        for w in (add, tidy, auto):
            row.addWidget(w)
        row.addStretch(1)
        row.addWidget(self.page_w)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(12)
        self.splitter.setStyleSheet("QSplitter::handle { background:#CCCCC8; margin:60px 4px; "
                                    "border-radius:2px; } QSplitter::handle:hover { background:#1D9E75; }")
        self.splitter.splitterMoved.connect(self._edited)
        lay_ = QVBoxLayout(self)
        lay_.setContentsMargins(0, 6, 0, 0)
        lay_.addWidget(help_)
        lay_.addLayout(row)
        lay_.addWidget(self.splitter, 1)

    def _new_list(self):
        lst = QListWidget()
        lst.setDragDropMode(QAbstractItemView.DragDrop)
        lst.setDefaultDropAction(Qt.MoveAction)
        lst.setSelectionMode(QAbstractItemView.SingleSelection)
        lst.setWordWrap(True)
        lst.setSpacing(3)
        lst.setMinimumWidth(90)
        lst.setStyleSheet("QListWidget::item { background:#FFFFFF; border:1px solid #E0DDD8; "
                          "border-radius:6px; padding:8px; color:#1A1A18; }"
                          "QListWidget { background:#F0EFEB; }")
        lst.model().rowsInserted.connect(self._edited)
        lst.model().rowsRemoved.connect(self._edited)
        lst.model().rowsMoved.connect(self._edited)
        return lst

    def lists(self):
        return [self.splitter.widget(i) for i in range(self.splitter.count())]

    def _clear(self):
        for lst in self.lists():
            lst.setParent(None)
            lst.deleteLater()

    def load(self, arrangement, titles):
        """Show an arrangement ({"page_width", "columns": [{"share", "cards"}]})."""
        self._loading = True
        self._clear()
        for col in arrangement.get("columns", []):
            lst = self._new_list()
            for key in col.get("cards", []):
                if key in titles:
                    item = QListWidgetItem(titles[key])
                    item.setData(Qt.UserRole, key)
                    lst.addItem(item)
            self.splitter.addWidget(lst)
        shares = [max(float(c.get("share") or 1), 0.05) for c in arrangement.get("columns", [])]
        total = sum(shares) or 1
        self.splitter.setSizes([int(1000 * s / total) for s in shares])
        w = int(arrangement.get("page_width") or 1800)
        i = min(range(self.page_w.count()), key=lambda k: abs(self.page_w.itemData(k) - w))
        self.page_w.setCurrentIndex(i)
        self._loading = False

    def sync(self, titles):
        """Keep a custom arrangement in step with what's ticked: drop unticked cards,
        add newly ticked ones to the shortest column."""
        self._loading = True
        present = set()
        for lst in self.lists():
            for k in reversed(range(lst.count())):
                key = lst.item(k).data(Qt.UserRole)
                if key not in titles:
                    lst.takeItem(k)
                else:
                    lst.item(k).setText(titles[key])
                    present.add(key)
        for key, title in titles.items():
            if key not in present and self.lists():
                lst = min(self.lists(), key=lambda l: l.count())
                item = QListWidgetItem(title)
                item.setData(Qt.UserRole, key)
                lst.addItem(item)
        self._loading = False

    def arrangement(self):
        sizes = self.splitter.sizes()
        total = sum(sizes) or 1
        return {"page_width": self.page_w.currentData(),
                "columns": [{"share": round(s / total, 4),
                             "cards": [lst.item(k).data(Qt.UserRole) for k in range(lst.count())]}
                            for lst, s in zip(self.lists(), sizes)]}

    def _edited(self, *_):
        if self._loading:
            return
        self.custom = True
        self.changed.emit()

    def _add_column(self):
        self.splitter.addWidget(self._new_list())
        n = self.splitter.count()
        self.splitter.setSizes([1000 // n] * n)
        self._edited()

    def _tidy(self):
        for lst in self.lists():
            if lst.count() == 0 and self.splitter.count() > 1:
                lst.setParent(None)
                lst.deleteLater()
        self._edited()

    def _auto(self):
        self.custom = False
        self.changed.emit()


class SectionsPanel(QWidget):
    """The simple export: which products, and which sections (in order) with their options."""
    changed = pyqtSignal()

    def __init__(self, products):
        super().__init__()
        self.products = products
        self.cfg = export_sections.default_config()
        self._loading = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 6, 0, 0)
        root.setSpacing(6)
        prod = QHBoxLayout()
        lbl = QLabel("Products")
        lbl.setStyleSheet("font-weight:bold; font-size:13px;")
        prod.addWidget(lbl)
        self.prod_boxes = {}
        for name in products:
            b = QCheckBox(name)
            b.setChecked(True)
            b.toggled.connect(self._edited)
            self.prod_boxes[name] = b
            prod.addWidget(b)
        prod.addSpacing(10)
        self.side = QRadioButton("Side by Side")
        self.combined = QRadioButton("Combined")
        self.side.setChecked(True)
        self.side.toggled.connect(self._edited)
        prod.addWidget(self.side)
        prod.addWidget(self.combined)
        prod.addStretch(1)
        root.addLayout(prod)
        self.list_box = QVBoxLayout()
        self.list_box.setSpacing(0)
        holder = QWidget()
        holder.setLayout(self.list_box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(holder)
        root.addWidget(scroll, 1)
        self._build()

    def _build(self):
        while self.list_box.count():
            item = self.list_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.rows = {}
        order = self.cfg["order"]
        for i, sid in enumerate(order):
            name, opts = export_sections.SECTIONS[sid]
            box = QFrame()
            box.setObjectName("tile")
            v = QVBoxLayout(box)
            v.setContentsMargins(10, 6, 10, 8)
            v.setSpacing(4)
            head = QHBoxLayout()
            for text, step, enabled in (("↑", -1, i > 0), ("↓", 1, i < len(order) - 1)):
                b = QToolButton()
                b.setText(text)
                b.setEnabled(enabled)
                b.setToolTip("Move up" if step < 0 else "Move down")
                b.setFixedSize(26, 24)
                b.setStyleSheet("QToolButton { padding:0; font-size:12px; }")
                b.clicked.connect(lambda _, s=sid, st=step: self._move(s, st))
                head.addWidget(b)
            on = QCheckBox(name)
            on.setStyleSheet("font-weight:bold; font-size:13px; color:#1A1A18;")
            on.setChecked(bool(self.cfg["on"].get(sid)))
            on.toggled.connect(self._edited)
            head.addWidget(on, 1)
            v.addLayout(head)
            boxes = {}
            if opts:
                grid = QGridLayout()
                grid.setContentsMargins(58, 0, 0, 0)
                grid.setHorizontalSpacing(14)
                grid.setVerticalSpacing(2)
                for k, (key, label, _) in enumerate(opts):
                    c = QCheckBox(label)
                    c.setChecked(bool(self.cfg["opts"].get(sid, {}).get(key)))
                    c.toggled.connect(self._edited)
                    boxes[key] = c
                    grid.addWidget(c, k // 3, k % 3)
                grid.setColumnStretch(3, 1)
                v.addLayout(grid)
            if sid == "changelog":
                note = _small_label("The latest change log made in the 📝 Change Log tab.", MUTED)
                note.setContentsMargins(58, 0, 0, 0)
                v.addWidget(note)
            self.rows[sid] = (on, boxes)
            self.list_box.addWidget(box)
        self.list_box.addStretch(1)

    def load(self, cfg):
        self._loading = True
        self.cfg = export_sections.normalized(cfg)
        for name, b in self.prod_boxes.items():
            b.setChecked(self.cfg["products"].get(name, True))
        (self.side if self.cfg["split"] else self.combined).setChecked(True)
        self._build()
        self._loading = False

    def config(self):
        cfg = export_sections.normalized(self.cfg)
        cfg["products"] = {n: b.isChecked() for n, b in self.prod_boxes.items()}
        cfg["split"] = self.side.isChecked()
        for sid, (on, boxes) in self.rows.items():
            cfg["on"][sid] = on.isChecked()
            cfg["opts"][sid] = {k: c.isChecked() for k, c in boxes.items()}
        return cfg

    def _edited(self, *_):
        if self._loading:
            return
        self.cfg = self.config()
        self.changed.emit()

    def _move(self, sid, step):
        self.cfg = self.config()
        order = self.cfg["order"]
        i = order.index(sid)
        j = i + step
        if 0 <= j < len(order):
            order[i], order[j] = order[j], order[i]
            self._build()
            self.changed.emit()


class ExportDialog(QDialog):
    """Pick the dates, the page style, and which cards and charts go in; saves a PNG
    (or one per card) and a matching PDF. A preview updates as you change things."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Export snapshot")
        saved = win.cfg.get("export") or {}
        self.picks = dict(saved.get("picks") or {})       # only choices that differ from the layouts
        self.layouts = {t["title"]: win.layout_of(t) for t in win.data["tabs"]}

        intro = QLabel("Saves a snapshot for the dates you pick: a <b>PNG</b> for Slack or email and a "
                       "matching <b>PDF</b> where runs, bug keys and notes are clickable. Pick the sections "
                       "and products, or switch Content to Cards for any dashboard card.")
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

        self.style = QComboBox()
        for k, label in export_report.STYLES.items():
            self.style.addItem(label, k)
        self.style.setCurrentIndex(max(self.style.findData(saved.get("style", "wide")), 0))
        self.style.setToolTip("Wide: cards in columns — arrange them yourself under “Arrange the page”.\n"
                              "Slack summary: a 16:9 picture with the headline numbers and key charts,\n"
                              "plus the wide page.\n"
                              "One column: every ticked card stacked.\n"
                              "One image per card: a separate PNG for each card.")
        self.style.currentIndexChanged.connect(self._update)

        self.folder = saved.get("folder") or os.path.expanduser("~/Downloads")
        self.folder_lbl = QLabel()
        self.folder_lbl.setMinimumWidth(260)
        pick = QPushButton("Choose folder…")
        pick.clicked.connect(self._pick_folder)
        where = QHBoxLayout()
        where.addWidget(self.folder_lbl, 1)
        where.addWidget(pick)

        self.combine_row = QVBoxLayout()
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumHeight(280)
        self._fill_tree()
        self.tree.itemChanged.connect(self._tree_changed)
        defaults = QPushButton("Back to defaults")
        defaults.setToolTip("Tick exactly what each card's ✎ Customize settings put in the export")
        defaults.clicked.connect(self._defaults)
        tree_head = QHBoxLayout()
        tree_head.addWidget(_small_label("WHAT TO INCLUDE", "#888885", 10))
        tree_head.addStretch(1)
        tree_head.addWidget(defaults)

        self.summary = _small_label("", MUTED)
        self.summary.setWordWrap(True)

        self.profile_note = _small_label("", AMBER)
        self.profile = QComboBox()
        self.profile.setMinimumWidth(180)
        self.profile.setToolTip("Saved export settings: dates, layout, folder, what's ticked, and the arrangement")
        self.profile.currentIndexChanged.connect(self._apply_profile)
        self.save_profile = QPushButton("Save")
        self.save_profile.setToolTip("Save the changes to this profile")
        self.save_profile.clicked.connect(self._save_current)
        save_as = QPushButton("Save as new…")
        save_as.clicked.connect(self._save_as)
        self.delete_profile = QPushButton("Delete")
        self.delete_profile.clicked.connect(self._delete)
        prof_row = QHBoxLayout()
        prof_row.addWidget(self.profile, 1)
        prof_row.addWidget(self.profile_note)
        for b in (self.save_profile, save_as, self.delete_profile):
            prof_row.addWidget(b)

        self.view_box = QComboBox()
        self.view_box.addItem("MAIN", views.MAIN)
        for v in win.all_views():
            self.view_box.addItem(("👥 " if v["shared"] else "") + v["name"], v["id"])
        self.view_box.setCurrentIndex(max(self.view_box.findData(saved.get("view") or win.current_view_id()), 0))
        self.view_box.currentIndexChanged.connect(self._view_changed)

        form = QFormLayout()
        form.setSpacing(10)
        form.addRow("Profile", prof_row)
        self.content = QComboBox()
        self.content.addItem("Sections (simple)", "sections")
        self.content.addItem("Cards (advanced)", "cards")
        self.content.setToolTip("Sections: pick End-to-End Runs, Error Analysis, New Bugs… with a few options.\n"
                                "Cards: pick any dashboard card and chart, from MAIN or a view.")
        self.content.setCurrentIndex(max(self.content.findData(saved.get("content", "sections")), 0))
        self.content.currentIndexChanged.connect(self._content_changed)
        form.addRow("Content", self.content)
        self.theme = QComboBox()
        self.theme.addItem("Light", "light")
        self.theme.addItem("Dark", "dark")
        self.theme.setCurrentIndex(max(self.theme.findData(saved.get("theme", "light")), 0))
        self.theme.setToolTip("Light or dark page (Sections)")
        self.theme.currentIndexChanged.connect(self._update)
        form.addRow("View", self.view_box)
        self.view_row_label = form.labelForField(self.view_box)
        form.addRow("Dates", dates)
        layout_row = QHBoxLayout()
        layout_row.addWidget(self.style, 1)
        layout_row.addWidget(_small_label("Theme"))
        layout_row.addWidget(self.theme)
        form.addRow("Layout", layout_row)
        form.addRow("Save to", where)
        left = QVBoxLayout()
        left.setSpacing(10)
        left.addWidget(intro)
        left.addLayout(form)
        include = QWidget()
        il = QVBoxLayout(include)
        il.setContentsMargins(0, 6, 0, 0)
        il.addLayout(tree_head)
        il.addWidget(self.tree, 1)
        il.addLayout(self.combine_row)
        self.arrange = ArrangePanel()
        arr = saved.get("arrangement")
        if arr:
            self.arrange.load(arr, {k: k.split("|")[0] for c in arr.get("columns", []) for k in c.get("cards", [])})
            self.arrange.custom = True
        self.arrange.changed.connect(lambda: self.preview_timer.start(300))
        self.arrange.changed.connect(self._profile_state)
        self.side_tabs = QTabWidget()
        self.sections = SectionsPanel(export_sections.product_names(win.data))
        self.sections.load(saved.get("sections") or export_sections.default_config())
        self.sections.changed.connect(self._update)
        self.side_tabs.addTab(self.sections, "Sections")
        self.side_tabs.addTab(include, "What to include")
        self.side_tabs.addTab(self.arrange, "Arrange the page")
        left.addWidget(self.side_tabs, 1)
        left.addWidget(self.summary)

        self.preview = QLabel("Preparing a preview…")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet(f"color:{MUTED}; background:#FFFFFF; border:1px solid #E0DDD8; "
                                   "border-radius:8px;")
        self.preview.setMinimumSize(520, 420)
        self.preview_note = _small_label("", MUTED)
        right = QVBoxLayout()
        right.addWidget(_small_label("PREVIEW", "#888885", 10))
        right.addWidget(self.preview, 1)
        right.addWidget(self.preview_note)
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self._draw_preview)

        body = QHBoxLayout()
        body.setSpacing(18)
        body.addLayout(left, 1)
        body.addLayout(right, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.go = buttons.addButton("⬇ Export PNG + PDF", QDialogButtonBox.AcceptRole)
        self.go.setObjectName("primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.addLayout(body, 1)
        root.addWidget(buttons)
        self.resize(1180, 800)
        self._content_changed(reset=False)
        self._fill_profiles(saved.get("profile", self.DEFAULT_PROFILE))   # last used, as you left it
        self._update()

    # ── profiles ──────────────────────────────────────────────────────────────
    # A profile is a saved set of export settings (dates, layout, folder, what's
    # ticked, the page arrangement). "Default" is the built-in settings and can't be
    # changed; the others are kept on this computer.

    DEFAULT_PROFILE = "Default"

    def _profiles(self):
        return self.win.cfg.setdefault("export_profiles", {})

    def _default_settings(self):
        return {"preset": "Today", "style": "wide", "picks": {}, "arrangement": None,
                "folder": self.folder, "view": views.MAIN, "content": "sections", "theme": "light",
                "sections": export_sections.default_config()}

    def _content_changed(self, *_, reset=True):
        simple = self.content.currentData() == "sections"
        self.side_tabs.setTabEnabled(0, simple)
        self.side_tabs.setTabEnabled(1, not simple)
        if self.side_tabs.currentIndex() in (0, 1):
            self.side_tabs.setCurrentIndex(0 if simple else 1)
        self.view_box.setEnabled(not simple)
        self.theme.setEnabled(simple)
        self.view_box.setToolTip("" if not simple else "Views apply to Cards (advanced)")
        if reset and self.arrange.custom:
            self.arrange.custom = False               # a different set of cards: arrange afresh
        if reset:
            self._update()

    def current_settings(self):
        return {"preset": self.preset.currentText(), "style": self.style.currentData(),
                "content": self.content.currentData(), "sections": self.sections.config(),
                "theme": self.theme.currentData(),
                "view": self.view_box.currentData(),
                "picks": dict(self.picks), "folder": self.folder,
                "arrangement": self.arrange.arrangement() if self.arrange.custom else None}

    def _profile_settings(self, name):
        if name == self.DEFAULT_PROFILE:
            return self._default_settings()
        return self._profiles().get(name)

    def _fill_profiles(self, select):
        self.profile.blockSignals(True)
        self.profile.clear()
        self.profile.addItem(self.DEFAULT_PROFILE)
        for name in sorted(self._profiles(), key=str.lower):
            self.profile.addItem(name)
        i = self.profile.findText(select)
        self.profile.setCurrentIndex(max(i, 0))
        self.profile.blockSignals(False)
        self._profile_state()

    def _apply_profile(self, *_):
        p = self._profile_settings(self.profile.currentText())
        if not p:
            return
        self._applying = True
        self.preset.setCurrentText(p.get("preset", "Today"))
        self.view_box.blockSignals(True)
        self.view_box.setCurrentIndex(max(self.view_box.findData(p.get("view") or views.MAIN), 0))
        self.view_box.blockSignals(False)
        self.style.setCurrentIndex(max(self.style.findData(p.get("style", "wide")), 0))
        self.content.blockSignals(True)
        self.content.setCurrentIndex(max(self.content.findData(p.get("content", "cards" if p.get("picks")
                                                                         else "sections")), 0))
        self.content.blockSignals(False)
        self.sections.load(p.get("sections") or export_sections.default_config())
        self.theme.setCurrentIndex(max(self.theme.findData(p.get("theme", "light")), 0))
        self._content_changed(reset=False)
        self.folder = p.get("folder") or self.folder
        self.picks = dict(p.get("picks") or {})
        self._fill_tree()
        arr = p.get("arrangement")
        if arr:
            self.arrange.load(arr, {k: k.split("|")[0] for c in arr.get("columns", []) for k in c.get("cards", [])})
            self.arrange.custom = True
        else:
            self.arrange.custom = False
        self._applying = False
        self._update()

    def _same(self, a, b):
        keys = ("preset", "style", "picks", "arrangement", "folder", "view", "content", "sections", "theme")
        keys = [k for k in keys if k in (b or {})]   # profiles saved before an option existed don't count it
        norm = lambda s: json.dumps({k: (s or {}).get(k) for k in keys}, sort_keys=True)
        return norm(a) == norm(b)

    def _profile_state(self, *_):
        """Show whether the settings still match the chosen profile."""
        if getattr(self, "_applying", False) or not hasattr(self, "profile"):
            return
        name = self.profile.currentText()
        cur, saved = self.current_settings(), self._profile_settings(name)
        if name == self.DEFAULT_PROFILE:          # Default doesn't care which folder you use
            cur = dict(cur, folder=None)
            saved = dict(saved, folder=None)
        edited = not self._same(cur, saved)
        self.profile_note.setText("● edited" if edited else "")
        self.profile_note.setToolTip("These settings differ from the profile. Save them to the profile, "
                                     "or save as a new one." if edited else "")
        self.save_profile.setEnabled(edited and name != self.DEFAULT_PROFILE)
        self.delete_profile.setEnabled(name != self.DEFAULT_PROFILE)

    def _save_as(self):
        name, ok = QInputDialog.getText(self, "Save export profile",
                                        "Profile name (e.g. “EOD – Slack”, “Weekly bugs”):",
                                        text="" if self.profile.currentText() == self.DEFAULT_PROFILE
                                        else self.profile.currentText())
        name = (name or "").strip()
        if not ok or not name:
            return
        if name.lower() == self.DEFAULT_PROFILE.lower():
            QMessageBox.information(self, "Name taken", "“Default” is the built-in profile. Pick another name.")
            return
        if name in self._profiles() and QMessageBox.question(
                self, "Replace profile", f"Replace the “{name}” profile with these settings?",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self._profiles()[name] = self.current_settings()
        store.save_config(self.win.cfg)
        self._fill_profiles(name)

    def _save_current(self):
        name = self.profile.currentText()
        if name != self.DEFAULT_PROFILE:
            self._profiles()[name] = self.current_settings()
            store.save_config(self.win.cfg)
            self._profile_state()

    def _delete(self):
        name = self.profile.currentText()
        if name == self.DEFAULT_PROFILE:
            return
        if QMessageBox.question(self, "Delete profile", f"Delete the “{name}” export profile?",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self._profiles().pop(name, None)
            store.save_config(self.win.cfg)
            self._fill_profiles(self.DEFAULT_PROFILE)
            self._apply_profile()

    # ── what to include ───────────────────────────────────────────────────────

    def view_entries(self):
        """The chosen view's cards, or None for MAIN."""
        vid = self.view_box.currentData() if hasattr(self, "view_box") else views.MAIN
        view = next((v for v in self.win.all_views() if v["id"] == vid), None)
        return views.entries(view, self.win.data, self.win.layout_of) if view else None

    def _tree_card(self, tab, card):
        ckey = export_report.card_key(tab, card)
        item = QTreeWidgetItem([layout.card_title(tab, card)])
        item.setData(0, Qt.UserRole, (ckey, card["export"]))
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(0, Qt.Checked if self.picks.get(ckey, card["export"]) else Qt.Unchecked)
        f = item.font(0)
        f.setBold(True)
        item.setFont(0, f)
        for b in card["blocks"]:
            bkey = export_report.block_key(tab, card, b)
            child = QTreeWidgetItem([b.get("title") or layout.auto_title(tab, b)])
            child.setToolTip(0, layout.describe(tab, b))
            child.setData(0, Qt.UserRole, (bkey, b["export"]))
            child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
            child.setCheckState(0, Qt.Checked if self.picks.get(bkey, b["export"]) else Qt.Unchecked)
            item.addChild(child)
        self.tree.addTopLevelItem(item)
        item.setExpanded(item.checkState(0) == Qt.Checked)
        return item

    def _view_changed(self, *_):
        self._fill_tree()
        if self.arrange.custom:
            self.arrange.custom = False               # a different set of cards: arrange afresh
        self._update()

    def _fill_tree(self):
        self.tree.blockSignals(True)
        self.tree.clear()
        merged, groups = {}, {}
        entries = self.view_entries()
        if entries is not None:
            for tab, card, lo, e in entries:
                self._tree_card(tab, card)
            self.tree.blockSignals(False)
            while self.combine_row.count():
                w = self.combine_row.takeAt(0).widget()
                if w:
                    w.deleteLater()
            return
        for tab in self.win.data["tabs"]:
            if not tab["rows"]:
                continue
            for card in self.layouts[tab["title"]]["cards"]:
                combine = export_report.combines(card, self.picks)
                if card["combine"]:
                    groups.setdefault(card["id"], []).append((tab, card))
                if combine and card["id"] in merged:
                    item, tabs = merged[card["id"]]
                    tabs.append(tab)
                    item.setText(0, export_report.combined_title(card, tabs))
                    continue
                item = self._tree_card(tab, card)
                if combine:
                    merged[card["id"]] = (item, [tab])
        self.tree.blockSignals(False)
        # one checkbox per kind of card that can be merged across tabs (e.g. Error Analysis)
        while self.combine_row.count():
            w = self.combine_row.takeAt(0).widget()
            if w:
                w.deleteLater()
        for cid, members in groups.items():
            if len(members) < 2:
                continue
            tabs = [t for t, _ in members]
            card = members[0][1]
            names = " + ".join(blocks.short_name(t["title"]) for t in tabs)
            what = "Error Analysis" if cid == "analysis" else layout.card_title(tabs[0], card)
            box = QCheckBox(f"One combined {what} ({names})")
            box.setToolTip(f"Untick to export a separate {what} card for each of {names}")
            box.setChecked(export_report.combines(card, self.picks))
            box.toggled.connect(lambda on, c=card: self._set_combine(c, on))
            self.combine_row.addWidget(box)

    def _set_combine(self, card, on):
        key = export_report.COMBINE_PREFIX + card["id"]
        if on == card["combine"]:
            self.picks.pop(key, None)
        else:
            self.picks[key] = on
        QTimer.singleShot(0, self._fill_tree)       # not while the checkbox is still signalling
        self._update()

    def _tree_changed(self, item, _col):
        key, default = item.data(0, Qt.UserRole)
        on = item.checkState(0) == Qt.Checked
        if on == default:
            self.picks.pop(key, None)
        else:
            self.picks[key] = on
        if item.parent() is None and on:
            item.setExpanded(True)
        self._update()

    def _defaults(self):
        self.picks = {}
        self._fill_tree()
        self._update()

    # ── dates, files ──────────────────────────────────────────────────────────

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

    def snapshot(self):
        arrangement = self.arrange.arrangement() if self.arrange.custom else None
        simple = self.content.currentData() == "sections"
        return export_report.Snapshot(self.win.data, self.rng(), self.label(), _settings(self.win),
                                      layouts=self.layouts, picks=self.picks, style=self.style.currentData(),
                                      arrangement=arrangement, entries=None if simple else self.view_entries(),
                                      sections=self.sections.config() if simple else None,
                                      theme=self.theme.currentData())

    def _update(self, *_):
        name = self.preset.currentText()
        self.start.setVisible(name in ("On a date…", "Date range…"))
        self.to_lbl.setVisible(name == "Date range…")
        self.end.setVisible(name == "Date range…")
        short = self.folder.replace(os.path.expanduser("~"), "~", 1)
        self.folder_lbl.setText(self.folder_lbl.fontMetrics().elidedText(short, Qt.ElideMiddle, 300))
        self.folder_lbl.setToolTip(self.folder)
        rng, bits = self.rng(), []
        for tab in self.win.data["tabs"]:
            if store.is_run_tab(tab):
                n = len(export_report._in_range(tab, rng))
                bits.append(f"{blocks.short_name(tab['title'])}: {n} run{'s' if n != 1 else ''}")
            if store.is_bug_tab(tab):
                made, done = store.created_date_column(tab), store.done_date_column(tab)
                count = lambda c: sum(store.in_range(r["values"][c["pos"]], rng) for r in _visible_rows(tab)) if c else 0
                bits.append(f"bugs: {count(made)} new, {count(done)} closed")
        files = {"cards": f"{self.base_name()} - 1 ….png (one per card) and .pdf",
                 "slack": f"{self.base_name()}.png (summary), “… - full report.png” (wide page) and .pdf"
                 }.get(self.style.currentData(), f"{self.base_name()}.png and .pdf")
        self.summary.setText(f"{self.label()} · " + " · ".join(bits) + f"\nFiles: {files}")
        self.preview_timer.start(350)
        self._profile_state()

    def _draw_preview(self):
        try:
            snap = self.snapshot()
            img = snap.summary_image() if self.style.currentData() == "slack" else snap.image()
        except Exception as exc:
            self.preview.setPixmap(QPixmap())
            self.preview.setText(f"Couldn't draw the preview:\n{type(exc).__name__}: {exc}")
            return
        titles = snap.card_titles()
        if self.style.currentData() in ("wide", "slack"):
            if self.arrange.custom:
                self.arrange.sync(titles)
            elif snap.arrangement:
                self.arrange.load(snap.arrangement, titles)
        self.side_tabs.setTabEnabled(2, self.style.currentData() in ("wide", "slack"))
        if not snap.cards:
            self.preview.setPixmap(QPixmap())
            self.preview.setText("Nothing ticked to export.")
            self.preview_note.setText("")
            return
        pm = QPixmap.fromImage(img)
        box = self.preview.size()
        pm.setDevicePixelRatio(1)
        self.preview.setPixmap(pm.scaled(box.width() - 12, box.height() - 12,
                                         Qt.KeepAspectRatio, Qt.SmoothTransformation))
        n = len(snap.cards)
        note = {"slack": "Slack summary (16:9). The wide page is saved next to it, and as the PDF.",
                "wide": "Your arrangement." if self.arrange.custom else "Arranged automatically — "
                        "change it under “Arrange the page”.",
                "cards": "Each card is saved as its own PNG."}.get(self.style.currentData(), "")
        self.preview_note.setText(f"{n} card{'s' if n != 1 else ''} · {note}")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "preview_timer"):
            self.preview_timer.start(250)

    def accept(self):
        self.win.cfg["export"] = dict(self.current_settings(), profile=self.profile.currentText())
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
            if hasattr(self.win, "testing_page"):
                self.win.testing_page.refresh()

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
        testing = getattr(self.win, "testing_page", None)
        if rng is not None and testing is not None and self.parent() is testing:
            self.note.setText(f"Showing test sessions dated {dash.range_text}. The dashboard uses the same dates.")
        elif rng is None:
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


class EditTile(QFrame):
    """A chart while its card is being edited: drag ⠿ onto another chart to move it,
    drag the right edge to make it wider or narrower, ⋯ for everything else."""
    MIME = "application/x-test-tracker-block"

    def __init__(self, dash, tab, card, b, parts, target):
        super().__init__()
        self.dash, self.win, self.tab, self.card, self.b = dash, dash.win, tab, card, b
        self.setObjectName("tile_edit")
        self.setAcceptDrops(True)
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 6, 14, 8)
        v.setSpacing(6)
        top = QHBoxLayout()
        grip = QLabel("⠿")
        grip.setStyleSheet("font-size:16px; color:#8A8984; padding:0 4px;")
        grip.setCursor(Qt.OpenHandCursor)
        grip.setToolTip("Drag onto another chart to move this one there")
        grip.mousePressEvent = lambda e: setattr(self, "_press", e.pos())
        grip.mouseMoveEvent = self._drag
        top.addWidget(grip)
        name = QLabel(b.get("title") or layout.auto_title(tab, b))
        name.setStyleSheet("font-size:12px; font-weight:bold; color:#52514E;")
        top.addWidget(name, 1)
        more = QToolButton()
        more.setText("⋯")
        more.setPopupMode(QToolButton.InstantPopup)
        more.setMenu(self._menu(more))
        more.setStyleSheet("QToolButton { padding:0 8px; font-size:15px; }")
        top.addWidget(more)
        v.addLayout(top)
        body = QHBoxLayout()
        body.setSpacing(20)
        for k, p in enumerate(parts):
            if k == 0 and p.title.strip().lower() == name.text().strip().lower():
                p = blocks.Part("", p.content, p.pick, p.wide, p.full)    # the tile already names it
            body.addWidget(dash._part(tab, p, target), 1 if p.wide else 0, Qt.AlignTop)
        if not parts:
            body.addWidget(_small_label("Nothing to show in these dates.", MUTED))
        if not any(p.wide for p in parts):
            body.addStretch(1)
        v.addLayout(body)
        where = {(True, True): "Dashboard and export", (True, False): "Dashboard only",
                 (False, True): "Export only", (False, False): "Hidden"}[(b["dash"], b["export"])]
        foot = _small_label(f"{where} · size {int(b.get('size') or 1)}"
                            + (" · starts a new row" if b.get("new_row") else ""), MUTED, 10)
        v.addWidget(foot)
        if not b["dash"]:
            self.setStyleSheet("QFrame#tile_edit { background:#F3F2EF; }")
        self.handle = QFrame(self)
        self.handle.setCursor(Qt.SizeHorCursor)
        self.handle.setToolTip("Drag to make this chart wider or narrower")
        self.handle.setStyleSheet("background:#CCCCC8; border-radius:2px;")
        self.handle.mousePressEvent = self._resize_start
        self.handle.mouseReleaseEvent = self._resize_end

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.handle.setGeometry(self.width() - 7, self.height() // 2 - 18, 5, 36)

    # ── menu ──────────────────────────────────────────────────────────────────

    def _menu(self, parent):
        b, win, tab, card = self.b, self.win, self.tab, self.card
        m = QMenu(parent)
        m.addAction("Edit…", lambda: win.edit_block(tab, card, b["id"]))
        m.addAction("Duplicate", lambda: win.duplicate_block(tab, card, b["id"]))
        m.addSeparator()
        for label, key in (("Start a new row", "new_row"), ("Show on the dashboard", "dash"),
                           ("Include in the export", "export")):
            act = m.addAction(label)
            act.setCheckable(True)
            act.setChecked(bool(b.get(key)))
            act.toggled.connect(lambda on, k=key: win.set_block(tab, card, b["id"], **{k: on}))
        size = m.addMenu("Size")
        for n in (1, 2, 3, 4):
            act = size.addAction({1: "Small", 2: "Medium", 3: "Large", 4: "Extra large"}[n])
            act.setCheckable(True)
            act.setChecked(int(b.get("size") or 1) == n)
            act.triggered.connect(lambda _, n=n: win.set_block(tab, card, b["id"], size=n))
        m.addSeparator()
        m.addAction("Remove", lambda: win.remove_block(tab, card, b["id"]))
        return m

    # ── drag to move ──────────────────────────────────────────────────────────

    def _drag(self, e):
        if not (e.buttons() & Qt.LeftButton) or getattr(self, "_press", None) is None:
            return
        if (e.pos() - self._press).manhattanLength() < 6:
            return
        from PyQt5.QtCore import QMimeData
        from PyQt5.QtGui import QDrag
        mime = QMimeData()
        mime.setData(self.MIME, self.b["id"].encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(self.grab().scaledToWidth(min(self.width(), 260), Qt.SmoothTransformation))
        self._press = None
        drag.exec_(Qt.MoveAction)

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(self.MIME) and bytes(e.mimeData().data(self.MIME)).decode() != self.b["id"]:
            e.acceptProposedAction()
            self.setStyleSheet("QFrame#tile_edit { border:2px solid #1D9E75; }")

    def dragLeaveEvent(self, e):
        self.setStyleSheet("" if self.b["dash"] else "QFrame#tile_edit { background:#F3F2EF; }")

    def dropEvent(self, e):
        src = bytes(e.mimeData().data(self.MIME)).decode()
        after = e.pos().x() > self.width() / 2
        e.acceptProposedAction()
        QTimer.singleShot(0, lambda: self.win.move_block(self.tab, self.card, src, self.b["id"], after))

    # ── drag to resize ────────────────────────────────────────────────────────

    def _resize_start(self, e):
        self._start_x, self._start_w = e.globalPos().x(), self.width()

    def _resize_end(self, e):
        if not hasattr(self, "_start_x"):
            return
        w = max(self._start_w + e.globalPos().x() - self._start_x, 60)
        size = int(self.b.get("size") or 1)
        new = max(1, min(4, round(size * w / max(self._start_w, 1))))
        if new == size and abs(w - self._start_w) > 80:
            new = max(1, min(4, size + (1 if w > self._start_w else -1)))
        if new != size:
            QTimer.singleShot(0, lambda: self.win.set_block(self.tab, self.card, self.b["id"], size=new))


class ViewBar(QFrame):
    """MAIN, the shared views, then your own — above the date filter."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 8, 0, 0)
        h.setSpacing(8)
        h.addWidget(_small_label("View"))
        self.bar = QTabBar()
        self.bar.setDrawBase(False)
        self.bar.setExpanding(False)
        self.bar.setStyleSheet(
            "QTabBar::tab { background:#FFFFFF; border:1px solid #CCCCC8; border-radius:12px; padding:3px 12px;"
            " margin-right:6px; color:#52514E; font-size:12px; }"
            "QTabBar::tab:selected { background:#E1F5EE; border-color:#1D9E75; color:#0F6E56; font-weight:bold; }")
        self.bar.currentChanged.connect(self._picked)
        h.addWidget(self.bar)
        add = QPushButton("+ New view")
        add.setObjectName("link")
        add.clicked.connect(self.win.new_view)
        h.addWidget(add)
        h.addStretch(1)
        self.settings_btn = QPushButton("View settings…")
        self.settings_btn.setToolTip("Rename, add or remove cards, share with everyone, duplicate, delete")
        self.settings_btn.clicked.connect(lambda: self.win.view_settings())
        h.addWidget(self.settings_btn)
        self._ids = []

    def refresh(self):
        self.bar.blockSignals(True)
        while self.bar.count():
            self.bar.removeTab(0)
        self._ids = [views.MAIN]
        self.bar.addTab("MAIN")
        self.bar.setTabToolTip(0, "Every tab's cards, as the admin set them up")
        for v in self.win.all_views():
            i = self.bar.addTab(("👥 " if v["shared"] else "") + v["name"])
            self.bar.setTabToolTip(i, "Shared with everyone by the admin" if v["shared"] else "Only on this computer")
            self._ids.append(v["id"])
        current = self.win.current_view_id()
        self.bar.setCurrentIndex(self._ids.index(current) if current in self._ids else 0)
        self.bar.blockSignals(False)
        self.settings_btn.setVisible(current != views.MAIN)

    def _picked(self, i):
        if 0 <= i < len(self._ids):
            self.win.set_view(self._ids[i])


class ViewSettingsDialog(QDialog):
    """A view's name and cards, and sharing it with everyone (admin)."""

    def __init__(self, win, view, is_admin_view):
        super().__init__(win)
        self.win, self.view = win, views.copy_view(view)
        self.action = None
        self.setWindowTitle(f"View settings — {view['name']}")
        self.setMinimumWidth(620)
        self.name = QLineEdit(view["name"])
        self.list = QListWidget()
        self.list.setMinimumHeight(240)
        add = QToolButton()
        add.setText("+ Add card ▾")
        add.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(add)
        linked = menu.addMenu("From Main (stays in sync with it)")
        copied = menu.addMenu("Copy of a Main card (to change here)")
        for label, ref in views.main_cards(win.data, win.layout_of):
            linked.addAction(label, lambda r=ref: self._add({"ref": r}))
            copied.addAction(label, lambda r=ref: self._add_copy(r))
        blank = menu.addMenu("New empty card for")
        for t in win.data["tabs"]:
            blank.addAction(t["title"], lambda t=t: self._add({"tab": t["title"], "card": {
                "id": layout.new_id(), "title": "New card", "dash": True, "export": True,
                "combine": False, "split": None, "blocks": []}}))
        add.setMenu(menu)
        up, down, remove = QPushButton("↑"), QPushButton("↓"), QPushButton("Remove")
        up.clicked.connect(lambda: self._move(-1))
        down.clicked.connect(lambda: self._move(1))
        remove.clicked.connect(self._remove)
        row = QHBoxLayout()
        for w in (add, up, down, remove):
            row.addWidget(w)
        row.addStretch(1)

        manage = QHBoxLayout()
        if view["shared"]:
            b = QPushButton("Stop sharing")
            b.setToolTip("Take it back to just your computer (admin)")
            b.clicked.connect(lambda: self._done("unshare"))
        else:
            b = QPushButton("👥 Share with everyone…")
            b.setToolTip("Admin passcode: everyone using this release sheet gets this view")
            b.clicked.connect(lambda: self._done("share"))
        manage.addWidget(b)
        dup = QPushButton("Duplicate")
        dup.clicked.connect(lambda: self._done("duplicate"))
        delete = QPushButton("Delete view")
        delete.clicked.connect(lambda: self._done("delete"))
        manage.addWidget(dup)
        manage.addWidget(delete)
        manage.addStretch(1)

        note = _small_label("Cards from Main stay in sync with it. Cards of this view's own can be "
                            "changed with ✎ Edit on the card." + (" This view is shared: changes "
                            "apply to everyone." if view["shared"] else " This view is only on this computer."),
                            MUTED)
        note.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        ok = buttons.addButton("Save", QDialogButtonBox.AcceptRole)
        ok.setObjectName("primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form = QFormLayout()
        form.addRow("Name", self.name)
        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(_small_label("CARDS (TOP TO BOTTOM)", "#888885", 10))
        root.addWidget(self.list, 1)
        root.addLayout(row)
        root.addWidget(note)
        root.addLayout(manage)
        root.addWidget(buttons)
        self._fill()

    def _label(self, e):
        if "ref" in e:
            title, _, cid = e["ref"].rpartition("|")
            tab = self.win._tab(title)
            card = next((c for c in self.win.layout_of(tab)["cards"] if c["id"] == cid), None) if tab else None
            return f"{layout.card_title(tab, card) if card else e['ref']}   ·  from Main"
        tab = self.win._tab(e["tab"])
        return f"{layout.card_title(tab, e['card']) if tab else e['card'].get('title')}   ·  " \
               f"{len(e['card']['blocks'])} chart{'s' if len(e['card']['blocks']) != 1 else ''}, this view's own"

    def _fill(self, select=None):
        self.list.clear()
        for e in self.view["cards"]:
            self.list.addItem(self._label(e))
        if select is not None and self.list.count():
            self.list.setCurrentRow(max(0, min(select, self.list.count() - 1)))

    def _add(self, entry):
        self.view["cards"].append(entry)
        self._fill(len(self.view["cards"]) - 1)

    def _add_copy(self, ref):
        title, _, cid = ref.rpartition("|")
        tab = self.win._tab(title)
        card = next((c for c in self.win.layout_of(tab)["cards"] if c["id"] == cid), None)
        if card:
            own = layout.copy_layout(card)
            own.update(id=layout.new_id(), title=layout.card_title(tab, card), combine=False)
            self._add({"tab": title, "card": own})

    def _move(self, step):
        i, j = self.list.currentRow(), self.list.currentRow() + step
        cards = self.view["cards"]
        if 0 <= i < len(cards) and 0 <= j < len(cards):
            cards[i], cards[j] = cards[j], cards[i]
            self._fill(j)

    def _remove(self):
        i = self.list.currentRow()
        if 0 <= i < len(self.view["cards"]):
            del self.view["cards"][i]
            self._fill(i)

    def _done(self, action):
        self.action = action
        self.accept()

    def result_view(self):
        self.view["name"] = self.name.text().strip() or self.view["name"]
        return self.view


class TokensDialog(QDialog):
    """GitHub and JIRA tokens for making change logs. Kept in this Mac's Keychain."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Change log tokens")
        self.setMinimumWidth(620)
        creds = changelog.credentials()
        intro = QLabel(
            "To make change logs, the app reads <b>VitalBio/device-sw</b>'s history using your existing "
            "<b>SSH access to GitHub</b> (no GitHub token needed), and the <b>PROD</b> tickets in JIRA using "
            "a JIRA API token. It's kept in this Mac's Keychain, never in the sheet. Coworkers don't need "
            "any of this: the change logs you make are saved to the sheet for everyone.")
        intro.setWordWrap(True)
        self.gh = QLineEdit(creds["github"])
        self.gh.setEchoMode(QLineEdit.Password)
        self.gh.setPlaceholderText("github_pat_…")
        self.email = QLineEdit(creds["jira_email"])
        self.email.setPlaceholderText("you@vitalbio.com")
        self.jira = QLineEdit(creds["jira"])
        self.jira.setEchoMode(QLineEdit.Password)
        self.gh.setPlaceholderText("optional — leave empty to use your SSH access")
        gh_help = QLabel('Optional. Leave it empty: the app uses git over your SSH key. A <a href="https://github.com/'
                         'settings/personal-access-tokens/new">GitHub token</a> (fine-grained, VitalBio/device-sw, '
                         'Contents + Pull requests read-only) also lets it read PR descriptions.')
        jira_help = QLabel('<a href="https://id.atlassian.com/manage-profile/security/api-tokens">Create a '
                           'JIRA API token</a> — signed in as the email above.')
        for lbl in (gh_help, jira_help):
            lbl.setWordWrap(True)
            lbl.setOpenExternalLinks(True)
            lbl.setStyleSheet(f"color:{MUTED}; font-size:11px;")
        self.result = _small_label("", MUTED)
        self.result.setWordWrap(True)
        test = QPushButton("Test")
        test.clicked.connect(self._test)
        form = QFormLayout()
        form.addRow("GitHub token", self.gh)
        form.addRow("", gh_help)
        form.addRow("JIRA email", self.email)
        form.addRow("JIRA token", self.jira)
        form.addRow("", jira_help)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(test)
        bottom.addWidget(self.result, 1)
        bottom.addWidget(buttons)
        root = QVBoxLayout(self)
        root.addWidget(intro)
        root.addLayout(form)
        root.addLayout(bottom)

    def _test(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        bits = []
        try:
            if self.gh.text().strip():
                bits.append("GitHub ✓ " + changelog.test_github(self.gh.text().strip()))
            else:
                bits.append(f"GitHub over SSH ✓ ({changelog.REPO} at {changelog.test_git()})")
        except Exception as exc:
            bits.append(f"GitHub ✗ {exc}")
        try:
            bits.append("JIRA ✓ " + changelog.test_jira(self.email.text().strip(), self.jira.text().strip()))
        except Exception as exc:
            bits.append(f"JIRA ✗ {exc}")
        QApplication.restoreOverrideCursor()
        self.result.setText(" · ".join(bits))

    def accept(self):
        try:
            changelog.set_secret("github-token", self.gh.text().strip())
            changelog.set_secret("jira-email", self.email.text().strip())
            changelog.set_secret("jira-token", self.jira.text().strip())
        except Exception as exc:
            QMessageBox.warning(self, "Couldn't save", str(exc))
            return
        super().accept()


class ChangeLogPage(QWidget):
    """What changed between two software versions: PRs and the PROD tickets they touched."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.logs, self.log = [], None
        self.filter_cat = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 10, 0, 0)
        root.setSpacing(10)

        top = QHBoxLayout()
        top.addWidget(_small_label("Change log"))
        self.pick = QComboBox()
        self.pick.setMinimumWidth(460)
        self.pick.currentIndexChanged.connect(self._picked)
        top.addWidget(self.pick)
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setObjectName("link")
        self.delete_btn.clicked.connect(self._delete)
        top.addWidget(self.delete_btn)
        top.addStretch(1)
        self.from_v, self.to_v = QComboBox(), QComboBox()
        for box in (self.from_v, self.to_v):
            box.setEditable(True)
            box.setMinimumWidth(200)
            box.lineEdit().setPlaceholderText("version or git hash")
        self.make = QPushButton("Make change log")
        self.make.setObjectName("primary")
        self.make.clicked.connect(self._generate)
        tokens = QPushButton("Tokens…")
        tokens.setObjectName("link")
        tokens.clicked.connect(self._tokens)
        self.maker = QWidget()
        mk = QHBoxLayout(self.maker)
        mk.setContentsMargins(0, 0, 0, 0)
        mk.addWidget(_small_label("From"))
        mk.addWidget(self.from_v)
        mk.addWidget(QLabel("→"))
        mk.addWidget(self.to_v)
        mk.addWidget(self.make)
        mk.addWidget(tokens)
        top.addWidget(self.maker)
        self.setup_link = QPushButton("Make change logs (admin)…")
        self.setup_link.setObjectName("link")
        self.setup_link.clicked.connect(self._tokens)
        top.addWidget(self.setup_link)
        root.addLayout(top)

        self.tiles = QHBoxLayout()
        self.tiles.setSpacing(10)
        self.tile_btns = {}
        for cat in changelog.CATEGORIES + [changelog.IN_QA]:
            b = QPushButton()
            b.setCheckable(True)
            b.setMinimumHeight(72)
            b.setToolTip("Show only PRs whose JIRA ticket is in QA (click again to show all)"
                         if cat == changelog.IN_QA else f"Show only {cat} (click again to show all)")
            b.setStyleSheet(f"QPushButton {{ border-left:4px solid {changelog.CATEGORY_COLORS[cat]}; "
                            f"background:#FFFFFF; border-radius:8px; }}"
                            f"QPushButton:checked {{ background:#E1F5EE; border-color:#1D9E75; }}")
            inner = QVBoxLayout(b)
            inner.setContentsMargins(14, 8, 10, 8)
            inner.setSpacing(0)
            num, name = QLabel("0"), QLabel(cat)
            num.setStyleSheet("font-size:24px; font-weight:bold; color:#1A1A18;")
            name.setStyleSheet("font-size:12px; color:#52514E;")
            for lbl in (num, name):
                lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
                inner.addWidget(lbl)
            b.num = num
            b.clicked.connect(lambda on, c=cat: self._filter(c if on else None))
            self.tile_btns[cat] = b
            self.tiles.addWidget(b, 1)
        root.addLayout(self.tiles)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search title, author, PR, PROD key, hash…")
        self.search.textChanged.connect(self._render)
        root.addWidget(self.search)
        self.body = QTextBrowser()
        self.body.setOpenExternalLinks(True)
        self.body.setStyleSheet("QTextBrowser { background:#FFFFFF; border:1px solid #E6E4E0; border-radius:10px; }")
        root.addWidget(self.body, 1)
        self._progress = ""
        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self._progress and self.win._set_status(self._progress))

    # ── data ──────────────────────────────────────────────────────────────────

    def refresh(self):
        data = self.win.data or {}
        self.logs = changelog.saved(data.get("settings"))
        keep = self.log["id"] if self.log else None
        self.pick.blockSignals(True)
        self.pick.clear()
        for l in self.logs:
            when = l.get("generated", "")[:10]
            self.pick.addItem(f"{l['from']}  →  {l['to']}   ·  made {when}", l["id"])
        if not self.logs:
            self.pick.addItem("No change logs yet", None)
        i = self.pick.findData(keep)
        self.pick.setCurrentIndex(max(i, 0))
        self.pick.blockSignals(False)
        versions = changelog.versions_in(data)
        for box in (self.from_v, self.to_v):
            text = box.currentText()
            box.clear()
            box.addItems(versions)
            box.setEditText(text)
        if not self.from_v.currentText() and len(versions) > 1:
            self.from_v.setEditText(versions[1])
            self.to_v.setEditText(versions[0])
        admin = changelog.has_credentials()
        self.maker.setVisible(admin)
        self.setup_link.setVisible(not admin)
        self.delete_btn.setVisible(admin and bool(self.logs))
        self._picked()

    def _picked(self, *_):
        lid = self.pick.currentData()
        self.log = next((l for l in self.logs if l["id"] == lid), None)
        self._render()

    def _filter(self, cat):
        self.filter_cat = cat
        for c, b in self.tile_btns.items():
            b.setChecked(c == cat)
        self._render()

    # ── making one ────────────────────────────────────────────────────────────

    def _tokens(self):
        if TokensDialog(self.win).exec_() == QDialog.Accepted:
            self.refresh()

    def _generate(self):
        a, b = self.from_v.currentText().strip(), self.to_v.currentText().strip()
        if not a or not b or a == b:
            QMessageBox.information(self, "Pick two versions", "Pick a From and a To version (or git hashes).")
            return
        unlocked = self.win._admin_unlock(self, "change logs")
        if not unlocked:
            return
        settings = unlocked[0]

        def work():
            return changelog.generate(a, b, progress=lambda m: setattr(self, "_progress", m))

        def done(log):
            self.timer.stop()
            self._progress = ""
            self.log = log
            self.win._save_settings(changelog.save(settings, log),
                                    f"Change log {a} → {b} saved for everyone "
                                    f"({len(log['entries'])} PRs, {log['commits']} commits).")

        def failed(exc):
            self.timer.stop()
            self._progress = ""
            self.win._set_status("")
            QMessageBox.warning(self, "Couldn't make the change log", str(exc))

        self.timer.start(300)
        self.win._run(work, done, failed, busy_msg=f"Making the change log {a} → {b}…")

    def _delete(self):
        if not self.log:
            return
        if QMessageBox.question(self, "Delete change log", f"Delete the change log {self.log['from']} → "
                                f"{self.log['to']} for everyone?", QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        unlocked = self.win._admin_unlock(self, "change logs")
        if unlocked:
            lid, self.log = self.log["id"], None
            self.win._save_settings(changelog.remove(unlocked[0], lid), "Change log deleted.")

    # ── drawing ───────────────────────────────────────────────────────────────

    def _render(self, *_):
        log = self.log
        counts = {c: 0 for c in changelog.CATEGORIES}
        for e in (log or {}).get("entries", []):
            counts[e["category"]] += 1
        counts[changelog.IN_QA] = len(changelog.qa_entries(log)) if log else 0
        for c, b in self.tile_btns.items():
            b.num.setText(str(counts[c]))
            b.setEnabled(bool(log))
        if not log:
            hint = ("Pick two versions above and click <b>Make change log</b>." if changelog.has_credentials()
                    else "No change logs yet. The admin makes them; they show up here for everyone.")
            self.body.setHtml(f'<p style="color:{MUTED}; font-size:14px; padding:20px">{hint}</p>')
            return
        self.body.setHtml(change_log_html(log, self.search.text(), self.filter_cat))


def change_log_html(log, search="", only=None):
    """The change log as HTML (the Change Log tab, and the export)."""
    esc = html.escape
    q = search.strip().lower()

    qa_ids = {id(e) for e in changelog.qa_entries(log)}

    def hit(e):
        if only == changelog.IN_QA and id(e) not in qa_ids:
            return False
        if only and only != changelog.IN_QA and e["category"] != only:
            return False
        if not q:
            return True
        hay = " ".join([e["title"], e.get("author", ""), str(e.get("pr") or ""), "#" + str(e.get("pr") or ""),
                        " ".join(e.get("commits", [])), e.get("sha", "")] +
                       [t["key"] + " " + t["summary"] for t in e["tickets"]]).lower()
        return q in hay

    linked = sum(1 for e in log["entries"] if e["tickets"])
    not_done, untagged = changelog.warnings(log)
    note = (f"<b>{log['commits']} commits</b> merged between the two builds, in {len(log['entries'])} PRs; "
            f"{linked} reference a {changelog.JIRA_PROJECT} ticket.")
    if not_done:
        note += " Tickets whose JIRA status is not Done: " + ", ".join(
            f'<a href="{esc(t["url"])}">{esc(t["key"])}</a> ({esc(t["status"])})' for t in not_done) + "."
    if untagged:
        note += f" JIRA fix versions are not tagged {esc(untagged)}.x, so they're shown for reference only."
    out = [f'''
<p style="font-size:22px; font-weight:bold; margin:6px 0 2px 0">Change Log</p>
<p style="color:#52514E; margin:0 0 10px 0">{esc(log["repo"].split("/")[-1])} · made {esc(log["generated"][:10])}
from <code>git log {esc(log["base"][:7])}..{esc(log["head"][:7])}</code> and JIRA project {changelog.JIRA_PROJECT}</p>
<table cellspacing="0" cellpadding="8" style="margin-bottom:10px"><tr>
<td style="border:1px solid #E6E4E0"><span style="font-size:10px; color:#8A8984"><b>FROM</b></span><br>
<code style="font-size:14px">{esc(log["from"])}</code></td>
<td style="font-size:18px; color:#8A8984">&nbsp;→&nbsp;</td>
<td style="border:1px solid #E6E4E0"><span style="font-size:10px; color:#8A8984"><b>TO</b></span><br>
<code style="font-size:14px">{esc(log["to"])}</code></td></tr></table>
<table width="100%" cellspacing="0" cellpadding="10" style="margin-bottom:14px"><tr>
<td bgcolor="#FFF6E5" style="border-left:4px solid #F28E2B; font-size:13px">{note}</td></tr></table>''']
    # waiting on QA: the tickets, at the top (when not filtering to a category)
    qa_tickets, seen = [], set()
    for e in log["entries"]:
        if id(e) in qa_ids and hit(e):
            for t in e["tickets"]:
                if changelog.in_qa(t) and t["key"] not in seen:
                    seen.add(t["key"])
                    qa_tickets.append((t, e))
    if qa_tickets and only in (None, changelog.IN_QA):
        color = changelog.CATEGORY_COLORS[changelog.IN_QA]
        rows = "".join(
            f'<tr><td style="padding:3px 10px 3px 0; white-space:nowrap"><a href="{esc(t["url"])}">{esc(t["key"])}</a></td>'
            f'<td style="padding:3px 10px 3px 0">{esc(t["summary"])}</td>'
            f'<td style="padding:3px 10px 3px 0; white-space:nowrap; color:#993556">{esc(t["status"])}</td>'
            f'<td style="padding:3px 0; white-space:nowrap">'
            + (f'<a href="{esc(e["url"])}">PR #{e["pr"]}</a>' if e.get("pr") else esc(e["sha"])) + "</td></tr>"
            for t, e in qa_tickets)
        out.append(f'<p style="font-size:16px; font-weight:bold; margin:16px 0 6px 0">'
                   f'<span style="color:{color}">●</span> In QA '
                   f'<span style="color:#8A8984; font-weight:normal">{len(qa_tickets)} ticket'
                   f'{"s" if len(qa_tickets) != 1 else ""} waiting on QA</span></p>'
                   f'<table width="100%" cellspacing="0" cellpadding="8" style="margin-bottom:8px"><tr>'
                   f'<td bgcolor="#FCEFF4" style="border:1px solid #F4C0D1"><table cellspacing="0">{rows}'
                   f'</table></td></tr></table>')
    for cat in changelog.CATEGORIES:
        items = [e for e in log["entries"] if e["category"] == cat and hit(e)]
        if not items:
            continue
        color = changelog.CATEGORY_COLORS[cat]
        out.append(f'<p style="font-size:16px; font-weight:bold; margin:16px 0 6px 0">'
                   f'<span style="color:{color}">●</span> {esc(cat)} '
                   f'<span style="color:#8A8984; font-weight:normal">{len(items)}</span></p>')
        for e in items:
            pr = (f'<a href="{esc(e["url"])}">PR #{e["pr"]}</a>' if e.get("pr") else
                  f'<a href="{esc(e["url"])}">commit</a>')
            meta = f'{pr} &nbsp; <span style="color:#52514E">{esc(e.get("author", ""))}</span> &nbsp; ' \
                   f'<span style="color:#8A8984">{esc(e["date"])}</span> &nbsp; <code>{esc(e["sha"])}</code>'
            tickets = ""
            for t in e["tickets"]:
                chips = [x for x in (t.get("type"), t.get("priority")) if x]
                chip = "".join(f' <span style="background:#F1EFE8; color:#444441; font-size:11px">&nbsp;{esc(x)}&nbsp;</span>'
                               for x in chips)
                if t.get("status"):
                    done = t.get("status_category") == "done"
                    chip += (f' <span style="background:{"#E3F4E1" if done else "#F1EFE8"}; font-size:11px; '
                             f'color:{"#0B7A0B" if done else "#52514E"}">&nbsp;{esc(t["status"])}&nbsp;</span>')
                for v in t.get("fix_versions", []):
                    chip += f' <span style="background:#F1EFE8; color:#444441; font-size:11px">&nbsp;fix: {esc(v)}&nbsp;</span>'
                tickets += (f'<br><a href="{esc(t["url"])}">{esc(t["key"])}</a> {esc(t["summary"])}{chip}')
            out.append(f'<table width="100%" cellspacing="0" cellpadding="10" style="margin-bottom:8px">'
                       f'<tr><td style="border:1px solid #E6E4E0">'
                       f'<b style="font-size:14px">{esc(e["title"])}</b><br>{meta}'
                       f'{"<hr style=\'color:#ECEAE6\'>" + tickets[4:] if tickets else ""}</td></tr></table>')
    if not any(hit(e) for e in log["entries"]):
        out.append(f'<p style="color:{MUTED}">Nothing matches.</p>')
    return "".join(out)


class FeatureTestingPage(QWidget):
    """Feature testing from JIRA's Testing tickets, in sections like the dashboard:
    Overview, What We Tested (by day), Tested in These Dates, Not Tested in These Dates.
    Uses the dashboard's date filter (the Dates bar moves here when this tab is open)."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 10, 0, 0)
        root.setSpacing(4)
        top = QHBoxLayout()
        title = QLabel("Feature Testing")
        title.setStyleSheet("font-size:20px; font-weight:bold;")
        top.addWidget(title)
        top.addSpacing(10)
        self.info = _small_label("", MUTED)
        top.addWidget(self.info, 1, Qt.AlignBottom)
        self.product = QComboBox()
        for label in ("All products", "VitalOne", "Viking"):
            self.product.addItem(label, None if label.startswith("All") else label)
        self.product.currentIndexChanged.connect(self.refresh)
        top.addWidget(self.product)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search effort, tester, build, notes, PROD key…")
        self.search.setMinimumWidth(300)
        self.search.textChanged.connect(self.refresh)
        top.addWidget(self.search)
        self.sync_btn = QPushButton("↻ Sync from JIRA")
        self.sync_btn.clicked.connect(lambda: self.win.sync_testing(force=True))
        self.jql_btn = QPushButton("JQL…")
        self.jql_btn.setObjectName("link")
        self.jql_btn.clicked.connect(self.win.edit_testing_jql)
        top.addWidget(self.sync_btn)
        top.addWidget(self.jql_btn)
        root.addLayout(top)
        self.date_slot = QVBoxLayout()               # the dashboard's Dates bar sits here on this tab
        self.date_slot.setContentsMargins(0, 0, 0, 0)
        root.addLayout(self.date_slot)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        root.addWidget(self.scroll, 1)

    # ── pieces ────────────────────────────────────────────────────────────────

    @staticmethod
    def _card(title, sub=""):
        card = QFrame()
        card.setObjectName("card")
        v = QVBoxLayout(card)
        v.setContentsMargins(18, 14, 18, 16)
        v.setSpacing(10)
        head = QHBoxLayout()
        t = QLabel(title)
        t.setStyleSheet("font-size:15px; font-weight:bold;")
        head.addWidget(t)
        if sub:
            head.addWidget(_small_label(sub, MUTED), 0, Qt.AlignBottom)
        head.addStretch(1)
        v.addLayout(head)
        return card, v

    def _rich(self, text, size=12, color="#1A1A18"):
        lbl = QLabel(text)
        lbl.setTextFormat(Qt.RichText)
        lbl.setOpenExternalLinks(False)
        lbl.linkActivated.connect(self._link)
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
        lbl.setStyleSheet(f"font-size:{size}px; color:{color};")
        return lbl

    @staticmethod
    def _pill(text, bg="#F1EFE8", fg="#52514E"):
        return (f'<span style="background-color:{bg}; color:{fg}; font-size:11px">&nbsp;'
                f'{html.escape(text)}&nbsp;</span>')

    @staticmethod
    def _jira(key, label=None):
        return (f'<a href="{changelog.JIRA_SITE}/browse/{html.escape(key)}" style="color:#2F5E9E; '
                f'text-decoration:none">{html.escape(label or key)}</a>')

    def _effort_table(self, efforts, rng, today):
        """One table: effort (with its tickets), status, assignee, sessions, last session, bugs."""
        lo, hi = (rng[0].isoformat(), rng[1].isoformat()) if rng else ("", "9999")
        th = lambda t, right=False: (f'<th align="{"right" if right else "left"}" style="color:#888885; '
                                     f'font-size:10px; padding:0 0 6px 14px">{t}</th>')
        rows = [f'<tr>{th("EFFORT")}{th("STATUS")}{th("ASSIGNEE")}{th("SESSIONS", True)}'
                f'{th("LAST SESSION", True)}{th("NEW BUGS", True)}{th("SEEN AGAIN", True)}{th("HW REQUESTS", True)}</tr>']
        cell = lambda x, right=False, first=False: (
            f'<td valign="middle" align="{"right" if right else "left"}" style="border-top:1px solid #ECEAE6; '
            f'padding:9px 0 9px {0 if first else 14}px">{x}</td>')
        for e in efforts:
            in_range = [x for x in e["sessions"] if x["date"] and lo <= x["date"] <= hi]
            chain = " → ".join(self._jira(t["key"], (f"PT{t['part']} " if len(e["tickets"]) > 1 else "") + t["key"])
                               for t in e["tickets"])
            new = bool(rng) and bool(e["tickets"][0]["created"]) and lo <= e["tickets"][0]["created"] <= hi
            name = (f'<b>{html.escape(e["name"])}</b>&nbsp; {self._pill(e["product"])}'
                    + (f'&nbsp; {self._pill("New", "#E8F1FC", "#2F5E9E")}' if new else "")
                    + f'<br><span style="font-size:11px">{chain}</span>')
            done = e["status_category"] == "done"
            if e["last"]:
                ago = (today - date.fromisoformat(e["last"])).days
                last = "today" if ago == 0 else f"{ago} day{'s' if ago != 1 else ''} ago"
                color = "#B26B00" if ago > 7 and not done else "#52514E"
            else:
                last, color = "none logged yet", "#8A8984"
            sessions = f"{len(in_range)} of {len(e['sessions'])}" if rng and e["sessions"] else str(len(e["sessions"]))
            settings = _settings(self.win)
            counted = in_range if rng else e["sessions"]      # only the sessions in the chosen dates
            kinds = {}
            for x in counted:
                for k in x["bugs"]:
                    kd = feature_testing.bug_kind(settings, x["ticket"], x, k)
                    kinds[k] = "new" if "new" in (kinds.get(k), kd) else kd
            counts = {"new": sum(v == "new" for v in kinds.values()), "seen": sum(v == "seen" for v in kinds.values())}
            hw_keys = list(dict.fromkeys(k for x in counted for k in x.get("support", [])))
            rows.append("<tr>" + cell(name, first=True)
                        + cell(self._pill(e["status"], "#E3F4E1" if done else "#F1EFE8", "#0B7A0B" if done else "#52514E"))
                        + cell(html.escape(", ".join(e["assignees"]) or "—"))
                        + cell(sessions, True)
                        + cell(f'<span style="color:{color}">{html.escape(last)}</span>', True)
                        + cell(str(counts["new"]) if counts["new"] else '<span style="color:#8A8984">—</span>', True)
                        + cell(str(counts["seen"]) if counts["seen"] else '<span style="color:#8A8984">—</span>', True)
                        + cell(str(len(hw_keys)) if hw_keys else '<span style="color:#8A8984">—</span>', True)
                        + "</tr>")
        return self._rich(f'<table width="100%" cellspacing="0" cellpadding="0">{"".join(rows)}</table>', 12)

    def _link(self, href):
        if href.startswith("kind:"):                  # kind:<ticket>#<session>:<bug>:<new kind>
            ref, _, kind = href[5:].rpartition(":")
            self.win.set_bug_kind(ref, kind)
        elif href.startswith("inst:"):                # inst:<instrument>:hide / :show
            key, _, act = href[5:].rpartition(":")
            self.win.set_instrument_hidden(key, act == "hide")
        else:
            _open_link(href)

    # ── drawing ───────────────────────────────────────────────────────────────

    def _instrument(self, inst, today, admin=False):
        """One instrument: HARDWARE SUPPORT REQUESTED with its open tickets, and when it was last tested."""
        box = QFrame()
        box.setObjectName("tile")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(14, 8, 14, 10)
        bl.setSpacing(3)
        if inst["last"]:
            e, s = inst["last"]
            d = date.fromisoformat(s["date"])
            ago = (today - d).days
            when = "today" if ago == 0 else "yesterday" if ago == 1 else f"{d:%b %-d}"
            last = (f'last tested {when} · {html.escape(e["name"])} '
                    f'{self._jira(s["ticket"], "S" + str(s["n"]))}')
        else:
            last = "no sessions logged on it"
        state = self._pill("HARDWARE SUPPORT REQUESTED", "#FFF6E5", "#854F0B")
        hide = (f'&nbsp; <a href="inst:{html.escape(inst["key"])}:hide" style="color:#8A8984; font-size:11px">'
                f'hide</a>') if admin else ""
        bl.addWidget(self._rich(f'<b style="font-size:14px">{html.escape(inst["name"])}</b>&nbsp; {state}'
                                f'&nbsp; <span style="color:#8A8984">{last}</span>{hide}', 13))
        for r in inst["open"]:
            bl.addWidget(self._rich(
                f'{self._pill(r["status"] or "Open")}&nbsp; {self._jira(r["key"])} {html.escape(r["summary"])}'
                f'<span style="color:#8A8984"> · from {html.escape(r["effort"])} '
                f'{self._jira(r["ticket"], r["ticket"] + " S" + str(r["n"]))}</span>', 12))
        return box

    def refresh(self, *_):
        data = self.win.data or {}
        testing = data.get("testing")
        admin = changelog.has_credentials()
        self.sync_btn.setVisible(admin)
        self.jql_btn.setVisible(admin)
        body = QWidget()
        body.setObjectName("dash")
        root = QVBoxLayout(body)
        root.setContentsMargins(0, 10, 8, 20)
        root.setSpacing(14)
        if not testing or not testing.get("tickets"):
            self.info.setText("")
            root.addWidget(_small_label("Click ↻ Sync from JIRA to read the Testing tickets." if admin else
                                        "Nothing synced from JIRA yet. The admin's app syncs the Testing "
                                        "tickets here for everyone.", MUTED))
            root.addStretch(1)
            self.scroll.setWidget(body)
            return
        try:
            when = datetime.fromisoformat(testing["synced"]).strftime("%b %-d, %H:%M")
        except (KeyError, ValueError):
            when = "?"
        n = len(testing["tickets"])
        self.info.setText(f"{n} Testing ticket{'s' if n != 1 else ''} from JIRA · synced {when}")
        d = self.win.dashboard
        rng, rtext = d.range, d.range_text
        product, q = self.product.currentData(), self.search.text().strip().lower()
        today = date.today()

        def hay(e, s=None):
            bits = [e["name"], e["status"], " ".join(e["assignees"]), " ".join(t["key"] for t in e["tickets"])]
            if s:
                bits += [s["build"], s["system"], " ".join(s["testers"]), " ".join(s["notes"]), " ".join(s["bugs"])]
            return " ".join(bits).lower()

        efforts = [e for e in feature_testing.efforts(testing) if (not product or e["product"] == product)]
        shown = [(e, s) for e, s in feature_testing.sessions_in(testing, rng)
                 if (not product or e["product"] == product) and (not q or q in hay(e, s))]
        tested_keys = {e["key"] for e in feature_testing.efforts_in(testing, rng)}
        tested = [e for e in efforts if e["key"] in tested_keys and (not q or q in hay(e))]
        untested = [e for e in efforts if e["key"] not in tested_keys and e["status_category"] != "done"
                    and (not q or q in hay(e))]
        active = [e for e in efforts if e["status_category"] != "done"]
        stale = [e for e in active if not e["last"] or (today - date.fromisoformat(e["last"])).days > 7]
        testers = {x for _, s in shown for x in s["testers"]}
        bugs = list(dict.fromkeys(k for _, s in shown for k in s["bugs"]))
        info = testing.get("support") or {}
        bug_info = testing.get("bug_info") or {}
        settings = _settings(self.win)
        kind = lambda s, k: feature_testing.bug_kind(settings, s["ticket"], s, k)
        new_bugs = list(dict.fromkeys(k for _, s in shown for k in s["bugs"] if kind(s, k) == "new"))
        seen_bugs = [k for k in dict.fromkeys(k for _, s in shown for k in s["bugs"] if kind(s, k) == "seen")
                     if k not in new_bugs]
        admin_bugs = changelog.has_credentials()
        hw = list(dict.fromkeys(k for _, s in shown for k in s.get("support", [])))
        hw_open = [k for k in hw if (info.get(k) or {}).get("status_category") != "done"]

        # Overview
        card, v = self._card("Overview", rtext)
        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        for label, value, sub, color in (
                ("Sessions", len(shown), rtext, INK),
                ("Efforts tested", len(tested), f"{len(active)} active in JIRA", INK),
                ("Testers", len(testers), ", ".join(sorted(testers))[:40] or "—", INK),
                ("New bugs", len(new_bugs), f"{len(seen_bugs)} seen again" if seen_bugs else "found in testing",
                 charts.PROGRESS if new_bugs else INK),
                ("HW support requests", len(hw), f"{len(hw_open)} still open" if hw else "none made",
                 "#B26B00" if hw_open else INK),
                ("No session in 7+ days", len(stale), "active efforts", "#B26B00" if stale else INK)):
            tiles.addWidget(blocks._tile_widget(label, value, color, sub, None), 1)
        v.addLayout(tiles)
        insts = [i for i in feature_testing.instruments(testing, rng, lambda e: not product or e["product"] == product)
                 if i["open"]]                  # only instruments waiting on hardware support
        hidden_keys = feature_testing.hidden_instruments(settings)
        hidden = [i for i in insts if i["key"] in hidden_keys]
        insts = [i for i in insts if i["key"] not in hidden_keys]
        if insts or (hidden and admin):
            lbl = QLabel("Instruments")
            lbl.setStyleSheet("font-size:13px; font-weight:bold; color:#52514E; padding-top:8px;")
            v.addWidget(lbl)
            for inst in insts:
                v.addWidget(self._instrument(inst, today, admin))
            if hidden and admin:                # only the admin sees what's hidden, to show it again
                v.addWidget(self._rich("Hidden: " + ", ".join(
                    f'{html.escape(i["name"])} <a href="inst:{html.escape(i["key"])}:show" style="color:#8A8984; '
                    f'font-size:11px">show</a>' for i in hidden), 12, "#8A8984"))
        root.addWidget(card)

        # What We Tested, by day
        card, v = self._card("What We Tested", f"{len(shown)} session{'s' if len(shown) != 1 else ''} · {rtext}")
        if not shown:
            v.addWidget(_small_label(f"No test sessions logged in {rtext}.", MUTED))
        day = None
        for e, s in shown:
            if s["date"] != day:
                day = s["date"]
                lbl = QLabel(date.fromisoformat(day).strftime("%A, %b %-d"))
                lbl.setStyleSheet("font-size:13px; font-weight:bold; color:#52514E; padding-top:6px;")
                v.addWidget(lbl)
            box = QFrame()
            box.setObjectName("tile")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(14, 10, 14, 12)
            bl.setSpacing(4)
            bl.addWidget(self._rich(
                f'<b style="font-size:14px">{html.escape(e["name"])}</b>&nbsp; {self._pill(e["product"])}'
                f'&nbsp; {self._jira(s["ticket"], "Session " + str(s["n"]))}', 13))
            meta = " · ".join(html.escape(x) for x in (", ".join(s["testers"]), s["time"],
                                                        s["build"] and "Build " + s["build"], s["system"]) if x)
            bl.addWidget(self._rich(meta, 12, "#8A8984"))
            for k in s["bugs"]:                       # each bug: new in this session, or seen again
                b_ = bug_info.get(k) or {}
                is_new = kind(s, k) == "new"
                done_ = b_.get("status_category") == "done"
                flip = (f'&nbsp; <a href="kind:{s["ticket"]}#{s["n"]}:{k}:{"seen" if is_new else "new"}" '
                        f'style="color:#8A8984; font-size:11px">change</a>') if admin_bugs else ""
                bl.addWidget(self._rich(
                    (self._pill("New Bug", "#E8F1FC", "#2F5E9E") if is_new else self._pill("Bug Seen Again"))
                    + f'&nbsp; {self._jira(k)}' + (f' {html.escape(b_["summary"])}' if b_.get("summary") else "")
                    + (f'&nbsp; {self._pill(b_["status"], "#E3F4E1" if done_ else "#F1EFE8", "#0B7A0B" if done_ else "#52514E")}'
                       if b_.get("status") else "") + flip, 12))
            for k in s.get("support", []):
                t_ = info.get(k) or {}
                done_ = t_.get("status_category") == "done"
                bl.addWidget(self._rich(
                    f'{self._pill("Hardware Support Request", "#FFF6E5", "#854F0B")}&nbsp; {self._jira(k)}'
                    + (f' {html.escape(t_["summary"])}' if t_.get("summary") else "")
                    + (f'&nbsp; {self._pill(t_["status"], "#E3F4E1" if done_ else "#F1EFE8", "#0B7A0B" if done_ else "#52514E")}'
                       if t_.get("status") else ""), 12))
            notes = "".join(f'<div style="margin-left:{14 * ((len(ln) - len(ln.lstrip())) // 2)}px">'
                            f'{_linkify(ln.strip())}</div>' for ln in s["notes"][:16])
            if len(s["notes"]) > 16:
                notes += (f'<div style="color:#8A8984">… {len(s["notes"]) - 16} more lines — '
                          f'{self._jira(s["ticket"], "open in JIRA")}</div>')
            if notes:
                bl.addWidget(self._rich(notes, 12))
            v.addWidget(box)
        root.addWidget(card)

        # efforts
        card, v = self._card("Tested in These Dates", f"{len(tested)} effort{'s' if len(tested) != 1 else ''} · {rtext}")
        if tested:
            v.addWidget(self._effort_table(tested, rng, today))
        else:
            v.addWidget(_small_label(f"No test efforts with sessions in {rtext}.", MUTED))
        root.addWidget(card)
        card, v = self._card("Not Tested in These Dates", "active efforts with no session in the dates — "
                             "including new tickets with no sessions logged yet")
        if untested:
            v.addWidget(self._effort_table(untested, rng, today))
        else:
            v.addWidget(_small_label("Every active effort was tested in these dates.", MUTED))
        root.addWidget(card)
        root.addStretch(1)
        pos = self.scroll.verticalScrollBar().value()
        self.scroll.setWidget(body)
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(pos))


def _linkify(text):
    """Notes text → HTML with short links: JIRA URLs become their key, Atlantis runs "Atlantis run"."""
    esc = html.escape

    def link(m):
        url = m.group(0).rstrip(").,")
        key = re.search(r"/browse/([A-Z][A-Z0-9]+-\d+)", url)
        label = key.group(1) if key else ("Atlantis run" if "atlantis" in url and "/run/" in url else
                                          (url.split("/")[2] if "//" in url else url))
        return f'<a href="{esc(url, quote=True)}">{esc(label)}</a>'
    parts, last = [], 0
    for m in re.finditer(r"https?://\S+", text):
        parts.append(esc(text[last:m.start()]))
        parts.append(link(m))
        last = m.end()
    parts.append(esc(text[last:]))
    return "".join(parts)


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

    def _go_rows(self, tab, rows):
        """Open a tab's table showing exactly these rows (by their first column, e.g. the
        run number), whatever their dates — e.g. open issues carried over from earlier days."""
        # identify rows by the first column whose values are unique (Run Number, Key…)
        filled = [r for r in tab["rows"] if not store.is_blank(r)]
        key = next((c for c in tab["columns"]
                    if len({r["values"][c["pos"]] for r in filled}) == len(filled)), tab["columns"][0])
        runs = [x for r in rows for x in r.get("_runs", [r])]       # a repeated issue opens all its runs
        self._go(tab["title"], {key["pos"]: {r["values"][key["pos"]] for r in runs} or {"\0"}}, dates=False)

    def _go(self, title, filters=None, dates=True):
        """Open a tab's table, filtered like the chart that was clicked — including the dates
        (unless dates=False, e.g. for open issues carried over from earlier days)."""
        filters = dict(filters or {})
        tab = next((t for t in self.win.data["tabs"] if t["title"] == title), None)
        if tab and dates and self._dated(tab):
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

        cur = self.win.current_view()
        title = QLabel(html.escape(data["title"]) + (f" <span style='color:#1D9E75'>· {html.escape(cur['name'])}</span>"
                                                     if cur else ""))
        title.setTextFormat(Qt.RichText)
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

        view = self.win.current_view()
        if view is not None:                       # a custom view: just its cards
            self._view_cards(root, data, view)
            root.addStretch(1)
            self.setWidget(body)
            self.body = body
            QTimer.singleShot(0, lambda: self.verticalScrollBar().setValue(pos))
            return

        tiles = QGridLayout()
        tiles.setSpacing(10)
        tabs = [t for t in data["tabs"] if _visible_rows(t) or t["rows"]]
        for i, tab in enumerate(tabs):
            tiles.addWidget(self._tile(tab), i // 4, i % 4)
        for c in range(min(4, len(tabs))):
            tiles.setColumnStretch(c, 1)
        root.addLayout(tiles)

        for tab in tabs:
            lo = self.win.layout_of(tab)
            for card in lo["cards"]:
                if not card["dash"] or (card["id"] == "analysis" and not _visible_rows(tab)):
                    continue
                try:
                    root.addWidget(self._card(tab, card, lo))
                except Exception as exc:          # a broken layout shows a note, not a crash
                    root.addWidget(_small_label(f"Couldn't draw {layout.card_title(tab, card)}: "
                                                f"{type(exc).__name__}: {exc}", RED))
        root.addStretch(1)
        self.setWidget(body)
        self.body = body
        QTimer.singleShot(0, lambda: self.verticalScrollBar().setValue(pos))

    def _view_cards(self, root, data, view):
        e = self.win.editing
        if e and e.get("view") == view["id"]:
            view = e["view_obj"]                   # the copy being edited
        got = views.entries(view, data, self.win.layout_of)
        if not got:
            box = QFrame()
            box.setObjectName("card")
            v = QVBoxLayout(box)
            v.setContentsMargins(18, 18, 18, 18)
            v.addWidget(QLabel(f"<b>{html.escape(view['name'])}</b> has no cards yet."))
            add = QPushButton("+ Add cards…")
            add.setObjectName("primary")
            add.clicked.connect(lambda: self.win.view_settings())
            v.addWidget(_small_label("Add cards from the main page (they stay in sync with it), or "
                                     "new cards of your own with any charts you like.", MUTED))
            v.addWidget(add, 0, Qt.AlignLeft)
            root.addWidget(box)
            return
        for tab, card, lo, entry in got:
            try:
                root.addWidget(self._card(tab, card, lo, linked="ref" in entry))
            except Exception as exc:
                root.addWidget(_small_label(f"Couldn't draw {layout.card_title(tab, card)}: "
                                            f"{type(exc).__name__}: {exc}", RED))

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

    def _card(self, tab, card, lo, preview=False, linked=False):
        """One dashboard card, drawn from its layout (see layout.py). While it's being
        edited (✎ Edit), each chart is a tile you can drag, resize, and change."""
        ctx = blocks.Ctx(self.range, self.range_text, export=False, settings=_settings(self.win),
                         data=self.win.data)
        editing = not preview and self.is_editing(tab, card)
        frame = QFrame()
        frame.setObjectName("card_edit" if editing else "card")
        lay_ = QVBoxLayout(frame)
        lay_.setContentsMargins(18, 14, 18, 16)
        lay_.setSpacing(10)
        head = QHBoxLayout()
        t = QLabel(layout.card_title(tab, card))
        t.setStyleSheet("font-size:15px; font-weight:bold;")
        head.addWidget(t)
        head.addWidget(_small_label(blocks.card_note(card, [tab], ctx), MUTED), 0, Qt.AlignBottom)
        head.addStretch(1)
        if editing:
            settings_btn = QPushButton("Card settings…")
            settings_btn.setObjectName("link")
            settings_btn.clicked.connect(lambda: self.win.card_settings(tab, card))
            cancel = QPushButton("Cancel")
            cancel.clicked.connect(self.win.cancel_editing)
            done = QPushButton("Done editing")
            done.setObjectName("primary")
            done.clicked.connect(self.win.finish_editing)
            for w in (settings_btn, cancel, done):
                head.addWidget(w)
        elif linked:
            head.addWidget(_small_label("from Main", MUTED), 0, Qt.AlignBottom)
            open_btn = QPushButton("Open table →")
            open_btn.setObjectName("link")
            open_btn.clicked.connect(lambda: self._go(tab["title"]))
            head.addWidget(open_btn)
        elif not preview:
            edit = QPushButton("✎ Edit")
            edit.setObjectName("link")
            edit.setToolTip("Add, move, resize and change this card's charts")
            edit.clicked.connect(lambda: self.win.start_editing(tab, card["id"]))
            head.addWidget(edit)
            open_btn = QPushButton("Open table →")
            open_btn.setObjectName("link")
            open_btn.clicked.connect(lambda: self._go(tab["title"]))
            head.addWidget(open_btn)
        lay_.addLayout(head)

        if not preview and not editing and card is lo["cards"][0]:
            for col in layout.new_columns(tab, lo, self.win.known_columns(tab)):
                lay_.addWidget(self._new_column_banner(tab, col))

        target = (lambda t: None) if (preview or editing) else self._target
        groups = layout.split_groups(tab, card)
        if not groups or editing:
            if groups:
                lay_.addWidget(_small_label(f"Shown once per section ({', '.join(n for n, _ in groups)}) "
                                            "when you're done.", MUTED))
            self._card_body(lay_, tab, card, lo, ctx, target, editing)
        else:
            for name, test in groups:                  # labelled sections, same card
                sub = ctx.section(test)
                n = len(sub.all_rows(tab))
                head = QLabel(f"{html.escape(name.upper())} <span style='color:{MUTED}; font-weight:normal'>"
                              f"· {n} bug{'s' if n != 1 else ''}</span>")
                head.setTextFormat(Qt.RichText)
                head.setStyleSheet("font-size:12px; font-weight:bold; color:#1A1A18; padding-top:8px;"
                                   "border-top:1px solid #ECEAE6;")
                lay_.addWidget(head)
                self._card_body(lay_, tab, card, lo, sub, target)
        if editing:
            add_row = QHBoxLayout()
            add = QPushButton("+ Add chart")
            add.setObjectName("dashed")
            add.clicked.connect(lambda: self.win.add_chart(tab, card))
            gallery = QPushButton("▦ Add from gallery ▾")
            gallery.setObjectName("dashed")
            gallery.clicked.connect(lambda: self._gallery_menu(tab, card, gallery))
            add_row.addWidget(add, 1)
            add_row.addWidget(gallery, 1)
            lay_.addLayout(add_row)
            lay_.addWidget(_small_label("Drag ⠿ to move a chart (drop it on another chart) · drag a "
                                        "chart's right edge to resize · ⋯ for edit, export and more", MUTED))
        return frame

    def _card_body(self, lay_, tab, card, lo, ctx, target, editing=False):
        drawn = []
        for b in card["blocks"]:
            if not b["dash"] and not editing:
                continue
            try:
                parts = blocks.render(b, [tab], ctx)
            except Exception as exc:               # one broken chart doesn't take the card down
                parts = [blocks.Part(layout.auto_title(tab, b),
                                     f'<span style="color:{MUTED}">Couldn\'t draw this chart '
                                     f'({html.escape(type(exc).__name__)}: {html.escape(str(exc))})</span>')]
            if parts or editing:
                drawn.append((b, parts or []))
        if not drawn:
            if card["id"] == "main" and not ctx.rows(tab):
                text = "No rows in the chosen dates." if ctx.dated(tab) else "Nothing filled in yet."
            else:
                text = "Nothing to show" + (" in the chosen dates." if ctx.dated(tab) else " yet.")
            lay_.addWidget(_small_label(text, MUTED))
            return

        if editing:                                   # one tile per chart, in its rows
            for row in blocks.rows_of(drawn):
                h = QHBoxLayout()
                h.setSpacing(12)
                for b, parts in row:
                    h.addWidget(EditTile(self, tab, card, b, parts, target),
                                int(b.get("size") or 1), Qt.AlignTop)
                lay_.addLayout(h)
            return

        lines = []
        for row in blocks.rows_of(drawn):
            cur = []
            for b, p in ((b, p) for b, ps in row for p in ps):
                if p.full and not isinstance(p.content, str) or not p.full:
                    cur.append((b, p))
                    continue
                if cur:                                  # tables get a row of their own
                    lines.append(cur)
                lines.append([(b, p)])
                cur = []
            if cur:
                lines.append(cur)
        for items in lines:
            parts = [p for _, p in items]
            texts = [p for p in parts if isinstance(p.content, str)]
            if len(texts) > 1 and len(texts) == len(parts):     # side-by-side lists wrap
                flow = _FlowColumns()
                for p in parts:
                    flow.add(self._part(tab, p, target))
                lay_.addWidget(flow)
                continue
            h = QHBoxLayout()
            h.setSpacing(28)
            for b, p in items:
                h.addWidget(self._part(tab, p, target),
                            int(b.get("size") or 1) if p.wide else 0, Qt.AlignTop)
            if not any(p.wide for p in parts):
                h.addStretch(1)
            lay_.addLayout(h)

    # ── editing ───────────────────────────────────────────────────────────────

    def is_editing(self, tab, card):
        e = self.win.editing
        here = self.win.current_view()
        return bool(e) and e["tab"] == tab["title"] and e["card"] == card["id"] and \
            e.get("view") == (here["id"] if here else None)

    def _gallery_menu(self, tab, card, button):
        menu = QMenu(button)
        for label, b in layout.gallery(tab):
            act = menu.addAction(label)
            act.triggered.connect(lambda _, b=b: self.win.insert_block(tab, card, b))
        menu.exec_(button.mapToGlobal(button.rect().bottomLeft()))

    def _part(self, tab, part, target):
        c = part.content
        pick = (lambda payload, p=part: target(p.pick(payload))) if part.pick else None
        if isinstance(c, list):
            w = QWidget()
            v = QVBoxLayout(w)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(6)
            for sub in c:
                v.addWidget(self._part(tab, sub, target))
            v.addStretch(1)
        elif isinstance(c, str):
            w = QLabel(c)
            w.setTextFormat(Qt.RichText)
            w.setWordWrap(True)
            w.setStyleSheet("font-size:12px;")
            w.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            w.linkActivated.connect(lambda href, pk=pick: self._link(href, pk))
        else:
            w = c
            if pick and hasattr(w, "picked"):
                w.picked.connect(pick)
            if pick and hasattr(w, "picked_cb"):
                w.picked_cb = pick
        return self._titled(part.title, w) if part.title else w

    def _link(self, href, pick):
        if href.startswith("pick:"):
            if pick:
                pick(href[5:])
        elif href.startswith("cell:"):                 # cell:<sheet row>:<column pos>:<tab title>
            r, pos, title = href[5:].split(":", 2)
            self.win.go_to_cell(title, int(r), int(pos))
        else:
            _open_link(href)

    def _target(self, t):
        """Open what a click on a chart points at."""
        if not t:
            return
        kind, tab, what = t
        if kind == "filters":
            self._go(tab["title"], what)
        else:
            self._go_rows(tab, what)

    def _new_column_banner(self, tab, col):
        box = QFrame()
        box.setObjectName("banner")
        h = QHBoxLayout(box)
        h.setContentsMargins(12, 6, 8, 6)
        lbl = QLabel(f"New column <b>{html.escape(col['name'])}</b> — add a chart for it?")
        lbl.setStyleSheet("font-size:12px;")
        h.addWidget(lbl, 1)
        choose = QPushButton("Choose…")
        choose.clicked.connect(lambda: self.win.start_editing(tab, layout.layout_for(
            tab, _settings(self.win))["cards"][0]["id"], add_column=col["name"]))
        skip = QPushButton("Not needed")
        skip.setObjectName("link")
        skip.setToolTip("Hide this note (just for you). You can still add it later in ✎ Customize.")
        skip.clicked.connect(lambda: self.win.dismiss_column(tab, col["name"]))
        h.addWidget(choose)
        h.addWidget(skip)
        return box

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

        self.editing = None            # the card being edited on the dashboard (✎ Edit)
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
        self.view_bar = ViewBar(self)
        dash_lay.addWidget(self.view_bar)
        self.view_bar.refresh()
        dash_lay.addWidget(self.date_bar)
        dash_lay.addWidget(self.dashboard, 1)
        self.tabs.addTab(dash_page, "📊 Dashboard")
        self.changelog_page = ChangeLogPage(self)
        self.tabs.addTab(self.changelog_page, "📝 Change Log")
        self.testing_page = FeatureTestingPage(self)
        self.tabs.addTab(self.testing_page, "🧪 Feature Testing")
        self._dash_layout = dash_lay
        self.tabs.currentChanged.connect(self._place_date_bar)
        self.empty = QLabel("Add your test-tracking Google Sheet in ⚙ Settings to get started.")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet(f"color:{MUTED}; font-size:14px;")
        self.dashboard.setWidget(self.empty)
        root.addWidget(self.tabs, 1)

        status_row = QHBoxLayout()
        self.status_lbl = _small_label("Ready.", MUTED)
        self.conn_lbl = _small_label("")
        self.jira_lbl = _small_label("")
        self.jira_lbl.mousePressEvent = lambda e: (self.sync_testing(force=True)
                                                   if changelog.has_credentials() else None)
        self.update_btn = QPushButton()
        self.update_btn.setObjectName("update")
        self.update_btn.hide()
        self.update_btn.clicked.connect(self._install_update)
        status_row.addWidget(self.status_lbl, 1)
        status_row.addWidget(self.update_btn)
        status_row.addSpacing(10)
        status_row.addWidget(self.conn_lbl)
        status_row.addSpacing(10)
        status_row.addWidget(self.jira_lbl)
        version_btn = QPushButton(f"v{APP_VERSION}" + (" testing" if APP_CHANNEL == "testing" else ""))
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

    def _set_jira(self, state=None, detail=""):
        """The JIRA dot beside the Google Sheets one: when Feature Testing was last synced from
        JIRA (saved in the sheet, so everyone sees it), or syncing / failed in the admin's app."""
        if state is None:
            state = "syncing" if getattr(self, "_testing_busy", False) else getattr(self, "_jira_state", "ok")
        self._jira_state = state if state in ("failed",) else "ok"
        synced = ((self.data or {}).get("testing") or {}).get("synced") or ""
        try:
            when = datetime.fromisoformat(synced)
        except ValueError:
            when = None
        if when is None:
            stamp = ""
        elif when.date() == date.today():
            stamp = f"{when:%H:%M}"
        else:
            stamp = f"{when:%b %-d %H:%M}"
        stale = when is None or (datetime.now() - when).total_seconds() > 3 * feature_testing.SYNC_MINUTES * 60
        if state == "syncing":
            text, color = "● JIRA · syncing…", AMBER
        elif state == "failed":
            text, color = "● JIRA sync failed" + (f" · last {stamp}" if stamp else ""), RED
        elif when is None:
            text, color = "● JIRA · not synced yet", MUTED
        else:
            text, color = f"● JIRA · synced {stamp}", AMBER if stale else GREEN
        mine = changelog.has_credentials()
        tip = detail or ("Feature Testing, from JIRA's Testing tickets. " +
                         ("Synced by your app every few minutes — click to sync now." if mine
                          else "Synced by the admin's app every few minutes."))
        self.jira_lbl.setText(text)
        self.jira_lbl.setStyleSheet(f"color:{color}; font-size:11px;")
        self.jira_lbl.setToolTip(tip)
        self.jira_lbl.setCursor(Qt.PointingHandCursor if mine else Qt.ArrowCursor)

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
            snap = dlg.snapshot()
            png = snap.save_png(png)[0]
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

    def go_to_cell(self, title, sheet_row, pos):
        """Open a tab's table with one cell selected (e.g. a run's Notes)."""
        page = self.pages.get(title)
        if page is None:
            return
        self.tabs.setCurrentWidget(page)
        page.go_to_cell(sheet_row, pos)

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
        self.view_bar.refresh()                   # shared views may have changed in the sheet
        self.changelog_page.refresh()
        self.testing_page.refresh()
        self._set_jira()
        QTimer.singleShot(1500, self.sync_testing)      # keep the JIRA testing data fresh (admin's app)
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
                self.tabs.insertTab(i + 3, page, tab["title"])     # after Dashboard, Change Log, Feature Testing
            else:
                page.set_tab(tab)
                if self.tabs.indexOf(page) != i + 3:
                    self.tabs.removeTab(self.tabs.indexOf(page))
                    self.tabs.insertTab(i + 3, page, tab["title"])
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
        look = dlg.appearance.currentData()
        look_changed = look != self.cfg.get("appearance", "light")
        self.cfg["appearance"] = look
        store.save_config(self.cfg)
        if look_changed and QMessageBox.question(
                self, "Appearance", "Restart Test Tracker now to switch its look?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) == QMessageBox.Yes:
            self._restart()
            return
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

    # ── card layouts ──────────────────────────────────────────────────────────

    def layout_of(self, tab):
        """The tab's card layout: the admin's saved one, or the built-in default
        (or the copy being edited, while a card of this tab is in edit mode)."""
        if self.editing and self.editing["tab"] == tab["title"]:
            return self.editing["layout"]
        return layout.layout_for(tab, _settings(self))

    def known_columns(self, tab):
        """Columns this app has already seen (or been told to ignore) on a tab, so
        only columns added later get the “New column” note. Kept on this computer."""
        sheets = self.cfg.setdefault("known_columns", {})
        known = sheets.setdefault(store.sheet_id(self.url) or self.url, {})
        key = tab["title"].strip().lower()
        if key not in known:
            known[key] = [c["name"].strip() for c in tab["columns"]]
            store.save_config(self.cfg)
        return known[key]

    def dismiss_column(self, tab, name):
        self.known_columns(tab).append(name.strip())
        store.save_config(self.cfg)
        self.dashboard.set_data(self.data)

    def layouts_elsewhere(self, tab):
        """[(release name, layout)] saved for a tab of the same name in other release sheets."""
        out = []
        for s in self.cfg["sheets"]:
            if s["url"] == self.url:
                continue
            raw = ((store.load_cache(s["url"]) or {}).get("settings") or {}).get(layout.key_for(tab))
            if raw:
                lo = layout.layout_for(tab, {layout.key_for(tab): raw})
                if lo.get("custom"):
                    out.append((s.get("title") or s["url"], lo))
        return out

    # ── views ─────────────────────────────────────────────────────────────────

    def all_views(self):
        return views.all_views(self.cfg, self.url, _settings(self)) if self.url else []

    def current_view_id(self):
        return self.cfg.setdefault("current_view", {}).get(store.sheet_id(self.url) or self.url, views.MAIN)

    def current_view(self):
        """The custom view on screen, or None for MAIN (or if it was deleted)."""
        vid = self.current_view_id()
        if vid == views.MAIN:
            return None
        return next((v for v in self.all_views() if v["id"] == vid), None)

    def set_view(self, view_id):
        if self.editing:
            self.cancel_editing()
        self.cfg.setdefault("current_view", {})[store.sheet_id(self.url) or self.url] = view_id
        store.save_config(self.cfg)
        self.view_bar.refresh()
        if self.data:
            self.dashboard.set_data(self.data)

    def new_view(self):
        if not self.data:
            return
        name, ok = QInputDialog.getText(self, "New view", "Name your view (e.g. “EOD”, “Viking only”):")
        if not ok or not name.strip():
            return
        v = views.new_view(name)
        views.private(self.cfg, self.url).append(v)
        store.save_config(self.cfg)
        self.set_view(v["id"])
        self.view_settings()

    def _save_view(self, view, settings=None, msg=None):
        """Shared views go to the sheet (needs the unlocked admin settings); yours to this computer."""
        if view.get("shared"):
            settings = dict(settings if settings is not None else _settings(self))
            settings[views.PREFIX + view["id"]] = views.dump(view)
            self._save_settings(settings, msg or f"Saved the shared view “{view['name']}” for everyone.")
        else:
            mine = views.private(self.cfg, self.url)
            i = next((k for k, v in enumerate(mine) if v["id"] == view["id"]), None)
            if i is None:
                mine.append(view)
            else:
                mine[i] = view
            store.save_config(self.cfg)
            self.view_bar.refresh()
            self.dashboard.set_data(self.data)
            self._set_status(msg or f"Saved the view “{view['name']}”.")

    def view_settings(self):
        view = self.current_view()
        if view is None:
            return
        settings = None
        if view["shared"]:
            box = QMessageBox(self)
            box.setWindowTitle("Shared view")
            box.setText(f"“{view['name']}” is shared with everyone. Changing it needs the admin passcode. "
                        "You can also make your own copy to change freely.")
            change = box.addButton("Change it (admin)", QMessageBox.AcceptRole)
            mine = box.addButton("Make my own copy", QMessageBox.ActionRole)
            box.addButton(QMessageBox.Cancel)
            box.exec_()
            if box.clickedButton() is mine:
                self._duplicate_view(view)
                return
            if box.clickedButton() is not change:
                return
            unlocked = self._admin_unlock(self, "shared views")
            if not unlocked:
                return
            settings = unlocked[0]
        dlg = ViewSettingsDialog(self, view, bool(settings))
        if dlg.exec_() != QDialog.Accepted:
            return
        new = dlg.result_view()
        new["shared"] = view["shared"]
        if dlg.action == "delete":
            if QMessageBox.question(self, "Delete view", f"Delete the view “{view['name']}”"
                                    + (" for everyone?" if view["shared"] else "?"),
                                    QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return
            if view["shared"]:
                settings = dict(settings)
                settings.pop(views.PREFIX + view["id"], None)
                self.cfg.setdefault("current_view", {})[store.sheet_id(self.url) or self.url] = views.MAIN
                self._save_settings(settings, f"Deleted the shared view “{view['name']}”.")
            else:
                mine = views.private(self.cfg, self.url)
                mine[:] = [v for v in mine if v["id"] != view["id"]]
                self.set_view(views.MAIN)
            return
        if dlg.action == "duplicate":
            self._duplicate_view(new)
            return
        if dlg.action == "share":
            unlocked = self._admin_unlock(self, "shared views")
            if not unlocked:
                return
            mine = views.private(self.cfg, self.url)
            mine[:] = [v for v in mine if v["id"] != view["id"]]
            store.save_config(self.cfg)
            new["shared"] = True
            self._save_view(new, unlocked[0], f"Shared “{new['name']}” with everyone.")
            return
        if dlg.action == "unshare":
            settings = dict(settings)
            settings.pop(views.PREFIX + view["id"], None)
            new["shared"] = False
            views.private(self.cfg, self.url).append(new)
            store.save_config(self.cfg)
            self._save_settings(settings, f"“{new['name']}” is now only on this computer.")
            return
        self._save_view(new, settings)

    def _duplicate_view(self, view):
        copy_ = views.copy_view(view)
        copy_.update(id=layout.new_id(), name=f"{view['name']} (my copy)", shared=False)
        for e in copy_["cards"]:
            if "card" in e:
                e["card"]["id"] = layout.new_id()
        views.private(self.cfg, self.url).append(copy_)
        store.save_config(self.cfg)
        self.set_view(copy_["id"])
        self._set_status(f"Made “{copy_['name']}”, your own copy.")

    # ── editing cards on the dashboard ────────────────────────────────────────
    # ✎ Edit on a card (admin passcode) works on a copy of the tab's layout; Done
    # editing saves it to the sheet for everyone, Cancel throws it away.

    def _tab(self, title):
        return next((t for t in (self.data or {}).get("tabs", []) if t["title"] == title), None)

    def start_editing(self, tab, card_id, add_column=None):
        if self.editing and (self.editing["tab"], self.editing["card"]) != (tab["title"], card_id):
            if QMessageBox.question(self, "Still editing", "Save the card you're editing first?",
                                    QMessageBox.Save | QMessageBox.Discard) == QMessageBox.Save:
                self.finish_editing()
            else:
                self.cancel_editing()
        view = self.current_view()
        if view is not None:                      # a card of a custom view
            settings = stored = None
            if view["shared"]:
                unlocked = self._admin_unlock(self, "shared views")
                if not unlocked:
                    return
                settings, stored = unlocked
            self.editing = {"tab": tab["title"], "card": card_id, "view": view["id"],
                            "view_obj": views.copy_view(view), "shared": view["shared"],
                            "settings": settings, "stored": stored}
            self._redraw()
            self._set_status("Editing a card of “" + view["name"] + "” — Done editing saves it"
                             + (" for everyone." if view["shared"] else "."))
            return
        unlocked = self._admin_unlock(self, "dashboard cards")
        if not unlocked:
            return
        settings, stored = unlocked
        lo = layout.copy_layout(layout.layout_for(tab, settings))
        card = next((c for c in lo["cards"] if c["id"] == card_id), lo["cards"][0])
        self.editing = {"tab": tab["title"], "card": card["id"], "layout": lo,
                        "settings": settings, "stored": stored}
        self.dashboard.set_data(self.data)
        self._set_status(f"Editing {layout.card_title(tab, card)} — Done editing saves it for everyone.")
        if add_column:
            col = layout.find_column(tab, add_column)
            spec = default_spec(tab)
            spec.update(group=add_column.strip(), show="pie" if col and col["kind"] == "dropdown" else "bars")
            if col and col["kind"] == "date":
                spec.update(show="days")
            self.add_chart(tab, card, spec=spec)

    def _edit_card(self, card_id=None):
        e = self.editing
        if not e:
            return None
        if e.get("view"):
            return next((x["card"] for x in e["view_obj"]["cards"]
                         if "card" in x and x["card"]["id"] == (card_id or e["card"])), None)
        return next((c for c in e["layout"]["cards"] if c["id"] == (card_id or e["card"])), None)

    def _redraw(self):
        self.dashboard.set_data(self.data)

    def cancel_editing(self):
        e, self.editing = self.editing, None
        if e and e.get("settings") and e["settings"].get(store.PASSCODE_KEY) != e["stored"]:
            self._save_settings(e["settings"], "Admin passcode set.")
        self._redraw()
        self._set_status("Changes thrown away.")

    def finish_editing(self):
        e, self.editing = self.editing, None
        if not e:
            return
        if e.get("view"):
            vo = e["view_obj"]
            vo["shared"] = e["shared"]
            self._save_view(vo, e["settings"])
            return
        tab = self._tab(e["tab"])
        settings = dict(e["settings"])
        key = layout.key_for({"title": e["tab"]})
        if e.get("reset"):
            settings.pop(key, None)
        else:
            lo = dict(e["layout"], columns=[c["name"].strip() for c in (tab or {}).get("columns", [])])
            settings[key] = layout.dump(lo)
        if tab:
            known = self.known_columns(tab)
            known += [c["name"].strip() for c in tab["columns"] if c["name"].strip() not in known]
            store.save_config(self.cfg)
        self._redraw()
        self._save_settings(settings, f"Saved the {e['tab']} cards for everyone.")

    def card_settings(self, tab, card):
        e = self.editing
        dlg = CardSettingsDialog(self, tab, card, e.get("layout"), in_view=bool(e.get("view")))
        if dlg.exec_() != QDialog.Accepted:
            return
        if e.get("view"):                          # a view's own card
            cards = e["view_obj"]["cards"]
            if dlg.action == "delete":
                cards[:] = [x for x in cards if x.get("card") is not card]
                self.finish_editing()
                return
            dlg.apply()
            if dlg.action == "new":
                name, ok = QInputDialog.getText(self, "New card", "Card title:")
                if ok and name.strip():
                    new = {"id": layout.new_id(), "title": name.strip(), "dash": True, "export": True,
                           "combine": False, "split": None, "blocks": []}
                    cards.append({"tab": tab["title"], "card": new})
                    e["card"] = new["id"]
            self._redraw()
            return
        if dlg.action == "reset":
            if QMessageBox.question(self, "Reset to built-in",
                                    f"Go back to the app's own charts for every {tab['title']} card? "
                                    "This applies to everyone when you click Done editing.",
                                    QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
                e["layout"] = layout.default_layout(tab, _settings(self))
                e["card"] = e["layout"]["cards"][0]["id"]
                e["reset"] = True
        elif dlg.action == "delete":
            e["layout"]["cards"] = [c for c in e["layout"]["cards"] if c is not card]
            e["card"] = e["layout"]["cards"][0]["id"]
        else:
            dlg.apply()
            if dlg.action == "new":
                name, ok = QInputDialog.getText(self, "New card", "Card title:")
                if ok and name.strip():
                    new = {"id": layout.new_id(), "title": name.strip(), "dash": True, "export": True,
                           "combine": False, "split": None, "blocks": []}
                    e["layout"]["cards"].append(new)
                    e["card"] = new["id"]
            e["reset"] = False
        self._redraw()

    def add_chart(self, tab, card, spec=None):
        dlg = AddChartDialog(self, tab)
        if spec:
            dlg._loading = True
            dlg._load(spec, None)
            dlg._loading = False
            dlg._changed()
        if dlg.exec_() == QDialog.Accepted:
            self.insert_block(tab, card, dlg.result_block())

    def insert_block(self, tab, card, b):
        card = self._edit_card(card["id"]) or card
        card["blocks"].append(b)
        self.editing["reset"] = False
        self._redraw()

    def _block(self, card, block_id):
        card = self._edit_card(card["id"])
        i = next((k for k, b in enumerate(card["blocks"]) if b["id"] == block_id), None) if card else None
        return card, i

    def edit_block(self, tab, card, block_id):
        card, i = self._block(card, block_id)
        if i is None:
            return
        b = card["blocks"][i]
        if b["type"] != "sql" and spec_from_block(tab, b) is None:
            title, ok = QInputDialog.getText(
                self, "Edit chart", f"“{layout.auto_title(tab, b)}” is one of the app's own charts "
                "(it isn't a simple count, so it can't become a query). You can rename it:",
                text=b.get("title") or layout.auto_title(tab, b))
            if ok:
                b["title"] = title.strip() if title.strip() != layout.auto_title(tab, dict(b, title="")) else ""
                self._redraw()
            return
        dlg = AddChartDialog(self, tab, b)
        if dlg.exec_() == QDialog.Accepted:
            card["blocks"][i] = dlg.result_block()
            self.editing["reset"] = False
            self._redraw()

    def duplicate_block(self, tab, card, block_id):
        card, i = self._block(card, block_id)
        if i is not None:
            nb = layout.copy_layout(card["blocks"][i])
            nb.update(id=layout.new_id(), new_row=False)
            card["blocks"].insert(i + 1, nb)
            self._redraw()

    def remove_block(self, tab, card, block_id):
        card, i = self._block(card, block_id)
        if i is not None:
            removed = card["blocks"].pop(i)
            if removed.get("new_row") and i < len(card["blocks"]):
                card["blocks"][i]["new_row"] = True       # keep the next chart on its own row
            self._redraw()

    def set_block(self, tab, card, block_id, **changes):
        card, i = self._block(card, block_id)
        if i is not None:
            card["blocks"][i].update(changes)
            self.editing["reset"] = False
            self._redraw()

    def move_block(self, tab, card, src_id, target_id, after):
        card, i = self._block(card, src_id)
        _, j = self._block(card, target_id)
        if i is None or j is None or i == j:
            return
        blocks_ = card["blocks"]
        b = blocks_.pop(i)
        if b.get("new_row") and i < len(blocks_):
            blocks_[i]["new_row"] = True                  # the chart after it keeps its row start
        j = next(k for k, x in enumerate(blocks_) if x["id"] == target_id)
        if after:
            b["new_row"] = False
            blocks_.insert(j + 1, b)
        else:                                             # take the target's place in its row
            b["new_row"], blocks_[j]["new_row"] = blocks_[j].get("new_row", False), False
            blocks_.insert(j, b)
        self._redraw()

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
        unlocked = self._admin_unlock(parent or self, "filters")
        if not unlocked:
            return
        settings, stored = unlocked
        dlg = ColumnsDialog(self, self.data, settings)
        if dlg.exec_() != QDialog.Accepted:
            if settings.get(store.PASSCODE_KEY) != stored:
                self._save_settings(settings, "Admin passcode set.")
            return
        self._save_settings(dlg.result_settings(), "Filters saved for everyone.")

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
            if APP_CHANNEL == "testing":         # testing builds also see newer testing builds
                found = _fetch_releases()
                if not found:
                    raise RuntimeError("no release found")
                self._latest_zip = found[0]["zip"]
                return found[0]["tag"]
            self._latest_zip = None
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
        self._run_installer(getattr(self, "_latest_zip", None))

    def _run_installer(self, zip_url=None):
        """Download and install a release (the newest stable one unless zip_url is given);
        the app closes, updates, and reopens by itself."""
        env = dict(os.environ)
        if zip_url:
            env["TRACKER_ZIP_URL"] = zip_url
        subprocess.Popen(["/bin/bash", "-c", INSTALL_CMD], start_new_session=True, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._set_status("Installing… the app will reopen by itself.")
        QTimer.singleShot(800, QApplication.quit)

    def open_versions(self, parent=None):
        dlg = VersionsDialog(self)
        if dlg.exec_() != QDialog.Accepted or not dlg.chosen():
            return
        r = dlg.chosen()
        if not (getattr(sys, "frozen", False) and sys.platform == "darwin"):
            _open_link(f"https://github.com/{REPO}/releases/tag/{r['tag']}")
            return
        reply = QMessageBox.question(
            parent or self, "Install another version",
            f"Install {r['tag']} ({'testing build' if r['testing'] else 'stable'}) in place of "
            f"v{APP_VERSION}?\n\nThe app will close, install it, and reopen by itself in about a minute.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if reply == QMessageBox.Yes:
            self._run_installer(r["zip"])

    def _place_date_bar(self, *_):
        """One Dates bar for the dashboard and the Feature Testing tab: it moves to whichever is open."""
        if self.tabs.currentWidget() is self.testing_page:
            if self.date_bar.parent() is not self.testing_page:
                self.testing_page.date_slot.addWidget(self.date_bar)
        elif self.date_bar.parent() is self.testing_page:
            self._dash_layout.insertWidget(1, self.date_bar)       # back under the view bar
        self.date_bar.refresh()

    # ── feature testing from JIRA ─────────────────────────────────────────────

    def sync_testing(self, force=False):
        """Read the Testing tickets from JIRA and save them to the sheet's hidden tab, so
        everyone sees them. Only the app with the JIRA token does this, at most every few
        minutes (or now, with force)."""
        import time
        if not (self.data and self.source and self.can_edit and changelog.has_credentials()):
            if force:
                QMessageBox.information(self, "Can't sync", "Connect to the sheet, and add your JIRA token "
                                        "(Change Log → Tokens…), then try again.")
            return
        if getattr(self, "_testing_busy", False):
            return
        if not force and time.time() - getattr(self, "_testing_at", 0) < feature_testing.SYNC_MINUTES * 60:
            return
        self._testing_busy, self._testing_at = True, time.time()
        self._set_jira("syncing")
        query, src = feature_testing.jql(_settings(self)), self.source

        def work():
            snap = feature_testing.snapshot(query)
            src.save_testing(feature_testing.to_rows(snap))
            return snap

        def done(snap):
            self._testing_busy = False
            self.data["testing"] = snap
            store.save_cache(self.url, self.data)
            self.testing_page.refresh()
            self._set_jira("ok")
            if force:
                self._set_status(f"Synced {len(snap['tickets'])} Testing tickets from JIRA.")

        def failed(exc):
            self._testing_busy = False
            self._set_jira("failed", f"Couldn't sync from JIRA: {exc}")
            if force:
                QMessageBox.warning(self, "Couldn't sync from JIRA", str(exc))
            else:
                self._set_status(f"Feature testing sync: {exc}")

        self._run(work, done, failed, busy_msg="Syncing feature testing from JIRA…" if force else None)

    def set_bug_kind(self, ref, kind):
        """Admin: correct whether a bug linked in a test session was new or seen again."""
        unlocked = self._admin_unlock(self, "bug labels in feature testing")
        if not unlocked:
            return
        settings = dict(unlocked[0])
        settings[feature_testing.BUG_KIND_PREFIX + ref] = kind
        self._save_settings(settings, f"Marked {ref.rpartition(':')[2]} as "
                            + ("a new bug." if kind == "new" else "seen again."))

    def set_instrument_hidden(self, key, hide):
        """Admin: hide an instrument from Feature Testing's Instruments (and the export) for everyone,
        or show it again."""
        unlocked = self._admin_unlock(self, "hiding instruments")
        if not unlocked:
            return
        settings = dict(unlocked[0])
        settings[feature_testing.HIDE_INST_PREFIX + key] = "1" if hide else ""
        self._save_settings(settings, f"{'Hid' if hide else 'Showing'} {key.upper()} in Instruments.")

    def edit_testing_jql(self):
        unlocked = self._admin_unlock(self, "the feature-testing JQL")
        if not unlocked:
            return
        settings = unlocked[0]
        dlg = QDialog(self)
        dlg.setWindowTitle("Feature testing JQL")
        dlg.setMinimumWidth(640)
        edit = QPlainTextEdit(feature_testing.jql(settings))
        edit.setMinimumHeight(140)
        lay_ = QVBoxLayout(dlg)
        lay_.addWidget(_small_label("Which JIRA tickets count as feature testing. Saved in the sheet; your app "
                                    "syncs them for everyone.", MUTED))
        lay_.addWidget(edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay_.addWidget(buttons)
        if dlg.exec_() != QDialog.Accepted:
            return
        settings = dict(settings)
        settings[feature_testing.JQL_KEY] = " ".join(edit.toPlainText().split())
        self._save_settings(settings, "Feature-testing JQL saved.")
        QTimer.singleShot(500, lambda: self.sync_testing(force=True))

    def _restart(self):
        """Quit and open the app again (e.g. to switch light / dark)."""
        if getattr(sys, "frozen", False) and sys.platform == "darwin":
            bundle = os.path.abspath(os.path.join(os.path.dirname(sys.executable), "..", ".."))
            subprocess.Popen(["/bin/sh", "-c", f'sleep 1; open -n "{bundle}"'], start_new_session=True)
        else:
            subprocess.Popen([sys.executable] + sys.argv, start_new_session=True)
        QTimer.singleShot(200, QApplication.quit)

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
    dark_mode = ui_theme.wants_dark(store.load_config().get("appearance", "light"))
    ui_theme.install(dark_mode, modules=[sys.modules[__name__], blocks])
    app.setPalette(ui_theme.palette() if dark_mode else _light_palette())
    window = TrackerApp()
    window.show()
    sys.exit(app.exec_())
