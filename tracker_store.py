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

import json, os, re, shutil, sys
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

DEFAULTS = {"sheets": [], "current": "", "show_empty_rows": False}


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

def parse_date(text):
    text = (text or "").strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%d %b %Y", "%b %d, %Y", "%m/%d"):
        try:
            d = datetime.strptime(text, fmt).date()
            return d.replace(year=date.today().year) if fmt == "%m/%d" else d
        except ValueError:
            pass
    return None

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

def group_columns(tab, rows, limit=None):
    """Columns worth filtering and charting by: dropdowns, plus short text
    columns that repeat (System, Assignee…), but not IDs or free-text notes."""
    status = status_column(tab)
    out = []
    for c in tab["columns"]:
        if c is status or c["kind"] in ("date", "number") or "note" in c["name"].lower():
            continue
        vals = [v for r in rows for v in parts(c, r["values"][c["pos"]]) if v.strip()]
        distinct = {v for v in vals}
        if c["kind"] == "dropdown":
            ok = len(distinct) >= 2 or (c["options"] and not limit)
        else:
            ok = 2 <= len(distinct) <= 12 and len(distinct) <= 0.6 * len(vals)
        if ok:
            out.append(c)
    out.sort(key=lambda c: c["kind"] != "dropdown")
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
        visible = [s for s in meta["sheets"] if not s["properties"].get("hidden")]
        grids = {}
        if visible:
            got = self.book.fetch_sheet_metadata({
                "includeGridData": "true",
                "ranges": [_quote(s["properties"]["title"]) for s in visible],
                "fields": "sheets(properties.sheetId,data(rowData(values(formattedValue,hyperlink,"
                          "userEnteredValue/formulaValue,dataValidation/condition))))"})
            for s in got["sheets"]:
                data = s.get("data") or [{}]
                grids[s["properties"]["sheetId"]] = data[0].get("rowData", [])
        tabs = [self._parse_tab(s, grids.get(s["properties"]["sheetId"], [])) for s in visible]
        return {"title": meta["properties"]["title"], "url": self.url,
                "tabs": [t for t in tabs if t["columns"]],
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
