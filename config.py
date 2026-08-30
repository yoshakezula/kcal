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
    "task_list_id": None,
}


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


def get_task_list_id():
    return load_config().get("task_list_id") or DEFAULTS["task_list_id"]


def set_task_list_id(task_list_id):
    config = load_config()
    config["task_list_id"] = task_list_id
    save_config(config)
