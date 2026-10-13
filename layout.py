"""
Card layouts: what each dashboard card shows, in what order, and which of it goes
into the export. The dashboard and the export are both drawn from these, so a
change made in ✎ Customize shows up in the same card in both.

A tab's layout is a list of cards; each card is a list of blocks (charts, lists…):

    {"cards": [{"id": "main", "title": "", "dash": True, "export": True, "combine": False,
                "blocks": [{"id": "…", "type": "pie", "column": "Reason for Error Runs",
                            "title": "", "dash": True, "export": False, "new_row": False,
                            "count": "issues"}, …]}, …],
     "columns": ["Run Number", "Date", …]}         # columns that existed when it was saved

Columns are referred to by header name (case-insensitive), so moving or inserting
columns in the sheet doesn't break anything. "@status" and "@platform" are the
app's derived columns (the row's status; Coder vs Atlantis). Layouts an admin
saves are stored in the sheet's hidden settings tab ("layout:<tab name>") and
apply to everyone. Tabs nobody has customized get the built-in default, which is
what the app showed before layouts existed.
"""

import copy, json, re, uuid

import tracker_store as store

LAYOUT_PREFIX = "layout:"
STATUS_REF, PLATFORM_REF = "@status", "@platform"

# type → (menu label, needs a column, one-line help)
BLOCK_TYPES = {
    "status":       ("Status donut",                    False, "Share of rows in each status"),
    "pie":          ("Pie chart",                       True,  "Share of each value in a column"),
    "bars":         ("Status bars, by column",          True,  "One bar per value, split by status"),
    "count":        ("Count bars",                      True,  "One bar per value, with its count"),
    "tally":        ("Tally, grouped by subsystem",     True,  "Counts per value, grouped by the part "
                                                               "before “ - ” (e.g. IA - Coils)"),
    "days":         ("Per day",                         True,  "Rows per day for a date column, by status"),
    "list":         ("List of rows",                    False, "A table of the rows, with chosen columns"),
    "bug_summary":  ("Bug summary tiles",               False, "Open now / New / Closed / In QA"),
    "bug_list":     ("Bug list",                        False, "New, closed, or in-QA bugs"),
    "resolved":     ("Resolved vs not resolved",        False, "With days to resolve and a summary"),
    "by_subsystem": ("Resolved / unresolved, by subsystem", False, "Tally of issues by subsystem"),
    "sql":          ("Query chart",                     False, "Any SQL query, drawn as a pie, bars, "
                                                               "per day, or a table"),
}
# chart types offered for a column, in menu order
COLUMN_TYPES = ["pie", "bars", "count", "tally", "days"]
HIDDEN = "hidden"


# ── columns ───────────────────────────────────────────────────────────────────

def col_ref(col):
    if col.get("derived"):
        return "@" + col["pos"]
    return col["name"].strip()

def find_column(tab, ref):
    """The column a block refers to (by header name), or None if it's gone."""
    if not ref:
        return None
    if ref.startswith("@"):
        return store.derived_columns(tab).get(ref[1:])
    key = ref.strip().lower()
    return next((c for c in tab["columns"] if c["name"].strip().lower() == key), None)

def column_label(tab, ref):
    col = find_column(tab, ref)
    return col["name"] if col else ref

def all_columns(tab):
    """Columns a block can use: the derived ones first, then the sheet's."""
    derived = store.derived_columns(tab)
    return [derived[k] for k in (store.STATUS, store.PLATFORM) if k in derived] + list(tab["columns"])


# ── blocks ────────────────────────────────────────────────────────────────────

def new_id():
    return uuid.uuid4().hex[:8]

def block(type_, column=None, dash=True, export=False, **extra):
    b = {"id": new_id(), "type": type_, "column": column, "title": "",
         "dash": dash, "export": export, "new_row": False}
    b.update(extra)
    return b

def suggest(tab, col):
    """A sensible chart for a column someone just added: pies for categories
    like “Reason”, a tally for “IA - Coils”-style components, per day for
    dates, bars for other dropdowns. None for free text (IDs, notes…)."""
    ref = col_ref(col)
    if col is store.component_column(tab):
        return block("tally", ref, count="issues")
    if col["kind"] == "date":
        return block("days", ref, new_row=True)
    if store.wants_pie(col):
        return block("pie", ref, count="rows" if store.is_workflow(col) else "issues")
    if col["kind"] == "dropdown" or col.get("derived"):
        return block("bars", ref)
    vals = {v.strip() for r in tab["rows"] for v in store.parts(col, r["values"][col["pos"]]) if v.strip()}
    if 2 <= len(vals) <= 12 and not store.looks_random(col):
        return block("count", ref)
    return None

def auto_title(tab, b):
    """The title a block gets when nobody typed one."""
    t, ref = b["type"], b.get("column")
    col = find_column(tab, ref) if ref else None
    name = col["name"] if col else (ref or "")
    status = store.derived_columns(tab).get(store.STATUS)
    sname = status["name"] if status else "Status"
    if t == "status":
        return sname + (" (all, as of now)" if store.is_bug_tab(tab) else "")
    if t == "pie":
        return name
    if t == "bars":
        if ref == PLATFORM_REF:
            return "Runs on Coder vs Atlantis"
        if col and store.is_version(col) and store.is_run_tab(tab):
            return f"Runs per {name}"
        return f"{sname} by {name}"
    if t == "count":
        return f"{'Runs' if store.is_run_tab(tab) else 'Rows'} per {name}"
    if t == "tally":
        return f"{name} tally, by subsystem"
    if t == "days":
        return f"Per day ({name})"
    if t == "list":
        return "Runs" if store.is_run_tab(tab) else "Rows"
    if t == "bug_summary":
        return "New and closed"
    if t == "bug_list":
        return {"new": "New bugs", "closed": "Closed bugs", "qa": "In QA"}.get(b.get("which"), "Bugs")
    if t == "resolved":
        return "Resolved Issues from Failed Runs" if store.is_run_tab(tab) else "Resolved"
    if t == "sql":
        return b.get("title") or "Query"
    if t == "by_subsystem":
        return "Resolved, by subsystem" if b.get("which") == "resolved" else "Unresolved, by subsystem"
    return name

def describe(tab, b):
    """One line for the editor, e.g. “Pie chart · Reason for Error Runs”."""
    label = BLOCK_TYPES.get(b["type"], (b["type"],))[0]
    if b.get("column") and BLOCK_TYPES.get(b["type"], ("", False))[1]:
        label += " · " + column_label(tab, b["column"])
    return label


# ── built-in defaults (what the app showed before layouts existed) ────────────

def _has_data(tab, rows, col):
    return any(store.value_of(tab, r, col).strip() for r in rows)

def default_layout(tab, settings=None):
    rows = [r for r in tab["rows"] if not store.is_blank(r)]
    run, bug, ticket = store.is_run_tab(tab), store.is_bug_tab(tab), store.is_ticket_tab(tab)
    status = store.derived_columns(tab).get(store.STATUS)
    fault, comp, dcol = store.fault_column(tab), store.component_column(tab), store.date_column(tab)
    pies = [c for c in store.pie_columns(tab, rows, settings) if _has_data(tab, rows, c)]

    main = []
    if status:
        main.append(block("status", STATUS_REF, export=run or bug, count="rows"))
    if bug:
        main.append(block("bug_summary", export=True))
    for c in pies:
        wf = store.is_workflow(c)
        main.append(block("pie", col_ref(c), export=run and wf, count="rows" if wf else "issues"))
    first_bar = True
    for c in store.chart_columns(tab, rows, settings, limit=3) if status else []:
        b = block("bars", col_ref(c), export=run and (c["pos"] == store.PLATFORM or
                                                      "software" in c["name"].lower()))
        b["new_row"] = first_bar and bool(pies)
        first_bar = False
        main.append(b)
    if bug:
        for which in ("new", "closed", "qa"):
            main.append(block("bug_list", which=which, export=which != "qa", new_row=which == "new"))
    if dcol and status:
        main.append(block("days", col_ref(dcol), new_row=True))
    if run:
        name = store.run_name_column(tab)
        cols = [col_ref(dcol)] if dcol else []
        cols += [col_ref(name)]
        cols += [PLATFORM_REF] if store.platform_column(tab) else []
        cols += [STATUS_REF]
        wf = next((c for c in pies if store.is_workflow(c)), None)
        reason = next((c for c in pies if not store.is_workflow(c)), None)
        cols += [col_ref(c) for c in (wf, reason, fault) if c]
        main.append(block("list", dash=False, export=True, new_row=True, columns=cols,
                          sort=col_ref(dcol) if dcol else None))
    cards = [{"id": "main", "title": "", "dash": True, "export": run or bug, "combine": False,
              "blocks": main}]
    cards[0]["split"] = default_split(tab, cards[0])

    if fault or comp:
        an = [block("pie", col_ref(c), export=True, count="issues")
              for c in pies if not store.is_workflow(c)]
        if fault:
            an.append(block("pie", col_ref(fault), export=True, count="issues", colors="subsystem"))
        if comp:
            an.append(block("tally", col_ref(comp), export=True, count="issues", new_row=True,
                            hide_zero="export"))
        if ticket:
            an.append(block("status", STATUS_REF, count="issues", new_row=True))
        else:
            an.append(block("resolved", export=True, new_row=True))
        an.append(block("by_subsystem", which="resolved", new_row=True))
        an.append(block("by_subsystem", which="unresolved", export=not ticket))
        cards.append({"id": "analysis", "title": "", "dash": True, "export": run, "combine": run,
                      "blocks": an})
    for card in cards:                 # stable ids, so export choices stick between runs
        seen = {}
        for b in card["blocks"]:
            base = f"{b['type']}:{(b.get('column') or b.get('which') or '').strip().lower()}"
            seen[base] = seen.get(base, 0) + 1
            b["id"] = base if seen[base] == 1 else f"{base}#{seen[base]}"
    return {"cards": cards, "columns": [c["name"].strip() for c in tab["columns"]], "custom": False}

# ── splitting a card into sections ────────────────────────────────────────────
# A card can be drawn as labelled sections of its rows, all in the same card. Built in:
# bugs whose Affects or Fix versions mention Viking go under "Viking", the rest under
# "VitalOne".

SPLITS = {"viking": "VitalOne / Viking (Affects or Fix versions mention Viking)"}

def _version_columns(tab):
    words = lambda c: store._words(c["name"])
    return [c for c in tab["columns"] if words(c) & {"version", "versions"}
            and words(c) & {"affects", "affected", "fix", "fixed"}]

def can_split(tab, how):
    return how == "viking" and bool(_version_columns(tab))

def default_split(tab, card):
    return "viking" if card.get("id") == "main" and store.is_bug_tab(tab) and can_split(tab, "viking") else None

def split_groups(tab, card):
    """[(section name, row test)] for a split card, or None."""
    how = card.get("split")
    if not how or not can_split(tab, how):
        return None
    cols = _version_columns(tab)
    viking = lambda r: any("viking" in r["values"][c["pos"]].lower() for c in cols)
    return [("VitalOne", lambda r: not viking(r)), ("Viking", viking)]


def gallery(tab):
    """[(menu label, new block)]: the app's own charts for this tab, to add back in a click."""
    out, seen = [], set()
    for card in default_layout(tab)["cards"]:
        for b in card["blocks"]:
            kind = {"status": "donut", "pie": "pie", "bars": "bars", "count": "bars", "tally": "tally",
                    "days": "per day", "list": "table", "bug_summary": "tiles", "bug_list": "list",
                    "resolved": "donut and days", "by_subsystem": "tally with notes"
                    if b.get("which") != "resolved" else "tally"}.get(b["type"], "chart")
            label = f"{auto_title(tab, b)}  ({kind})"
            if label in seen:
                continue
            seen.add(label)
            nb = copy.deepcopy(b)
            nb.update(id=new_id(), dash=True, new_row=False)
            out.append((label, nb))
    return out


def card_title(tab, card):
    if card.get("title"):
        return card["title"].replace("{tab}", tab["title"])
    if card["id"] == "analysis":
        return f"{tab['title']} — " + ("Analysis" if store.is_ticket_tab(tab) else "Error Analysis")
    return tab["title"]


# ── saved layouts ─────────────────────────────────────────────────────────────

def key_for(tab):
    return LAYOUT_PREFIX + tab["title"].strip().lower()

def _clean(layout):
    """Fill in anything missing (layouts saved by older versions, hand edits)."""
    for card in layout.get("cards", []):
        card.setdefault("id", new_id())
        card.setdefault("title", "")
        card.setdefault("dash", True)
        card.setdefault("export", False)
        card.setdefault("combine", False)
        blocks = []
        for b in card.get("blocks", []):
            if b.get("type") not in BLOCK_TYPES:
                continue
            for k, v in (("id", new_id()), ("title", ""), ("dash", True), ("export", False),
                         ("new_row", False), ("column", None)):
                b.setdefault(k, v)
            blocks.append(b)
        card["blocks"] = blocks
    layout.setdefault("columns", [])
    return layout

def layout_for(tab, settings=None):
    """The admin's saved layout for this tab, or the built-in default."""
    raw = (settings or {}).get(key_for(tab))
    if raw:
        try:
            saved = json.loads(raw)
            if isinstance(saved, dict) and saved.get("cards"):
                saved["custom"] = True
                saved = _clean(saved)
                for card in saved["cards"]:          # layouts saved before splits existed
                    card.setdefault("split", default_split(tab, card))
                return saved
        except ValueError:
            pass                       # a broken setting falls back to the default
    return default_layout(tab, settings)

def dump(layout):
    out = {k: v for k, v in layout.items() if k != "custom"}
    return json.dumps(out, separators=(",", ":"))

def new_columns(tab, layout, dismissed=()):
    """Columns added to the sheet since the layout was saved (or since the app first
    saw this tab), that nobody has chosen a chart for yet."""
    known = {n.strip().lower() for n in layout.get("columns", [])} | {d.lower() for d in dismissed}
    used = {(b.get("column") or "").strip().lower() for c in layout["cards"] for b in c["blocks"]}
    used |= {x.strip().lower() for c in layout["cards"] for b in c["blocks"] for x in b.get("columns", [])}
    return [c for c in tab["columns"]
            if c["name"].strip().lower() not in known and c["name"].strip().lower() not in used
            and c["name"].strip()]

def copy_layout(layout):
    return copy.deepcopy(layout)
