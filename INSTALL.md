# Install Test Tracker on your Mac

Setup takes about 2 minutes. You'll need a Mac with Apple Silicon (M1 or newer), which is
any Mac from late 2020 onwards. To check, open the  Apple menu → **About This Mac** and
look for **Chip: Apple M…**.

## Step 1: Install the app

1. Open **Terminal**: press **⌘ Space**, type `Terminal`, and press **Return**.
2. Copy this line, paste it into Terminal, and press **Return**:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/shivthakar-vital/test-tracking/main/install.sh | bash
   ```

3. When it says **✓ Installed**, Test Tracker opens automatically. You can close Terminal.

From now on, open it like any other app: press **⌘ Space**, type `Test Tracker`, and press
**Return**.

## Step 2: Connect to the team's sheet

The first time it opens, the app asks for the sheet.

1. Ask **Shiv** for:
   - the **Google Sheet link** for the release
   - the **key file**, a small `.json` file. *If you already use Qave Inventory, skip this:
     the app picks up its key by itself.*
2. Paste the link and click **Add**.
3. If you were sent a key file, click **Choose key file…** and pick it.
4. Click **Save**.

The dashboard loads, and the bottom-right corner shows **● Google Sheets · synced**.

> 🔒 Keep the key file private. Don't post it in public channels or email it outside the
> company. Anyone who has it can edit the sheet.

## Updating

When a new version is out, a blue **⬆ Update to v…** button appears in the bottom-right
corner. Click it: the app closes, updates, and reopens by itself.

You can also update by running the Step 1 line again.

---

## Other ways to install

<details>
<summary><b>Download it instead of using Terminal</b></summary>

1. Go to the [latest release](https://github.com/shivthakar-vital/test-tracking/releases/latest)
   and download **Test-Tracker-mac.zip**.
2. Double-click the zip, then drag **Test Tracker** into your **Applications** folder.
3. Open it. macOS warns that it *"can't be opened"* or that *Apple could not verify it*,
   because the app isn't registered with Apple. Click **Done**.
4. Open **System Settings → Privacy & Security**, scroll down to
   *"Test Tracker was blocked…"*, click **Open Anyway**, and enter your Mac password.

You only need to do this once. The Terminal method above skips this warning.

</details>

<details>
<summary><b>Intel Mac</b></summary>

The ready-made app only runs on Apple Silicon. On an Intel Mac, build it yourself with
Python 3: download the code, then run `./build_mac.sh --install` in Terminal from that folder.

</details>

## Troubleshooting

| Problem | Fix |
|---------|-----|
| "Couldn't connect to Google Sheets" | Check your internet connection. Then open ⚙ Settings and check the link and key file. |
| "No permission to open the Google Sheet" | Tell Shiv. The sheet needs to be shared with the key's service account as an Editor. |
| "Changed by someone else" | Someone edited the same cell since your last sync. Choose whether to keep theirs or replace it with yours. |
| Can't edit a tab | Tabs filled in automatically (like *Automated Bug Statuses*) are read-only. Cells with formulas are too. |
| App won't open after downloading the zip | Follow step 4 of *Download it instead of using Terminal* above. |
| Want to uninstall | Drag **Test Tracker** from Applications to the Trash. To also remove its settings, delete `~/Library/Application Support/TestTracker`. |

---

### For Shiv: publishing a new version

1. In `test_tracker.py`, bump the version near the top:

   ```python
   APP_VERSION = "1.0.1"
   ```

2. Commit that change, push it to `main`, then push a matching tag:

   ```bash
   git tag v1.0.1
   ```

   ```bash
   git push origin v1.0.1
   ```

GitHub builds and publishes the app in about 3 minutes. If the tag doesn't match
`APP_VERSION`, the build stops with an error and nothing is published. Everyone sees the
**⬆ Update** button the next time they open the app (or within 6 hours if it's already open).
