"""
Change logs between two software versions: what was merged (GitHub PRs) and which
JIRA tickets it touched, grouped like the team's change log:
New features · Improvements · Bug fixes · GUI / UX · Infrastructure & tooling · Release.

How one is made (by the admin):
  1. The commits between the two versions, from a local copy of VitalBio/device-sw's history
     kept by the app (git over your SSH access — no GitHub token needed), with each PR's number
     and title from its merge commit. With a GitHub token, PRs come from the GitHub API instead
     (which also reads PR descriptions for PROD keys).
  2. PROD keys from the PR title, branch, and description (e.g. PROD-3330).
  3. JIRA: one search for those keys → summary, type, priority, status, fix versions.
  4. Each PR gets a category from its tickets' types and from keywords.

The finished change log is saved in the sheet (hidden settings tab, "changelog:<id>"),
so everyone sees it without tokens. Tokens stay in this Mac's Keychain.
"""

import json, re, subprocess, sys
from datetime import datetime

REPO = "VitalBio/device-sw"
JIRA_SITE = "https://vitalbio.atlassian.net"
JIRA_PROJECT = "PROD"
CATEGORIES = ["New features", "Improvements", "Bug fixes", "GUI / UX", "Infrastructure & tooling", "Release"]
CATEGORY_COLORS = {"In QA": "#E87BA4", "New features": "#7F5AF0", "Improvements": "#0E8FB3", "Bug fixes": "#D03B3B",
                   "GUI / UX": "#F28E2B", "Infrastructure & tooling": "#7A7974", "Release": "#0CA30C"}
PREFIX = "changelog:"
CHUNK = 40000                      # characters per settings cell (Sheets allows 50,000)
KEY_RE = re.compile(rf"\b{JIRA_PROJECT}-\d+\b", re.I)
VERSION_RE = re.compile(r"v?\d+(?:\.\d+)+[-_][0-9a-f]{7,40}")


class ChangeLogError(Exception):
    pass


# ── versions in the sheet ─────────────────────────────────────────────────────

def versions_in(data):
    """Versions with a git hash, from any "… Version" column (e.g. v4.19.3-a767250), newest first."""
    found = set()
    for t in data.get("tabs", []):
        for c in t["columns"]:
            if "version" in c["name"].lower():
                for r in t["rows"]:
                    found.update(m.group(0) for m in VERSION_RE.finditer(r["values"][c["pos"]]))
    nat = lambda v: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", v.lower())]
    return sorted(found, key=nat, reverse=True)

def ref_of(version):
    """The git ref to compare: the hash in "v4.19.3-a767250", or whatever was typed."""
    m = re.search(r"[-_]([0-9a-f]{7,40})$", version.strip())
    return m.group(1) if m else version.strip()


# ── tokens (macOS Keychain; a private file elsewhere) ─────────────────────────

SERVICE = "TestTracker"

def get_secret(name):
    if sys.platform == "darwin":
        r = subprocess.run(["security", "find-generic-password", "-s", f"{SERVICE}-{name}", "-w"],
                           capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ""
    import tracker_store as store, os
    try:
        return json.load(open(os.path.join(store.DATA_DIR, "secrets.json"))).get(name, "")
    except (OSError, ValueError):
        return ""

def set_secret(name, value):
    if sys.platform == "darwin":
        if not value:
            subprocess.run(["security", "delete-generic-password", "-s", f"{SERVICE}-{name}"],
                           capture_output=True)
            return
        r = subprocess.run(["security", "add-generic-password", "-U", "-s", f"{SERVICE}-{name}",
                            "-a", name, "-w", value], capture_output=True, text=True)
        if r.returncode != 0:
            raise ChangeLogError(f"Couldn't save to the Keychain: {r.stderr.strip()}")
        return
    import tracker_store as store, os
    path = os.path.join(store.DATA_DIR, "secrets.json")
    try:
        data = json.load(open(path))
    except (OSError, ValueError):
        data = {}
    data[name] = value
    with open(path, "w") as f:
        json.dump(data, f)
    os.chmod(path, 0o600)

def credentials():
    return {"github": get_secret("github-token"), "jira_email": get_secret("jira-email"),
            "jira": get_secret("jira-token")}

def has_credentials():
    """JIRA is required; GitHub works through git over SSH unless a token is set."""
    c = credentials()
    return bool(c["jira_email"] and c["jira"])


# ── git over SSH (no GitHub token) ────────────────────────────────────────────

GIT_URL = f"git@github.com:{REPO}.git"

def _repo_dir():
    import os, tracker_store as store
    return os.path.join(store.DATA_DIR, "repos", REPO.split("/")[-1] + ".git")

def _git(*args, cwd=None, timeout=300):
    import os, shutil
    git = shutil.which("git") or "/usr/bin/git"
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0",
               GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=15")
    try:
        r = subprocess.run([git] + list(args), cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise ChangeLogError("git isn't installed. Install Apple's command line tools: run "
                             "xcode-select --install in Terminal.")
    except subprocess.TimeoutExpired:
        raise ChangeLogError("git took too long (is the network up?).")
    if r.returncode != 0:
        err = (r.stderr or r.stdout).strip()
        if "Permission denied" in err or "Could not read from remote" in err or "Host key" in err:
            raise ChangeLogError(f"GitHub didn't accept your SSH key for {REPO}. In Terminal, check that "
                                 f"git clone {GIT_URL} works.\n\n{err[:300]}")
        raise ChangeLogError(err[:400] or f"git {args[0]} failed")
    return r.stdout

def update_repo(progress=lambda m: None):
    """Clone (history only, no file contents) the first time; fetch after that."""
    import os
    path = _repo_dir()
    if not os.path.isdir(path):
        progress(f"Copying {REPO}'s history (first time only)…")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _git("clone", "--bare", "--filter=blob:none", "--quiet", GIT_URL, path, timeout=900)
    progress(f"Getting the latest from {REPO}…")
    _git("fetch", "--quiet", "--prune", "--tags", "--filter=blob:none", "origin",
         "+refs/heads/*:refs/heads/*", cwd=path, timeout=600)
    return path

def test_git():
    return _git("ls-remote", GIT_URL, "HEAD", timeout=60).split()[0][:7]

def git_entries(base, head, progress=lambda m: None):
    """[(entry, text)] for the changes in head that aren't in base, newest first, from git.
    Squash merges ("Title (#123)") and merge commits ("Merge pull request #123 from …")
    become PRs; other commits are listed as commits."""
    path = update_repo(progress)
    for ref, name in ((base, "From"), (head, "To")):
        try:
            _git("rev-parse", "--verify", "--quiet", ref + "^{commit}", cwd=path)
        except ChangeLogError:
            raise ChangeLogError(f"{name} ({ref}) isn't a commit in {REPO}. Check the version's hash.")
    try:
        _git("merge-base", "--is-ancestor", base, head, cwd=path)
        ahead = True
    except ChangeLogError:
        ahead = False
    if not ahead:
        try:
            _git("merge-base", "--is-ancestor", head, base, cwd=path)
            raise ChangeLogError("To is older than From — swap them.")
        except ChangeLogError as exc:
            if "swap" in str(exc):
                raise
    total = int(_git("rev-list", "--count", f"{base}..{head}", cwd=path).strip() or 0)
    sep, end = "\x1f", "\x1e"
    out = _git("log", "--first-parent", f"--format=%H{sep}%an{sep}%cI{sep}%P{sep}%s{sep}%b{end}",
               f"{base}..{head}", cwd=path)
    items = []
    for rec in out.split(end):
        rec = rec.strip("\n")
        if not rec:
            continue
        sha, author, when, parents, subject, body = (rec.split(sep) + [""] * 6)[:6]
        merge = re.match(r"Merge pull request #(\d+) from [^/\s]+/(\S+)", subject)
        squash = re.search(r"\(#(\d+)\)\s*$", subject)
        if merge:
            number, branch = int(merge.group(1)), merge.group(2)
            title = (body.strip().split("\n", 1)[0].strip() or branch)
        elif squash:
            number, branch, title = int(squash.group(1)), "", subject[:squash.start()].strip()
        else:
            number, branch, title = None, "", subject
        url = (f"https://github.com/{REPO}/pull/{number}" if number else
               f"https://github.com/{REPO}/commit/{sha}")
        e = {"pr": number, "url": url, "title": title, "author": author, "date": when[:10],
             "sha": sha[:7], "branch": branch, "labels": [], "commits": [sha[:7]]}
        items.append((e, " ".join([subject, branch, body])))
    return items, total


# ── GitHub and JIRA ───────────────────────────────────────────────────────────

def _get(url, headers, params=None, auth=None):
    import requests
    try:
        r = requests.get(url, headers=headers, params=params, auth=auth, timeout=20)
    except requests.RequestException as exc:
        raise ChangeLogError(f"Couldn't reach {url.split('/')[2]} ({type(exc).__name__}).")
    if r.status_code == 401:
        raise ChangeLogError(f"{url.split('/')[2]} didn't accept the token. Check it under Tokens…")
    if r.status_code == 404:
        raise ChangeLogError("Not found — check the versions/hashes, and that the token can read "
                             f"{REPO}." if "github" in url else "JIRA said not found.")
    if r.status_code >= 400:
        raise ChangeLogError(f"{url.split('/')[2]} answered {r.status_code}: {r.text[:200]}")
    return r

def _gh(token):
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}

def test_github(token):
    r = _get(f"https://api.github.com/repos/{REPO}", _gh(token))
    return r.json().get("full_name", REPO)

def test_jira(email, token):
    r = _get(f"{JIRA_SITE}/rest/api/3/myself", {"Accept": "application/json"}, auth=(email, token))
    return r.json().get("displayName", email)

def compare(token, base, head, progress=lambda msg: None):
    """Commits in head that aren't in base, oldest first."""
    commits, page = [], 1
    while True:
        progress(f"Reading commits {base}…{head} (page {page})…")
        r = _get(f"https://api.github.com/repos/{REPO}/compare/{base}...{head}", _gh(token),
                 {"per_page": 100, "page": page})
        data = r.json()
        batch = data.get("commits", [])
        commits += batch
        if len(batch) < 100 or len(commits) >= data.get("total_commits", 0) or page >= 30:
            return commits, data.get("total_commits", len(commits)), data.get("status")
        page += 1

def pull_for(token, sha, message):
    """The PR a commit came from: "(#4606)" in a squash-merge message, else ask GitHub."""
    m = re.search(r"\(#(\d+)\)\s*$", message.split("\n", 1)[0]) or re.match(r"Merge pull request #(\d+)", message)
    if m:
        r = _get(f"https://api.github.com/repos/{REPO}/pulls/{m.group(1)}", _gh(token))
        return r.json()
    r = _get(f"https://api.github.com/repos/{REPO}/commits/{sha}/pulls", _gh(token))
    pulls = [p for p in r.json() if p.get("merged_at")]
    return pulls[0] if pulls else None

def jira_issues(email, token, keys):
    """{KEY: {summary, type, priority, status, status_category, fix_versions, labels, components}}"""
    import requests
    out, keys = {}, sorted(set(k.upper() for k in keys))
    for i in range(0, len(keys), 80):
        batch = keys[i:i + 80]
        try:
            r = requests.post(f"{JIRA_SITE}/rest/api/3/search/jql", auth=(email, token), timeout=30,
                              headers={"Accept": "application/json", "Content-Type": "application/json"},
                              json={"jql": f"key in ({', '.join(batch)})", "maxResults": 100,
                                    "fields": ["summary", "issuetype", "priority", "status", "fixVersions",
                                               "labels", "components"]})
        except requests.RequestException as exc:
            raise ChangeLogError(f"Couldn't reach JIRA ({type(exc).__name__}).")
        if r.status_code == 401:
            raise ChangeLogError("JIRA didn't accept the email/token. Check them under Tokens…")
        if r.status_code == 400 and "does not exist" in r.text:
            # one unknown key fails the whole search: retry the batch one by one
            for k in batch:
                try:
                    out.update(jira_issues(email, token, [k]) if len(batch) > 1 else {})
                except ChangeLogError:
                    pass
            continue
        if r.status_code >= 400:
            raise ChangeLogError(f"JIRA answered {r.status_code}: {r.text[:200]}")
        for it in r.json().get("issues", []):
            f = it["fields"]
            out[it["key"]] = {
                "summary": f.get("summary") or "", "type": (f.get("issuetype") or {}).get("name", ""),
                "priority": (f.get("priority") or {}).get("name", ""),
                "status": (f.get("status") or {}).get("name", ""),
                "status_category": ((f.get("status") or {}).get("statusCategory") or {}).get("key", ""),
                "fix_versions": [v["name"] for v in f.get("fixVersions") or []],
                "labels": f.get("labels") or [],
                "components": [c["name"] for c in f.get("components") or []]}
    return out


# ── categories ────────────────────────────────────────────────────────────────

UI_WORDS = r"\b(ui|ux|gui|screen|screens|button|buttons|modal|modals|dialog|display|layout|icon|icons|font|fonts|" \
           r"frontend|front-end|page|pages|theme|style|styles|css|animation|toast|banner|menu|tab|tabs|keyboard|" \
           r"touch|nav|navbar|overlay|glyph|glyphs|figma|svg|pinpad|osk|countdown|tile|tiles|copy text|" \
           r"video asset|dashboard|card|cards)\b"
INFRA_WORDS = r"\b(ci|cd|build|pipeline|docker|deps|dependency|dependencies|lint|linter|linters|tests?|testing|" \
              r"tooling|script|scripts|refactor|chore|infra|infrastructure|github actions|workflow file|" \
              r"terraform|helm|makefile|cmake|bazel|codecov|coverage|pre-?commit|hook|hooks|nix|nixos|flake|" \
              r"integration tests?|flaky|proto|buf|monitor-pr|localdev|hydra)\b"
RELEASE_WORDS = r"(^release\b|\brelease (v|version)?\s*\d|\bbump(ed)? (to )?(the )?version\b|\bversion bump\b|" \
                r"\bchangelog\b|^v?\d+\.\d+\.\d+$)"

def categorize(title, branch, labels, tickets):
    text = " ".join([title, branch.replace("/", " ").replace("-", " ").replace("_", " "), " ".join(labels)]).lower()
    types = {t["type"].lower() for t in tickets}
    tlabels = " ".join(l.lower() for t in tickets for l in t.get("labels", []) + t.get("components", []))
    if re.search(RELEASE_WORDS, title.lower().strip()):
        return "Release"
    if types & {"bug", "defect"} or re.match(r"(fix|bugfix|hotfix)[/_-]", branch.lower()) or \
            (not tickets and re.search(r"\b(fix|fixes|fixed|bug|hotfix)\b", text)):
        return "Bug fixes"
    if re.search(UI_WORDS, text) or re.search(r"\b(ui|ux|gui|frontend)\b", tlabels):
        return "GUI / UX"
    if types & {"story", "feature", "new feature", "epic"}:
        return "New features"
    if re.search(INFRA_WORDS, text) or re.match(r"(chore|ci|build|infra|deps)[/_-]", branch.lower()):
        return "Infrastructure & tooling"
    if not tickets and re.match(r"(feat|feature)[/_-]", branch.lower()):
        return "New features"
    return "Improvements"


# ── making one ────────────────────────────────────────────────────────────────

def generate(from_version, to_version, progress=lambda msg: None):
    creds = credentials()
    if not (creds["jira_email"] and creds["jira"]):
        raise ChangeLogError("Add your JIRA email and token first (Tokens…).")
    base, head = ref_of(from_version), ref_of(to_version)
    if not creds["github"]:                       # no token: git over SSH
        items, total = git_entries(base, head, progress)
        entries = [dict(e, text=text) for e, text in items]
        return _finish(from_version, to_version, base, head, total, entries, creds, progress)
    commits, total, status = compare(creds["github"], base, head, progress)
    if status == "behind":
        raise ChangeLogError(f"{to_version} is older than {from_version} — swap From and To.")
    entries, seen_prs = [], {}
    for k, c in enumerate(commits, start=1):
        progress(f"Finding the PR for commit {k} of {len(commits)}…")
        msg = c["commit"]["message"]
        pr = pull_for(creds["github"], c["sha"], msg)
        if pr and pr["number"] in seen_prs:
            seen_prs[pr["number"]]["commits"].append(c["sha"][:7])
            continue
        if pr:
            e = {"pr": pr["number"], "url": pr["html_url"], "title": pr["title"],
                 "author": (pr.get("user") or {}).get("login", ""),
                 "date": (pr.get("merged_at") or c["commit"]["committer"]["date"])[:10],
                 "sha": c["sha"][:7], "branch": (pr.get("head") or {}).get("ref", ""),
                 "labels": [l["name"] for l in pr.get("labels") or []],
                 "text": " ".join([pr["title"], (pr.get("head") or {}).get("ref", ""), pr.get("body") or ""]),
                 "commits": [c["sha"][:7]]}
            seen_prs[pr["number"]] = e
        else:                                             # a commit pushed straight to the branch
            e = {"pr": None, "url": c["html_url"], "title": msg.split("\n", 1)[0],
                 "author": c["commit"]["author"]["name"], "date": c["commit"]["committer"]["date"][:10],
                 "sha": c["sha"][:7], "branch": "", "labels": [], "text": msg, "commits": [c["sha"][:7]]}
        if (c.get("author") or {}).get("login") and not e["author"]:
            e["author"] = c["author"]["login"]
        entries.append(e)
    return _finish(from_version, to_version, base, head, total, entries, creds, progress)


def _finish(from_version, to_version, base, head, total, entries, creds, progress):
    """PROD tickets from JIRA, categories, and the change log record."""
    keys = {k.upper() for e in entries for k in KEY_RE.findall(e["text"])}
    progress(f"Looking up {len(keys)} PROD ticket{'s' if len(keys) != 1 else ''} in JIRA…")
    issues = jira_issues(creds["jira_email"], creds["jira"], keys) if keys else {}
    for e in entries:
        ks = list(dict.fromkeys(k.upper() for k in KEY_RE.findall(e.pop("text"))))
        e["tickets"] = [dict(issues.get(k, {"summary": "(not found in JIRA)", "type": "", "priority": "",
                                            "status": "", "status_category": "", "fix_versions": [],
                                            "labels": [], "components": []}), key=k,
                             url=f"{JIRA_SITE}/browse/{k}") for k in ks]
        e["category"] = categorize(e["title"], e["branch"], e["labels"], e["tickets"])
    entries.sort(key=lambda e: e["date"], reverse=True)
    entries.sort(key=lambda e: CATEGORIES.index(e["category"]))
    return {"id": f"{from_version}..{to_version}", "repo": REPO, "from": from_version, "to": to_version,
            "base": base, "head": head, "commits": total, "generated": datetime.now().isoformat(timespec="seconds"),
            "entries": entries}

IN_QA = "In QA"

def in_qa(ticket):
    """A JIRA status that means it's waiting on QA ("QA", "In QA", "Ready for QA"…)."""
    return "qa" in re.findall(r"[a-z]+", (ticket.get("status") or "").lower())

def qa_entries(log):
    return [e for e in log["entries"] if any(in_qa(t) for t in e["tickets"])]


def warnings(log):
    """Tickets not Done yet, and fix versions that don't match the release (like the team's notes)."""
    not_done, seen = [], set()
    for e in log["entries"]:
        for t in e["tickets"]:
            if t["key"] not in seen and t.get("status") and t.get("status_category") != "done":
                seen.add(t["key"])
                not_done.append(t)
    m = re.match(r"v?(\d+\.\d+)", log["to"])
    minor = m.group(1) if m else None
    fixes = {v for e in log["entries"] for t in e["tickets"] for v in t.get("fix_versions", [])}
    tagged = bool(minor) and any(minor in v for v in fixes)
    return not_done, (minor if fixes and not tagged else None)


# ── saving in the sheet ───────────────────────────────────────────────────────

def save(settings, log):
    """Settings with this change log added (split over several cells if it's big)."""
    out = {k: v for k, v in settings.items() if not k.startswith(PREFIX + log["id"] + "#")}
    text = json.dumps(log, separators=(",", ":"))
    for i in range(0, max(len(text), 1), CHUNK):
        out[f"{PREFIX}{log['id']}#{i // CHUNK:03d}"] = text[i:i + CHUNK]
    return out

def remove(settings, log_id):
    return {k: v for k, v in settings.items() if not k.startswith(PREFIX + log_id + "#")}

def saved(settings):
    """[change log] from the sheet's settings, newest first."""
    parts = {}
    for k, v in (settings or {}).items():
        if k.startswith(PREFIX) and "#" in k:
            lid, _, n = k[len(PREFIX):].rpartition("#")
            parts.setdefault(lid, []).append((n, v))
    out = []
    for lid, chunks in parts.items():
        try:
            out.append(json.loads("".join(v for _, v in sorted(chunks))))
        except ValueError:
            continue
    return sorted(out, key=lambda l: l.get("generated", ""), reverse=True)
