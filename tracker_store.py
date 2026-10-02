"""
Data layer for Test Tracker.

The Google Sheet is the database. Every visible tab is read as a table:
  • If the tab uses a Google Sheets *Table* (Format → Convert to table), its
    column types and dropdown options come straight from the table, so the app
    shows dropdowns and date pickers that match the sheet.
  • Otherwise the first non-empty row is the header, and dropdown options come
    from the cells' data validation.

Hidden tabs are skipped. Nothing about a particular release is hard-coded, so a
new release tab (or a copy of the whole file) works without changing the app.

Before writing a cell, the app re-reads the tab and checks that the row is still
where it was and the cell still holds what the user saw, so edits made by other
people (or typed straight into the sheet) are never silently overwritten.
"""

import hashlib, hmac, json, os, re, secrets, shutil, sys
from datetime import date, datetime

APP_NAME = "TestTracker"

# ── files & config ────────────────────────────────────────────────────────────

def _data_dir(name=APP_NAME):
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    elif os.name == "nt":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    else:
        base = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return os.path.join(base, name)

DATA_DIR = _data_dir()
os.makedirs(DATA_DIR, exist_ok=True)
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
CACHE_FILE  = os.path.join(DATA_DIR, "cache.json")
CREDS_FILE  = os.path.join(DATA_DIR, "service_account.json")
INVENTORY_CREDS = os.path.join(_data_dir("QaveInventory"), "service_account.json")

DEFAULTS = {"sheets": [], "current": "", "show_empty_rows": False, "wrap_text": True}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_FILE) as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    cfg["sheets"] = [s for s in cfg["sheets"] if isinstance(s, dict) and s.get("url")]
    return cfg

def save_config(cfg):
    _write_json(CONFIG_FILE, cfg)

def load_cache(url):
    try:
        with open(CACHE_FILE) as f:
            return json.load(f).get(url)
    except (OSError, ValueError, AttributeError):
        return None

def save_cache(url, data):
    try:
        with open(CACHE_FILE) as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    cache[url] = data
    _write_json(CACHE_FILE, cache)

def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)

def service_account_email(path=CREDS_FILE):
    try:
        with open(path) as f:
            return json.load(f).get("client_email", "")
    except (OSError, ValueError, AttributeError):
        return ""

def install_credentials(src):
    shutil.copy(src, CREDS_FILE)
    os.chmod(CREDS_FILE, 0o600)

def adopt_inventory_key():
    """Reuse Qave Inventory's key on first run, so there's one less setup step."""
    if os.path.exists(CREDS_FILE) or not service_account_email(INVENTORY_CREDS):
        return False
    install_credentials(INVENTORY_CREDS)
    return True

def sheet_id(url):
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", url or "")
    return m.group(1) if m else ""

def tab_url(url, tab):
    return f"https://docs.google.com/spreadsheets/d/{sheet_id(url)}/edit#gid={tab['id']}"


# ── shared app settings (colours, admin passcode) ─────────────────────────────
# These live in a hidden tab of the sheet, so every install sees the same ones
# and a copied release sheet keeps them.

SETTINGS_TAB = "Test Tracker Settings"
PASSCODE_KEY = "admin_passcode"
COLOR_PREFIX = "color:"

def hash_passcode(passcode):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", passcode.encode(), bytes.fromhex(salt), 200_000)
    return f"pbkdf2${salt}${digest.hex()}"

def check_passcode(passcode, stored):
    try:
        _, salt, want = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", passcode.encode(), bytes.fromhex(salt), 200_000)
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(got.hex(), want)

def color_overrides(settings):
    """{key: "#RRGGBB"} chosen by the admin. Keys are a status value ("blocked"),
    a subsystem ("subsystem:ia"), or a column and value ("workflow type:cc12n dry"),
    all lower case."""
    return {k[len(COLOR_PREFIX):]: v for k, v in (settings or {}).items()
            if k.startswith(COLOR_PREFIX) and re.fullmatch(r"#[0-9A-Fa-f]{6}", v)}


# ── errors ────────────────────────────────────────────────────────────────────

class TrackerError(Exception):
    """A problem the user can fix."""

class ConflictError(TrackerError):
    """The cell or row changed in the sheet since the last sync."""
    def __init__(self, msg, current=None):
        super().__init__(msg)
        self.current = current

def describe_error(exc):
    """Turn any exception into a message a person can act on."""
    if isinstance(exc, TrackerError):
        return str(exc)
    import gspread, google.auth.exceptions as gauth
    email = service_account_email()
    share = f"\n\nMake sure the sheet is shared with:\n{email}\n(as an Editor)." if email else ""
    if isinstance(exc, FileNotFoundError):
        return "No Google service-account key installed. Add one in ⚙ Settings."
    if isinstance(exc, (gspread.exceptions.SpreadsheetNotFound,
                        gspread.exceptions.NoValidUrlKeyFound)):
        return "Couldn't find that Google Sheet. Check the link in ⚙ Settings." + share
    if isinstance(exc, PermissionError):
        return "No permission to open the Google Sheet." + share
    if isinstance(exc, gspread.exceptions.APIError):
        code = exc.response.status_code
        if code == 403:
            return "No permission to edit the Google Sheet." + share
        if code == 429:
            return "Google Sheets is rate-limiting requests. Wait a minute and try again."
        if code == 400:
            return f"Google Sheets didn't accept that value.\n\n{exc}"
        return f"Google Sheets error ({code}): {exc}"
    if isinstance(exc, gauth.RefreshError):
        return "Google rejected the service-account key. It may have been deleted — add a new one in ⚙ Settings."
    if isinstance(exc, (gauth.TransportError, OSError)):
        return "Couldn't reach Google Sheets. Check your internet connection."
    return f"{type(exc).__name__}: {exc}"


# ── column helpers (shared with the UI) ───────────────────────────────────────

COLUMN_KINDS = {"DROPDOWN": "dropdown", "DATE": "date", "DATE_TIME": "date",
                "DOUBLE": "number", "CURRENCY": "number", "PERCENT": "number",
                "BOOLEAN": "checkbox"}

def parts(col, value):
    """A multi-select dropdown cell like "SMOKE, DRY" → ["SMOKE", "DRY"]."""
    if col["kind"] == "dropdown" and "," in value:
        bits = [b.strip() for b in value.split(",") if b.strip()]
        opts = {o.lower() for o in col["options"]}
        if bits and all(b.lower() in opts for b in bits):
            return bits
    return [value]

def is_multi(col, rows):
    return col["kind"] == "dropdown" and any(
        len(parts(col, r["values"][col["pos"]])) > 1 for r in rows)

def is_blank(row):
    """Placeholder rows, e.g. a run number with nothing else filled in yet."""
    return not any(v.strip() for v in row["values"][1:])

_DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%d %b %Y", "%b %d, %Y", "%d/%b/%y",
                 "%d/%b/%Y", "%b %d %Y", "%m/%d")

def parse_date(text):
    """A date from the formats people and Jira use, ignoring any time of day:
    9/30/2026, 2026-09-30, 2026-09-30T14:22:05.000-0700, 30/Sep/26 2:22 PM, Sep 30, 2026…"""
    text = (text or "").strip()
    for candidate in (text, re.split(r"[ T]", text, 1)[0]):
        for fmt in _DATE_FORMATS:
            try:
                d = datetime.strptime(candidate, fmt).date()
                return d.replace(year=date.today().year) if fmt == "%m/%d" else d
            except ValueError:
                pass
    return None

def done_date_column(tab):
    """e.g. "Done Date" / "Resolved" / "Closed Date": when a bug was completed."""
    for c in tab["columns"]:
        w = _words(c["name"])
        if (w & {"done", "closed", "resolved", "completed"}) and (w & {"date", "on", "at"} or len(w) == 1):
            return c
    return None

def created_date_column(tab):
    """e.g. "Created" / "Date Opened" / "Reported On": when a bug was raised."""
    for c in tab["columns"]:
        w = _words(c["name"])
        if c not in status_columns(tab) and (w & {"created", "opened", "reported", "raised"}) \
                and (w & {"date", "on", "at"} or len(w) == 1):
            return c
    return None

def resolved_column(tab):
    """e.g. "Resolved?" on a run tracker: the date a failed run's problem was fixed."""
    return next((c for c in tab["columns"] if c not in status_columns(tab)
                 and _words(c["name"]) & {"resolved", "resolution", "fixed"}), None)

FAILED = ("blocked", "cancelled", "warning")      # status classes that count as a failed run

def resolutions(tab, rows):
    """For each failed run: (row, resolved date or None, days it took or None, run date or None).
    Days = the Resolved date minus the run's date (0 = fixed the same day)."""
    import charts
    res, dcol = resolved_column(tab), date_column(tab)
    out = []
    if res is None:
        return out
    for r in rows:
        if charts.classify(row_status(tab, r)) not in FAILED:
            continue
        ran = parse_date(r["values"][dcol["pos"]]) if dcol else None
        fixed = parse_date(r["values"][res["pos"]])
        days = (fixed - ran).days if fixed and ran and fixed >= ran else None
        out.append((r, fixed, days, ran))
    return out

def is_bug_tab(tab):
    return bool(done_date_column(tab) or created_date_column(tab)) and "bug" in tab["title"].lower()

def in_range(value, rng):
    """rng: (first day, last day) or None for all dates."""
    d = parse_date(value)
    return d is not None and (rng is None or rng[0] <= d <= rng[1])

def status_column(tab):
    """The column that says how far along each row is (e.g. "Current Status")."""
    cols = tab["columns"]
    for test in (lambda c: c["kind"] == "dropdown" and "status" in c["name"].lower(),
                 lambda c: "status" in c["name"].lower(),
                 lambda c: c["kind"] == "dropdown" and c["options"]):
        for c in cols:
            if test(c):
                return c
    return None

def date_column(tab):
    return next((c for c in tab["columns"] if c["kind"] == "date"), None)

# ── what gets a filter and a chart ────────────────────────────────────────────
# Kept strict on purpose: columns full of generated or one-off values (run names,
# patient names, IDs, notes…) make useless filters and charts. An admin can turn
# any column's filter or chart on or off in the app (⚙ Settings → Charts & filters).

RANDOM_WORDS = ("name", "link", "url", "serial", "key", "summary", "note", "comment",
                "hash", "path", "script", "description", "title", "number", "patient", "id",
                "image")
CHART_OFF_BY_DEFAULT = {"workflow type", "equipment"}
FILTER_PREFIX, CHART_PREFIX, PIE_PREFIX = "filter:", "chart:", "pie:"
PIE_WORDS = ("why", "reason", "cause", "category", "categories",  # get their own pie chart
             "workflow", "workflows")

def is_workflow(col):
    return "workflow" in _words(col["name"])
PLATFORM = "platform"      # derived column: which system a run was on (Coder / Atlantis)
STATUS = "status"          # derived column: a row's status, from whichever status column is filled

def _words(name):
    return set(re.findall(r"[a-z]+", name.lower()))

def looks_random(col):
    return bool(_words(col["name"]) & set(RANDOM_WORDS))

def is_version(col):
    return "version" in _words(col["name"])

def status_columns(tab):
    """All status dropdowns, e.g. "Coder E2E Status" and "Atlantis Status"."""
    cols = [c for c in tab["columns"] if c["kind"] == "dropdown" and "status" in c["name"].lower()]
    if cols:
        return cols
    one = status_column(tab)
    return [one] if one else []

def run_name_column(tab):
    """End-to-end run trackers have a run name column (e.g. "Run Name w/ Link")."""
    return next((c for c in tab["columns"] if "run name" in c["name"].lower()), None)

def is_run_tab(tab):
    return run_name_column(tab) is not None

def platform_column(tab):
    """Run trackers whose runs can be on Coder or Atlantis: a run named "Coder E2E"
    ran on Coder, any other (generated) name on Atlantis. Trackers with no Coder
    status column and no Coder runs (e.g. Viking, always on Atlantis) have no split."""
    col = run_name_column(tab)
    if col is None:
        return None
    coder = any("coder" in c["name"].lower() for c in status_columns(tab)) or \
        any("coder" in r["values"][col["pos"]].lower() for r in tab["rows"])
    return col if coder else None

def row_platform(tab, row):
    col = platform_column(tab)
    v = row["values"][col["pos"]].strip() if col else ""
    if not v:
        return ""
    return "Coder" if "coder" in v.lower() else "Atlantis"

def row_status(tab, row):
    """The row's status. With several status columns, the one for the row's
    platform (e.g. Atlantis Status for an Atlantis run) wins, then any filled one."""
    cols = status_columns(tab)
    plat = row_platform(tab, row).lower()
    if plat:
        cols = sorted(cols, key=lambda c: plat not in c["name"].lower())
    for c in cols:
        v = row["values"][c["pos"]].strip()
        if v:
            return v
    return ""

def derived_columns(tab):
    """Pseudo-columns the app filters and charts by, keyed by name ("status", "platform")."""
    out = {}
    cols = status_columns(tab)
    if cols:
        opts = list(dict.fromkeys(o for c in cols for o in c["options"]))
        out[STATUS] = {"name": cols[0]["name"] if len(cols) == 1 else "Run Status",
                       "pos": STATUS, "kind": "dropdown", "options": opts, "derived": True}
    if platform_column(tab):
        out[PLATFORM] = {"name": "Ran on", "pos": PLATFORM, "kind": "dropdown",
                         "options": ["Coder", "Atlantis"], "derived": True}
    return out

def value_of(tab, row, col):
    if col["pos"] == STATUS:
        return row_status(tab, row)
    if col["pos"] == PLATFORM:
        return row_platform(tab, row)
    return row["values"][col["pos"]]

def blank_label(col):
    name = col["name"].lower()
    if any(w in name for w in ("assign", "owner", "tester")):
        return "Unassigned"
    if col["pos"] == STATUS or "status" in name:
        return "No status"
    if col["pos"] == PLATFORM:
        return "Unknown"
    return "(blank)"

def fault_column(tab):
    """e.g. "Subsystem At Fault": which subsystem caused a failed run."""
    return next((c for c in tab["columns"] if "fault" in _words(c["name"])
                 and c not in status_columns(tab)), None)

def component_column(tab):
    """e.g. "Subsystem Component": a dropdown of "Subsystem - Component" options."""
    return next((c for c in tab["columns"] if c["kind"] == "dropdown"
                 and "component" in _words(c["name"])
                 and any(" - " in o for o in c["options"])), None)

def split_component(value):
    """ "HT - Precision Stepper" → ("HT", "Precision Stepper")."""
    sub, _, part = value.partition(" - ")
    return (sub.strip(), part.strip()) if part else (value.strip(), "")

def wants_pie(col):
    """Dropdowns of set categories like "Why?" or "Failure reason"."""
    return col["kind"] == "dropdown" and bool(_words(col["name"]) & set(PIE_WORDS))

def _default_on(tab, rows, col, purpose):
    """Whether a column gets a filter/chart/pie when no admin has said otherwise."""
    if purpose == "pie":
        return not col.get("derived") and col not in status_columns(tab) and wants_pie(col)
    if col.get("derived"):
        return True
    if col in (fault_column(tab), component_column(tab)):
        return purpose == "filter"                 # charted in the Error Analysis card instead
    if col in (created_date_column(tab), done_date_column(tab), resolved_column(tab)) \
            or "date" in _words(col["name"]):
        return False                               # dates aren't categories (the bug summary uses them)
    if purpose == "chart" and wants_pie(col):
        return False                               # it gets a pie instead of a bar chart
    if col in status_columns(tab) or col["kind"] in ("date", "number", "checkbox"):
        return False
    if purpose == "chart" and col["name"].strip().lower() in CHART_OFF_BY_DEFAULT:
        return False
    if is_version(col):          # only Software Version: "how many runs per software version"
        return "software" in col["name"].lower() and (purpose == "filter" or is_run_tab(tab))
    if looks_random(col):
        return False
    vals = [v.strip() for r in rows for v in parts(col, r["values"][col["pos"]]) if v.strip()]
    distinct = set(vals)
    if col["kind"] == "dropdown":
        return len(distinct) >= 2 or (purpose == "filter" and bool(col["options"]))
    return 2 <= len(distinct) <= 10 and len(vals) >= 4 and len(distinct) <= 0.5 * len(vals)

def column_on(tab, rows, col, purpose, settings=None):
    """purpose: "filter" or "chart". An admin's choice (by column name) wins."""
    prefix = {"filter": FILTER_PREFIX, "chart": CHART_PREFIX, "pie": PIE_PREFIX}[purpose]
    key = prefix + col["name"].strip().lower()
    choice = (settings or {}).get(key)
    if choice in ("on", "off"):
        return choice == "on"
    return _default_on(tab, rows, col, purpose)

def filter_columns(tab, rows, settings=None):
    derived = derived_columns(tab)
    cols = [derived[k] for k in (STATUS, PLATFORM) if k in derived]
    cols += [c for c in tab["columns"] if c not in status_columns(tab)]
    return [c for c in cols if c.get("derived") and c["pos"] == STATUS
            or column_on(tab, rows, c, "filter", settings)]

def pie_columns(tab, rows, settings=None):
    """Columns that get their own pie next to the status donut (e.g. "Why?")."""
    return [c for c in tab["columns"] if c not in status_columns(tab)
            and c["kind"] == "dropdown" and column_on(tab, rows, c, "pie", settings)]

def chart_columns(tab, rows, settings=None, limit=3):
    """Side charts next to the status donut: status broken down by these columns."""
    derived = derived_columns(tab)
    cols = ([derived[PLATFORM]] if PLATFORM in derived else []) + \
        [c for c in tab["columns"] if c not in status_columns(tab)]
    out = [c for c in cols if column_on(tab, rows, c, "chart", settings)]
    return out[:limit] if limit else out

def readonly_tab(tab):
    """Tabs filled in automatically (e.g. from Jira) are shown but not edited."""
    return "automated" in tab["title"].lower()


# ── Google Sheets ─────────────────────────────────────────────────────────────

def _quote(title):
    return "'" + title.replace("'", "''") + "'"

def _a1_col(n):              # 0-based column index → "A", "B", … "AA"
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


class SheetSource:
    def __init__(self, url, creds_path=CREDS_FILE):
        import gspread
        self.url = url
        self.gc = gspread.service_account(filename=creds_path)
        self.book = self.gc.open_by_url(url)

    # ── reading ───────────────────────────────────────────────────────────────

    def fetch(self):
        meta = self.book.fetch_sheet_metadata({
            "fields": "properties.title,sheets(properties(sheetId,title,hidden,index),"
                      "tables(tableId,range,columnProperties))"})
        settings_sheet = next((s for s in meta["sheets"]
                               if s["properties"]["title"] == SETTINGS_TAB), None)
        visible = [s for s in meta["sheets"]
                   if not s["properties"].get("hidden") and s is not settings_sheet]
        grids = {}
        wanted = visible + ([settings_sheet] if settings_sheet else [])
        if wanted:
            got = self.book.fetch_sheet_metadata({
                "includeGridData": "true",
                "ranges": [_quote(s["properties"]["title"]) for s in wanted],
                "fields": "sheets(properties.sheetId,data(rowData(values(formattedValue,hyperlink,"
                          "userEnteredValue/formulaValue,dataValidation/condition))))"})
            for s in got["sheets"]:
                data = s.get("data") or [{}]
                grids[s["properties"]["sheetId"]] = data[0].get("rowData", [])
        tabs = [self._parse_tab(s, grids.get(s["properties"]["sheetId"], [])) for s in visible]
        settings = {}
        if settings_sheet:
            for row in grids.get(settings_sheet["properties"]["sheetId"], [])[1:]:
                vals = [v.get("formattedValue", "").strip() for v in row.get("values", [])]
                if len(vals) >= 2 and vals[0]:
                    settings[vals[0]] = vals[1]
        return {"title": meta["properties"]["title"], "url": self.url,
                "tabs": [t for t in tabs if t["columns"]], "settings": settings,
                "fetched_at": datetime.now().isoformat(timespec="seconds")}

    @staticmethod
    def _parse_tab(sheet, grid):
        props = sheet["properties"]
        cells = [r.get("values", []) for r in grid]

        def cell(r, c):
            return cells[r][c] if r < len(cells) and c < len(cells[r]) else {}

        def text(r, c):
            return cell(r, c).get("formattedValue", "")

        tables = sorted(sheet.get("tables", []),
                        key=lambda t: (t["range"].get("startRowIndex", 0),
                                       t["range"].get("startColumnIndex", 0)))
        columns = []
        if tables:
            t = tables[0]
            rng = t["range"]
            header = rng.get("startRowIndex", 0)
            first, last = header + 1, rng.get("endRowIndex", len(cells))
            left = rng.get("startColumnIndex", 0)
            for cp in t.get("columnProperties", []):
                idx = left + cp.get("columnIndex", 0)
                rule = cp.get("dataValidationRule", {}).get("condition", {})
                columns.append({
                    "name": (cp.get("columnName") or text(header, idx)).strip(),
                    "index": idx,
                    "kind": COLUMN_KINDS.get(cp.get("columnType"), "text"),
                    "options": [v.get("userEnteredValue", "") for v in rule.get("values", [])]
                               if rule.get("type") == "ONE_OF_LIST" else []})
            table_id = t["tableId"]
        else:
            table_id = None
            header = next((r for r in range(len(cells))
                           if any(text(r, c).strip() for c in range(len(cells[r])))), None)
            if header is None:
                return {"id": props["sheetId"], "title": props["title"], "columns": [], "rows": []}
            first = header + 1
            last = max((r + 1 for r in range(first, len(cells))
                        if any(text(r, c).strip() for c in range(len(cells[r])))), default=first)
            for idx in range(len(cells[header])):
                name = text(header, idx).strip()
                if not name:
                    continue
                kind, options = "text", []
                for r in range(first, last):
                    cond = cell(r, idx).get("dataValidation", {}).get("condition", {})
                    if cond.get("type") == "ONE_OF_LIST":
                        kind = "dropdown"
                        options = [v.get("userEnteredValue", "") for v in cond.get("values", [])]
                        break
                    if cond.get("type") == "BOOLEAN":
                        kind = "checkbox"
                        break
                    if cond.get("type", "").startswith("DATE"):
                        kind = "date"
                        break
                columns.append({"name": name, "index": idx, "kind": kind, "options": options})

        for pos, c in enumerate(columns):
            c["pos"] = pos
            c["link"] = "link" in c["name"].lower()      # e.g. "Run Name w/ Link", even before any links
        rows = []
        for r in range(first, last):
            values, links, locked = [], [], []
            for c in columns:
                cd = cell(r, c["index"])
                formula = cd.get("userEnteredValue", {}).get("formulaValue", "")
                values.append(cd.get("formattedValue", ""))
                links.append(cd.get("hyperlink", ""))
                # HYPERLINK() cells are just links; other formulas are calculated, so read-only
                locked.append(bool(formula) and not formula.upper().startswith("=HYPERLINK("))
                if cd.get("hyperlink"):
                    c["link"] = True
            rows.append({"r": r, "values": values, "links": links, "locked": locked})
        return {"id": props["sheetId"], "title": props["title"], "table_id": table_id,
                "header_row": header, "columns": columns, "rows": rows}

    def _fresh_values(self, tab):
        """Current cell text of the whole tab, as a list of rows (0-based sheet rows)."""
        from gspread.utils import ValueRenderOption
        return self.book.get_worksheet_by_id(tab["id"]).get_all_values(
            value_render_option=ValueRenderOption.formatted)

    # ── writing ───────────────────────────────────────────────────────────────

    def save_settings(self, settings):
        """Write the shared settings to the hidden settings tab (made on first use)."""
        import gspread
        try:
            ws = self.book.worksheet(SETTINGS_TAB)
        except gspread.WorksheetNotFound:
            ws = self.book.add_worksheet(SETTINGS_TAB, rows=100, cols=2)
            self.book.batch_update({"requests": [{"updateSheetProperties": {
                "properties": {"sheetId": ws.id, "hidden": True}, "fields": "hidden"}}]})
        rows = [["setting", "value"]] + [[k, v] for k, v in sorted(settings.items())]
        ws.clear()
        ws.update(rows, "A1", value_input_option="RAW")

    @staticmethod
    def _current_layout(tab, fresh):
        """`tab` with each column's sheet index looked up again by its header name,
        so a write lands in the right column even if columns were inserted, moved
        or deleted since the last sync. Values stay keyed by position (`pos`)."""
        names = [c["name"].strip().lower() for c in tab["columns"]]

        def header_map(r):
            cells = [v.strip().lower() for v in fresh[r]] if r < len(fresh) else []
            found, used = {}, set()
            for pos, name in enumerate(names):
                idx = next((i for i, v in enumerate(cells) if v == name and i not in used), None)
                if idx is None:
                    return None
                found[pos] = idx
                used.add(idx)
            return found

        header = tab.get("header_row", 0)
        found = header_map(header)
        if found is None:                              # rows added above the header
            header = next((r for r in range(min(len(fresh), 50)) if header_map(r)), None)
            found = header_map(header) if header is not None else None
        if found is None:
            raise ConflictError("A column in this tab was renamed or deleted since your last "
                                "sync. The latest data has been loaded — please try again.")
        cols = [dict(c, index=found[c["pos"]]) for c in tab["columns"]]
        return dict(tab, columns=cols, header_row=header)

    @staticmethod
    def _cell_data(col, value, link=None):
        value = value if value is not None else ""
        if value == "":
            uev = None
        elif re.fullmatch(r"0|[1-9]\d{0,8}", value.strip()):      # run numbers stay numbers
            uev = {"numberValue": int(value)}
        elif col["kind"] == "date" and parse_date(value):
            uev = {"numberValue": (parse_date(value) - date(1899, 12, 30)).days}
        elif col["kind"] == "number" and re.fullmatch(r"-?[\d,]*\.?\d+", value.strip()):
            uev = {"numberValue": float(value.replace(",", ""))}
        elif col["kind"] == "checkbox" and value.upper() in ("TRUE", "FALSE"):
            uev = {"boolValue": value.upper() == "TRUE"}
        else:
            uev = {"stringValue": value}
        cd = {"userEnteredValue": uev} if uev else {}                 # {} clears the cell
        if link is not None:
            cd["textFormatRuns"] = [{"startIndex": 0, "format": {"link": {"uri": link}}}] \
                if link and value else []
        return cd

    @staticmethod
    def _key(tab, values):
        n = min(2, len(tab["columns"]))
        return tuple((values[c["index"]] if c["index"] < len(values) else "").strip()
                     for c in tab["columns"][:n])

    def _locate(self, tab, row, fresh):
        """Find the sheet row that holds `row` now, even if rows were inserted above it."""
        want = self._key(tab, self._expand(tab, row["values"]))
        if row["r"] < len(fresh) and self._key(tab, fresh[row["r"]]) == want:
            return row["r"]
        first = tab.get("header_row", 0) + 1
        hits = [r for r in range(first, len(fresh)) if self._key(tab, fresh[r]) == want]
        if len(hits) == 1:
            return hits[0]
        raise ConflictError("This row was moved, changed or deleted in the sheet since your "
                            "last sync. The latest data has been loaded — please try again.")

    @staticmethod
    def _expand(tab, values):
        """Row values by column position → a sheet-width list (by sheet column index)."""
        out = [""] * (max(c["index"] for c in tab["columns"]) + 1)
        for c in tab["columns"]:
            out[c["index"]] = values[c["pos"]]
        return out

    def write_cell(self, tab, row, col, new, old, link=None, force=False):
        """Set one cell. `link` is only passed for link cells (text + URL)."""
        fresh = self._fresh_values(tab)
        tab = self._current_layout(tab, fresh)
        col = tab["columns"][col["pos"]]
        r = self._locate(tab, row, fresh)
        current = fresh[r][col["index"]] if col["index"] < len(fresh[r]) else ""
        if current.strip() != old.strip() and not force:
            raise ConflictError("Someone else changed this cell since your last sync.", current)
        self.book.batch_update({"requests": [{"updateCells": {
            "start": {"sheetId": tab["id"], "rowIndex": r, "columnIndex": col["index"]},
            "rows": [{"values": [self._cell_data(col, new, link)]}],
            "fields": "userEnteredValue" + (",textFormatRuns" if link is not None else "")}}]})
        return r

    def add_row(self, tab, values, links):
        """values/links: {column pos: text}. Fills a matching placeholder row
        (e.g. run number 4 with nothing else in it) if there is one, else
        appends a row to the end of the table."""
        fresh = self._fresh_values(tab)
        tab = self._current_layout(tab, fresh)
        cols = tab["columns"]
        first_col = cols[0]
        target = None
        if values.get(0):
            for r in range(tab.get("header_row", 0) + 1, len(fresh)):
                row = fresh[r]
                get = lambda c: row[c["index"]].strip() if c["index"] < len(row) else ""
                if get(first_col) == values[0].strip() and not any(get(c) for c in cols[1:]):
                    target = r
                    break

        def cd(c):
            return self._cell_data(c, values.get(c["pos"], ""),
                                   links.get(c["pos"]) if c["link"] or links.get(c["pos"]) else None)

        if target is not None:
            reqs = [{"updateCells": {
                "start": {"sheetId": tab["id"], "rowIndex": target, "columnIndex": c["index"]},
                "rows": [{"values": [cd(c)]}],
                "fields": "userEnteredValue" + (",textFormatRuns" if (c["link"] or links.get(c["pos"])) else "")}}
                for c in cols[1:] if values.get(c["pos"]) or links.get(c["pos"])]   # first column already matches
            if reqs:
                self.book.batch_update({"requests": reqs})
            return "filled"
        width = max(c["index"] for c in cols) + 1
        # a table's body starts at its first column; a plain tab's rows start at column A
        left = min(c["index"] for c in cols) if tab.get("table_id") else 0
        by_index = {c["index"]: c for c in cols}
        row_cells = [cd(by_index[i]) if i in by_index else {} for i in range(left, width)]
        req = {"rows": [{"values": row_cells}], "fields": "userEnteredValue,textFormatRuns"}
        if tab.get("table_id"):
            req["tableId"] = tab["table_id"]
        else:
            req["sheetId"] = tab["id"]
        self.book.batch_update({"requests": [{"appendCells": req}]})
        return "appended"
