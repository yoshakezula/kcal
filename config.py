"""Load/save the small local settings file (config.json).

Currently the only setting is which calendar IDs to display. Kept in its
own module so app.py and gcal.py don't need to know the file format.
"""

import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

DEFAULTS = {
    "calendar_ids": ["primary"],
    "zip_code": "90008",
    "task_list_ids": [],
    "points_tracking": False,
    "points_log_enabled": False,
    "points_log_folder_id": "",
    "points_log_spreadsheet_id": "",
    "points_log_tabs": {},
    "ui_scale": 1.0,
    "sleep_enabled": False,
    "view": "week3",
}

# The view names the frontend uses; anything else is rejected so a stale or
# hand-edited config can't leave the kiosk on a view that doesn't render.
VIEWS = ("month", "week", "week2", "week3", "list")


def load_config():
    if not os.path.exists(CONFIG_PATH):
        return dict(DEFAULTS)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    merged = dict(DEFAULTS)
    merged.update(data)
    return merged


def save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def get_calendar_ids():
    return load_config().get("calendar_ids", DEFAULTS["calendar_ids"])


def set_calendar_ids(calendar_ids):
    config = load_config()
    config["calendar_ids"] = calendar_ids
    save_config(config)


def get_zip_code():
    return load_config().get("zip_code") or DEFAULTS["zip_code"]


def set_zip_code(zip_code):
    config = load_config()
    config["zip_code"] = zip_code
    save_config(config)


def get_task_list_ids():
    """Which task lists the kiosk shows, as a list of IDs. An empty list
    means "every list the account has" — so the Tasks button is never dead
    just because nothing has been picked yet."""
    config = load_config()
    ids = [i for i in (config.get("task_list_ids") or []) if i]
    if not ids:
        # Migrate the old single-list setting, from before the Tasks popup
        # showed several lists side by side. Dropped once a selection is
        # saved (see set_task_list_ids).
        legacy = config.get("task_list_id")
        return [legacy] if legacy else []
    return ids


def set_task_list_ids(task_list_ids):
    config = load_config()
    config["task_list_ids"] = list(task_list_ids)
    config.pop("task_list_id", None)  # drop the superseded single-list key
    save_config(config)


def get_points_tracking():
    return bool(load_config().get("points_tracking", DEFAULTS["points_tracking"]))


def set_points_tracking(enabled):
    config = load_config()
    config["points_tracking"] = bool(enabled)
    save_config(config)


def get_points_log_enabled():
    """Whether completed tasks are appended to the points log spreadsheet.
    Off by default: the kiosk pulls from main automatically, so this has to
    stay inert on arrival until the sheet has actually been created."""
    return bool(load_config().get("points_log_enabled", DEFAULTS["points_log_enabled"]))


def set_points_log_enabled(enabled):
    config = load_config()
    config["points_log_enabled"] = bool(enabled)
    save_config(config)


def get_points_log_ids():
    """Return (folder_id, spreadsheet_id) for the log, empty strings when it
    hasn't been created yet."""
    config = load_config()
    return (
        config.get("points_log_folder_id") or "",
        config.get("points_log_spreadsheet_id") or "",
    )


def set_points_log_ids(folder_id, spreadsheet_id):
    """Record a freshly created log. The per-list tab map belongs to that
    spreadsheet, so pointing at a different one clears it."""
    config = load_config()
    if spreadsheet_id != config.get("points_log_spreadsheet_id"):
        config["points_log_tabs"] = {}
    config["points_log_folder_id"] = folder_id
    config["points_log_spreadsheet_id"] = spreadsheet_id
    save_config(config)


def get_points_log_tabs():
    """Map of task list ID -> numeric sheet (tab) ID within the log
    spreadsheet. Numeric IDs survive a tab being renamed; titles don't."""
    tabs = load_config().get("points_log_tabs") or {}
    return {str(k): v for k, v in tabs.items() if isinstance(v, int)}


def set_points_log_tab(task_list_id, sheet_id):
    config = load_config()
    tabs = dict(config.get("points_log_tabs") or {})
    tabs[str(task_list_id)] = int(sheet_id)
    config["points_log_tabs"] = tabs
    save_config(config)


def get_ui_scale():
    try:
        scale = float(load_config().get("ui_scale", DEFAULTS["ui_scale"]))
    except (TypeError, ValueError):
        return DEFAULTS["ui_scale"]
    return min(max(scale, 0.7), 1.8)


def set_ui_scale(scale):
    config = load_config()
    config["ui_scale"] = min(max(float(scale), 0.7), 1.8)
    save_config(config)


def get_view():
    view = load_config().get("view")
    return view if view in VIEWS else DEFAULTS["view"]


def set_view(view):
    """Persist the last-used view. Returns False (saving nothing) if the
    name isn't one the frontend knows."""
    if view not in VIEWS:
        return False
    config = load_config()
    config["view"] = view
    save_config(config)
    return True


def get_sleep_enabled():
    return bool(load_config().get("sleep_enabled", DEFAULTS["sleep_enabled"]))


def set_sleep_enabled(enabled):
    config = load_config()
    config["sleep_enabled"] = bool(enabled)
    save_config(config)
