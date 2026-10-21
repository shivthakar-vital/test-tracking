"""
The app's light or dark look (⚙ Settings → Appearance), applied at startup.

The app's styles are written with light colours. In dark mode every style sheet and
rich-text label goes through dark(), which swaps each light neutral for its dark
partner — text colours (after "color:") one way, backgrounds and borders another —
while data colours (statuses, categories) stay as they are. Charts get their own
dark colours (charts.set_theme). Exports always stay light unless their own theme says so.
"""

import re, subprocess, sys

# text colours (after "color:")
TEXT = {"#1A1A18": "#ECEAE4", "#52514E": "#C2C0B9", "#666663": "#B0AEA7", "#888885": "#9A9891",
        "#8A8984": "#9A9891", "#999996": "#8E8C86", "#AAAAA6": "#6E6D69", "#444441": "#D3D1C7",
        "#0F6E56": "#7FD8B6", "#2F5E9E": "#7DB0FF", "#993556": "#F4A7C0", "#0B7A0B": "#5CC45C",
        "#A32D2D": "#F08080", "#854F0B": "#E3A33B", "#3B6D11": "#8FCB5A", "#B26B00": "#E3A33B"}
# backgrounds and borders (everything else)
FILL = {"#FFFFFF": "#262624", "#F5F5F2": "#1C1C1B", "#FAFAF8": "#2A2A28", "#EEECEA": "#33322F",
        "#E0DDD8": "#3A3936", "#E6E4E0": "#3A3936", "#CCCCC8": "#4A4945", "#F0EEE9": "#2F2E2C",
        "#F0EFEB": "#2A2A28", "#F1EFE8": "#33322F", "#F3F2EF": "#2E2D2B", "#ECEAE6": "#33322F",
        "#F7F7F5": "#2A2A28", "#E1F5EE": "#1F3A30", "#FFF6E5": "#3A3020", "#F3D9A4": "#6B5520",
        "#FCEFF4": "#3A2630", "#F4C0D1": "#6B3A4C", "#E3F4E1": "#1F3A22", "#E8F1FC": "#1E2A3A",
        "#B4B2A9": "#6E6D69", "#BDBBB6": "#5A5955", "#8CCDB8": "#2E6B57", "#F4F3EF": "#2A2A28"}
_HEX = re.compile(r"((?<![-\w])color\s*:\s*)(#[0-9A-Fa-f]{6})|(#[0-9A-Fa-f]{6})")

DARK = False

def dark(text):
    """Light style/HTML → dark (does nothing in light mode)."""
    if not DARK or not isinstance(text, str) or "#" not in text:
        return text

    def swap(m):
        if m.group(1):
            return m.group(1) + TEXT.get(m.group(2).upper(), m.group(2))
        return FILL.get(m.group(3).upper(), m.group(3))
    return _HEX.sub(swap, text)


def system_is_dark():
    if sys.platform != "darwin":
        return False
    try:
        r = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True, text=True,
                           timeout=3)
        return r.stdout.strip().lower() == "dark"
    except Exception:
        return False

def wants_dark(appearance):
    return appearance == "dark" or (appearance == "system" and system_is_dark())


def install(dark_mode, modules=()):
    """Turn dark mode on for the app: patch style sheets and rich text, and swap
    the QLabel used by our modules for one that themes its text."""
    global DARK
    DARK = bool(dark_mode)
    import charts
    charts.set_theme(DARK)
    if not DARK:
        return
    from PyQt5.QtWidgets import QWidget, QLabel, QTextBrowser
    set_style = QWidget.setStyleSheet
    QWidget.setStyleSheet = lambda self, s: set_style(self, dark(s))
    set_text = QLabel.setText
    QLabel.setText = lambda self, s: set_text(self, dark(s))
    set_html = QTextBrowser.setHtml
    QTextBrowser.setHtml = lambda self, s: set_html(self, dark(s))

    class ThemedLabel(QLabel):
        def __init__(self, *args, **kwargs):
            if args and isinstance(args[0], str):
                args = (dark(args[0]),) + args[1:]
            super().__init__(*args, **kwargs)

    for m in modules:
        if getattr(m, "QLabel", None) is QLabel:
            m.QLabel = ThemedLabel


def palette():
    from PyQt5.QtGui import QPalette, QColor
    pal = QPalette()
    for role, color in [(QPalette.Window, "#1C1C1B"), (QPalette.WindowText, "#ECEAE4"),
                        (QPalette.Base, "#262624"), (QPalette.AlternateBase, "#2A2A28"),
                        (QPalette.Text, "#ECEAE4"), (QPalette.Button, "#262624"),
                        (QPalette.ButtonText, "#ECEAE4"), (QPalette.ToolTipBase, "#2E2D2B"),
                        (QPalette.ToolTipText, "#ECEAE4"), (QPalette.Highlight, "#1D9E75"),
                        (QPalette.HighlightedText, "#FFFFFF"), (QPalette.PlaceholderText, "#8E8C86"),
                        (QPalette.Link, "#7DB0FF"), (QPalette.BrightText, "#FFFFFF")]:
        pal.setColor(role, QColor(color))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor("#6E6D69"))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#6E6D69"))
    pal.setColor(QPalette.Disabled, QPalette.WindowText, QColor("#6E6D69"))
    return pal
