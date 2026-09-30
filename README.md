# Test Tracker

**[⬇ Install](#install)** · [Step-by-step guide](INSTALL.md) · [Latest release](https://github.com/shivthakar-vital/test-tracking/releases/latest)

A desktop dashboard for software release testing. It reads the team's test-tracking
**Google Sheet**, shows where testing stands at a glance, and lets you update cells, which
are written straight back to the sheet.

![icon](assets/icon.png)

## Features

- **Dashboard** with a card per tab (percent done, with a bar split by status) and, for each
  tab:
  - a **status donut** with counts and percentages
  - **status by group** bars, e.g. by *System in Formal Test*, *Assignee*, *Equipment*, or
    *Workflow Type*
  - **runs per day**, for tabs with a date column
  - Hover over any chart for details. **Click a slice or bar** to jump to exactly those rows
- **Date filter** at the top of the dashboard: *Today*, *Yesterday*, *Last 7 / 30 days*,
  *This month*, *On a date…*, or a *Date range…*. It narrows every chart and card for tabs that
  have a date column (e.g. the end-to-end run trackers); other tabs show everything. Clicking
  a chart opens the table with the same dates applied. Your choice is remembered.
- **⬇ Export snapshot**: pick a date range (Today by default) and save a one-page summary
  as a **PNG** (to paste into Slack or email) and a matching **PDF** (where run names and bug
  keys are clickable links). It covers:
  - **End-to-end runs**: status donut and Coder vs Atlantis for each run tracker, plus the list
    of VitalOne runs with their Atlantis links
  - **Error Analysis**: the reason and subsystem pies, and a tally of the components that had
    runs
  - **Bugs**: status of all bugs, and the bugs **closed in the date range** (from the bug tab's
    *Done Date* column), highlighted in green
- **One tab per sheet tab**, with:
  - **Search** across every column
  - **Filters** for dropdown and grouping columns (*Status ▾*, *Equipment ▾*…). Tick the values
    to show
  - **Sorting**: click a column header, and click again to reverse. Status sorts by
    progress, dates sort by date, and ticket numbers sort as numbers (PROD-2 before PROD-10)
  - **Wrap text** (on by default) shows long titles, paths and notes on several lines. Turn it off for a compact view
  - **Show empty rows** to reveal placeholder rows (e.g. run numbers with nothing filled in yet)
- **Edit in the app, saved to the sheet**: double-click a cell.
  - Dropdown columns show the sheet's own dropdown options, with *Select several…* for
    multi-select cells like `SMOKE, DRY`
  - Date columns get a calendar
  - Link cells (PROD tickets, run links) open on double-click. Change their text or URL with
    **Edit cell…** or by right-clicking
  - Notes open in a larger text box
- **+ Add row**: a form with the right control for each column. On run trackers it
  suggests the next run number, and fills the empty placeholder row for that number if
  there is one
- **Safe with several people editing**: before saving, the app checks that the cell still
  holds what you saw. If someone changed it meanwhile, it asks before replacing it. If rows
  were added or moved, it still finds the right row
- **Syncs every minute** (or click **↻ Sync**, ⌘R). If the connection drops, it keeps
  showing the last synced data
- **One-click updates**: a **⬆ Update** button appears when a new version is released

## How it adapts to new releases

Nothing about a particular release is built into the app. It reads the sheet's structure
every time it syncs:

- **Every visible tab** becomes a tab in the app. **Hidden tabs are ignored.**
- If a tab is a Google Sheets **Table** (*Format → Convert to table*), the app uses the
  table's **column types**: dropdown options become dropdowns, and date columns become date
  pickers. On plain tabs, the first row is the header, and dropdowns come from data
  validation.
- The **status column** is the dropdown whose name contains "Status" (e.g. *Current
  Status*). If a tab has several (*Coder E2E Status* and *Atlantis Status*), each row's
  status comes from the one that's filled in, preferring the one for the system the run
  was on.
- **End-to-end trackers** (tabs with a *Run Name* column) get a **Ran on** filter and a
  **Coder vs Atlantis** chart. A run named *Coder E2E* ran on Coder; any other name means
  Atlantis. They also get a **Runs per Software Version** chart, newest version first.
- **Filters and charts are strict by default.** Columns of generated or one-off values
  (names, links, IDs, serial numbers, notes, scripts, and version hashes other than
  *Software Version*) get no filter or chart. Other dropdowns and short, repeating text
  columns (like *System* or *Assignee*) do. *Workflow Type* and *Equipment* have filters
  but no charts. An admin can change any of this (see below).
- **Category pies:** dropdown columns named like *Why?*, *Reason*, *Cause*, or *Category*
  get their own donut next to the status donut, with one fixed colour per option. Rows where
  the column is blank are left out. Click a slice to see those rows.
- **Error Analysis card:** a tab with a *… At Fault* column (e.g. *Subsystem At Fault*) or a
  *… Component* dropdown whose options look like `Subsystem - Component` (e.g.
  `HT - Precision Stepper`) gets a second card under its own. It shows the category pies
  (e.g. *Reason for Error Runs*), a **Subsystem At Fault** pie, and a **tally of every
  component grouped by subsystem**, including components with no runs yet. Subsystem
  colours match between the pie and the tally, and blanks are left out. Click anything
  to see those runs.
- Blank cells show as **Unassigned** in assignee columns, and **No status** in status columns.
- Status colors follow meaning (an admin can change them; see *Admin: chart colours*): *Completed / Done / Results* are green, *In Progress*
  blue, *Blocked / Error* red, *Cancelled* orange, *No results* yellow, *Not Started /
  Created* gray, and *Deferred* dark gray.
- Tabs whose name contains **"Automated"** (filled in from Jira) are read-only in the app.

**To start a new release**, do either of these:

1. **New sheet file**: in Google Sheets, *File → Make a copy* (tick *Share it with the same
   people*, so the robot account keeps access), rename it (e.g. *v4.22 Test Tracker*), and
   clear out the old rows. In the app, pick **+ Add another release…** from the **Release**
   dropdown and paste the link. You can switch between releases from that dropdown.
2. **New tab in the same file**: duplicate a tab and rename it. It shows up by itself on the
   next sync.

## Admin: charts & filters

Click **⚙ Settings → 📊 Charts & filters…** (admin passcode) to choose which columns get a
**filter** button on their tab, a bar **chart** on the dashboard, and (for dropdowns) their own
**pie** chart. It lists every column in the
sheet with its built-in default. Choices are matched by column name, so turning off
*Patient Name* turns it off on every tab. They're saved in the hidden settings tab and apply
to everyone. Each tab shows up to 3 side charts.

## Admin: chart colours

Each status gets a colour automatically (see above). To choose your own:

1. Click **⚙ Settings → 🎨 Chart colours…**
2. The first time, set an **admin passcode**. After that, the app asks for it once per session.
3. Click a colour next to any status to change it. **Reset** goes back to the built-in colour.
4. Click **Save for everyone**.

The colours apply to every chart, summary card, and status dot, for everyone using that
release sheet, after their next sync (within a minute). Statuses are matched by name, so
*Blocked* gets the same colour on every tab.

The colours and passcode are saved in a hidden tab of the sheet called
**Test Tracker Settings**, so a release sheet made with *File → Make a copy* keeps them. The
passcode is stored only as a one-way hash.

> The passcode keeps people from changing colours by accident. It isn't strong security:
> anyone who can edit the Google Sheet could also edit the hidden tab.

**Forgot the passcode?** In Google Sheets, open **View → Hidden sheets → Test Tracker
Settings**, delete the `admin_passcode` row, and hide the tab again. The next time you open
🎨 Chart colours, the app asks you to set a new passcode. Your colours are kept.

## Install

**On any Apple Silicon Mac**, paste this into Terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/shivthakar-vital/test-tracking/main/install.sh | bash
```

See **[INSTALL.md](INSTALL.md)** for step-by-step instructions to share with coworkers.

### Building it yourself

`./build_mac.sh --install` builds `Test Tracker.app` from source and copies it to
`/Applications`. Drop `--install` to only build into `dist/`.

To publish a new version for everyone, bump `APP_VERSION` at the top of `test_tracker.py`,
commit, and push a matching tag (e.g. `v1.0.1`). GitHub Actions checks that the two match,
builds the app, and attaches it to a GitHub Release. The install line downloads it, and the
app's **⬆ Update** button installs it.

**Windows:** run `build_windows.bat` on a Windows PC with Python 3. The app ends up in
`dist\Test Tracker\Test Tracker.exe`.

**Run from source (for development):**

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python test_tracker.py
```

## Connect a Google Sheet

The app signs in to Google with a *service account*: a robot Google account that you share
the sheet with. It uses the same kind of key as Qave Inventory. **If Qave Inventory is set
up on this Mac, Test Tracker reuses its key automatically.**

To set up a new one (about 10 minutes):

1. **Create a Google Cloud project** at <https://console.cloud.google.com/>.
2. **Enable the API**: *APIs & Services → Library*, search for **Google Sheets API**, and
   click **Enable**.
3. **Create the service account**: *APIs & Services → Credentials → Create credentials →
   Service account*. Name it and click **Done**.
4. **Download a key**: click the service account, then *Keys → Add key → Create new key →
   JSON*. Keep the file private.
5. **Share the sheet** with the service account's email (e.g.
   `inventory-bot@qave-inventory.iam.gserviceaccount.com`) as an **Editor**.
6. **Configure the app**: click **⚙ Settings**, paste the sheet link, click **Add**, then
   **Choose key file…**, and **Save**.

## Files

| File | What it is |
|------|------------|
| `test_tracker.py` | The app: window, dashboard, tables, editing, updates |
| `tracker_store.py` | Reading and writing the Google Sheet, settings, and the offline cache |
| `charts.py` | The donut, bar, per-day, and tally charts (drawn with Qt, no extra libraries) |
| `export_report.py` | The snapshot export: lays out the page and saves the PNG and PDF |
| `install.sh` | The one-line installer and updater |
| `build_mac.sh` | Builds the `.app` |
| `.github/workflows/release.yml` | Builds and publishes a release when a `v*` tag is pushed |

Settings, the key, and the offline cache live in `~/Library/Application Support/TestTracker`.
