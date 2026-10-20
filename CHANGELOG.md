# What's new

## 2.2.0
- **📝 Change Log** tab: what changed between two software versions of VitalBio/device-sw — the PRs merged (who, when, hash) and the PROD tickets they touched (summary, type, priority, status, fix version).
  - Tiles for **New features, Improvements, Bug fixes, GUI / UX, Infrastructure & tooling, Release**, and **In QA** (PRs whose JIRA ticket is in QA). Click a tile to filter.
  - **Search** by title, author, PR, PROD key, or hash.
  - A note lists tickets that aren't Done yet, and whether JIRA fix versions match the release.
  - The admin makes change logs (pick From and To — the versions in the sheet, or any git hash); they're saved to the sheet, so everyone sees them with no setup.

## 2.0.1
- **Show blanks** on charts, with a name for them (e.g. *Unassigned*).

## 2.0.0 — the big update

Everything from the 1.2.x test builds, now for everyone.

### Dashboard: edit cards right on the page (1.2.1)
- **✎ Edit** on any card (admin passcode) turns its charts into tiles:
  - **Move** a chart by dragging its ⠿ handle onto another chart.
  - **Resize** a chart by dragging its right edge (or ⋯ → Size).
  - **⋯ menu**: Edit, Duplicate, Start a new row, Show on the dashboard, Include in the export, Remove.
  - **Done editing** saves for everyone; **Cancel** throws the changes away.
- **+ Add chart** with a simple form: *Count* runs / issues / bugs / rows of any tab · *group by* a
  column · *split by* another · *only where* filters · *dates* · *show as* Pie, Bars, Table, or Per day.
  A **live preview** shows the chart before you add it.
- **SQL tab** for anything the form can't do: the whole sheet is a database (a table per tab, plus
  ready-made `runs`, `issues`, `resolutions`, and `bugs` views, and the dashboard's dates as
  `:start` / `:end`). The form writes the SQL for you, so you can start simple and tweak it.
- **Add from gallery** puts back any of the app's own charts in one click.
- **New columns** in the sheet get a note on their card: *add a chart for it?*

### Views (1.2.2)
- **MAIN** is the full dashboard. **+ New view** makes your own page of cards: link cards from MAIN
  (they stay in sync), copy them to change, or start new ones.
- Your views stay on your computer. The admin can **👥 share a view with everyone**.
- Anyone can **make their own copy** of a shared view.

### Export snapshot (1.2.0)
- **Pick what goes in**: tick cards and charts. The defaults match what each card is set to export.
- **Wide layout** (default): cards in balanced columns, landscape-shaped so it shows up large in
  Slack. Columns always end flush, so there are no gaps when you untick something.
- **Arrange the page yourself**: drag cards between columns, drag the dividers to resize, add
  columns, and pick the page width. Your arrangement is remembered.
- **Profiles**: save a whole export setup (dates, layout, view, folder, what's ticked, arrangement)
  and switch between them. **Default** is always there.
- **Export any view**, not only MAIN.
- **Error Analysis**: one combined card for VitalOne + Viking, or split into one per tracker.
- Other layouts: a **Slack summary** (16:9 headline numbers + key charts), **One column**, or
  **One image per card**.
- The PDF matches the page, with clickable runs, bug keys, and notes.

### Error Analysis and bugs
- **Unresolved issues list with notes** under *Unresolved, by subsystem*: run, date, subsystem,
  days open, and the note from the issue's first run. **Click a note** to jump to that cell (in the
  app's table, or in Google Sheets from the PDF).
- **Repeated issues** ("Continued from Run#") now take the **newest** run's diagnosis, including
  *Unknown / TBD* until it's investigated. Notes still come from the first run.
- **Bug Statuses** is split into **VitalOne** and **Viking** sections, in the same card. A bug is
  Viking when its Affects or Fix versions mention Viking.

### Versions
- **⚙ Settings → Versions…** lists every release; pick one to install it (to go back if needed).
- Test builds are published as pre-releases, so they're never offered to everyone automatically.

### Moved or renamed
- *Charts & filters* is now **🔎 Filters** (charts are chosen on each card with ✎ Edit).
- *✎ Customize* is replaced by **✎ Edit** on the card.

---

## 1.1.7 and earlier
See the [GitHub releases](https://github.com/shivthakar-vital/test-tracking/releases).
