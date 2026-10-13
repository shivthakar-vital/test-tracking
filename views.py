"""
Dashboard views: MAIN (every tab's cards, as the admin set them up) plus custom views.

A view is a named page of cards:

    {"id": "a1b2c3d4", "name": "EOD", "cards": [
        {"ref": "VitalOne End-to-End Tracker|analysis"},       # a card from MAIN, kept in sync
        {"tab": "Bug Statuses", "card": {…a card of its own…}}  # a card only this view has
    ]}

Anyone can make views for themselves; they're kept on their computer (config.json).
The admin can share a view with everyone: it's then stored in the sheet's hidden
settings tab ("view:<id>") and shows up for all users. Shared views are changed only
with the admin passcode; anyone can duplicate one into their own views.
"""

import copy, json

import layout as lay
import tracker_store as store

MAIN = "main"
PREFIX = "view:"


def new_view(name):
    return {"id": lay.new_id(), "name": name.strip() or "New view", "cards": []}

def shared(settings):
    out = []
    for k, v in (settings or {}).items():
        if k.startswith(PREFIX):
            try:
                view = json.loads(v)
                if isinstance(view, dict) and view.get("id"):
                    view["shared"] = True
                    out.append(view)
            except ValueError:
                pass
    return sorted(out, key=lambda v: v.get("name", "").lower())

def private(cfg, url):
    views = cfg.setdefault("views", {}).setdefault(store.sheet_id(url) or url, [])
    for v in views:
        v["shared"] = False
    return views

def all_views(cfg, url, settings):
    return shared(settings) + private(cfg, url)

def find(cfg, url, settings, view_id):
    return next((v for v in all_views(cfg, url, settings) if v["id"] == view_id), None)

def dump(view):
    return json.dumps({k: v for k, v in view.items() if k != "shared"}, separators=(",", ":"))

def copy_view(view):
    return copy.deepcopy(view)


def entries(view, data, layout_of):
    """[(tab, card, layout, entry)] for a view's cards, skipping ones whose tab or card
    is gone. A linked card (ref) comes from MAIN, as it is now."""
    tabs = {t["title"]: t for t in data.get("tabs", [])}
    out = []
    for e in view.get("cards", []):
        if "ref" in e:
            title, _, cid = e["ref"].rpartition("|")
            tab = tabs.get(title)
            if not tab:
                continue
            lo = layout_of(tab)
            card = next((c for c in lo["cards"] if c["id"] == cid), None)
            if card:
                out.append((tab, card, lo, e))
        else:
            tab = tabs.get(e.get("tab"))
            card = e.get("card")
            if tab and card:
                lay._clean({"cards": [card]})
                out.append((tab, card, {"cards": [card], "columns": [], "custom": True, "own": True}, e))
    return out

def main_cards(data, layout_of):
    """[(label, ref)] for every card on MAIN, to add to a view."""
    out = []
    for tab in data.get("tabs", []):
        for card in layout_of(tab)["cards"]:
            out.append((lay.card_title(tab, card), f"{tab['title']}|{card['id']}"))
    return out
