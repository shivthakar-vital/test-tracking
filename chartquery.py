"""
Charts made from SQL queries (see sqldata.py), and the Simple form that writes the SQL.

A query chart is a block {"type": "sql", "sql": "...", "show": "pie|bars|days|table|auto",
"simple": {...form…} or None, "color_by": column, "unit": "runs"}. How its result is drawn:

    label, n              → pie or bars          (n = the last number column)
    label, series, n      → stacked bars
    day, [series,] n      → per day              (day = YYYY-MM-DD)
    anything else         → a table

Columns starting with "_" are hidden. A "_rows" column ("tab:row,tab:row…", e.g.
GROUP_CONCAT(_tab || ':' || _row)) makes slices and bars clickable; tables are clickable
when they have _tab and _row. "<column> link" / "<x>_link" columns turn the matching
column into links.
"""

import re

import sqldata

SHOW = [("pie", "Pie"), ("bars", "Bars"), ("table", "Table"), ("days", "Per day")]
OPS = [("is", "is"), ("is not", "is not"), ("contains", "contains"),
       ("is empty", "is empty"), ("is not empty", "is not empty")]

# what the Simple form can count: key → (label, table for "follow dates", table for all dates,
#                                         date column, kind)
SOURCES = {
    "runs":        ("Runs", "runs", "runs", "run_date", "runs"),
    "issues":      ("Issues (a repeated issue counts once)", "issues", "all_issues", None, "runs"),
    "resolutions": ("Failed issues, resolved or not", "resolutions", "resolutions", None, "runs"),
    "bugs":        ("Bugs", "bugs", "bugs", "created", "bugs"),
}
HIDDEN_COLUMNS = {"_tab", "_row", "tracker", "status_class", "failed", "repeats"}


def sources(db):
    """[(key, label)] for the Simple form's "Count" menu: the views, then each tab."""
    out = [(k, v[0]) for k, v in SOURCES.items() if db.conn.execute(
        f"SELECT COUNT(*) FROM {sqldata.ident(v[1])}").fetchone()[0]]
    return out + [("tab:" + t, f"Rows of {t}") for t in db.tables]

def table_for(spec):
    src = spec.get("source", "runs")
    if src.startswith("tab:"):
        return src[4:]
    s = SOURCES[src]
    return s[1] if spec.get("dates", "follow") != "ignore" else s[2]

def friendly(col):
    """Column names as people say them: run_date → Run date."""
    if col.startswith("_"):
        return col
    if re.fullmatch(r"[a-z_]+", col):
        return col.replace("_", " ").capitalize()
    return col

def columns_for(db, spec):
    cols = db.columns_of(table_for(spec))
    return [c for c in cols if not c.startswith("_") and not c.endswith(" link") and not c.endswith("_link")
            and c not in HIDDEN_COLUMNS]

def date_columns(db, spec):
    src = spec.get("source", "runs")
    if src == "runs":
        return ["run_date"]
    if src == "bugs":
        return ["created", "done"]
    if src == "resolutions":
        return ["first_date", "resolved_date"]
    if src == "issues":
        return ["run_date"]
    import tracker_store as store
    tab = next((t for t in db.data["tabs"] if t["title"] == src[4:]), None)
    return [c["name"].strip() for c in tab["columns"] if c["kind"] == "date"] if tab else []

def lit(v):
    return "'" + str(v).replace("'", "''") + "'"


def to_sql(spec, db):
    """SQL for a Simple form spec."""
    q = sqldata.ident
    table = table_for(spec)
    src = spec.get("source", "runs")
    where = []
    if src in ("runs", "issues", "resolutions"):
        trackers = spec.get("trackers") or []
        if trackers:
            where.append(f"tracker IN ({', '.join(lit(t) for t in trackers)})")
    if src in ("runs", "issues") and spec.get("only_failed"):
        where.append("failed = 1")
    if src == "bugs" and spec.get("product") in ("VitalOne", "Viking"):
        where.append(f"product = {lit(spec['product'])}")
    # dates: issues and resolutions follow the dashboard's dates by their table; runs by
    # run date; bugs by created date (or done date); other tabs by their first date column
    dates, dcol = spec.get("dates", "follow"), None
    if dates == "follow" and src not in ("issues", "resolutions"):
        dcol = (date_columns(db, spec) or [None])[0]
    elif dates not in ("follow", "ignore"):
        dcol = dates
    if dcol:
        where.append(f"{q(dcol)} BETWEEN :start AND :end")
    for col, op, val in spec.get("filters") or []:
        if not col:
            continue
        c = q(col)
        if op == "is":
            where.append(f"{c} = {lit(val)}")
        elif op == "is not":
            where.append(f"({c} IS NULL OR {c} != {lit(val)})")
        elif op == "contains":
            where.append(f"{c} LIKE {lit('%' + str(val) + '%')}")
        elif op == "is empty":
            where.append(f"({c} IS NULL OR {c} = '')")
        elif op == "is not empty":
            where.append(f"({c} IS NOT NULL AND {c} != '')")
    show = spec.get("show", "pie")
    rows_ref = "GROUP_CONCAT(_tab || ':' || _row) AS _rows"

    if show == "table":
        cols = spec.get("columns") or columns_for(db, spec)[:6]
        links = [c for c in db.columns_of(table) if c.endswith(" link") or c.endswith("_link")]
        sel = ", ".join(q(c) for c in cols + [l for l in links if any(l.startswith(c) or
                        l == re.sub(r"_(name|number)$", "", c) + "_link" for c in cols)])
        order = (date_columns(db, spec) or [None])[0]
        sql = f"SELECT {sel}, _tab, _row\nFROM {q(table)}"
        if where:
            sql += "\nWHERE " + "\n  AND ".join(where)
        if order:
            sql += f"\nORDER BY {q(order)} DESC"
        return sql

    if show == "days":
        group = spec.get("date_column") or (date_columns(db, spec) or ["run_date"])[0]
        where.append(f"{q(group)} IS NOT NULL")
        series = spec.get("series")
        sel = f"{q(group)} AS day" + (f", COALESCE(NULLIF({q(series)}, ''), '(blank)') AS series" if series else "")
        grp = "day" + (", series" if series else "")
        sql = f"SELECT {sel},\n       COUNT(*) AS n,\n       {rows_ref}\nFROM {q(table)}"
        sql += "\nWHERE " + "\n  AND ".join(where)
        return sql + f"\nGROUP BY {grp}\nORDER BY day"

    group = spec.get("group") or (columns_for(db, spec) or ["status"])[0]
    series = spec.get("series") if show == "bars" else None
    if show == "pie":                       # pies leave blanks out
        where.append(f"{q(group)} IS NOT NULL AND {q(group)} != ''")
        label = f"{q(group)} AS label"
    else:
        label = f"COALESCE(NULLIF({q(group)}, ''), '(blank)') AS label"
    sel = label + (f", COALESCE(NULLIF({q(series)}, ''), '(blank)') AS series" if series else "")
    grp = "label" + (", series" if series else "")
    sql = f"SELECT {sel},\n       COUNT(*) AS n,\n       {rows_ref}\nFROM {q(table)}"
    if where:
        sql += "\nWHERE " + "\n  AND ".join(where)
    return sql + f"\nGROUP BY {grp}\nORDER BY n DESC"


def unit_for(spec):
    src = spec.get("source", "runs")
    return {"runs": "runs", "issues": "issues", "resolutions": "issues", "bugs": "bugs"}.get(src, "rows")

def default_title(spec, db=None):
    src = spec.get("source", "runs")
    what = {"runs": "Runs", "issues": "Issues", "resolutions": "Failed issues", "bugs": "Bugs"}.get(
        src, src[4:] if src.startswith("tab:") else "Rows")
    show = spec.get("show", "pie")
    if show == "table":
        return what
    if show == "days":
        return f"{what} per day"
    by = friendly(spec.get("group") or "")
    return f"{what} by {by.lower() if by.islower() or '_' in (spec.get('group') or '') else by}" if by else what


# ── results → shape ───────────────────────────────────────────────────────────

_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

def is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)

def shape(cols, rows, show="auto"):
    """How to draw a result: ("pie"|"bars"|"days"|"table", label i, series i or None, value i)."""
    vis = [i for i, c in enumerate(cols) if not c.startswith("_")]
    num = [i for i in vis if rows and all(is_number(r[i]) or r[i] is None for r in rows)]
    value = num[-1] if num else None
    others = [i for i in vis if i != value]
    can_count = value is not None and 1 <= len(others) <= 2
    if show == "table" or not can_count:
        return "table", None, None, None
    label, series = others[0], (others[1] if len(others) == 2 else None)
    is_day = rows and all(r[label] is None or (isinstance(r[label], str) and _DAY.match(r[label])) for r in rows)
    if show == "days" or (show == "auto" and is_day):
        return ("days", label, series, value) if is_day else ("table", None, None, None)
    if show == "pie" and series is None:
        return "pie", label, None, value
    if show == "auto":
        return ("pie" if series is None and len(rows) <= 8 else "bars"), label, series, value
    return "bars", label, series, value
