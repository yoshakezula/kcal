import datetime as dt
import platform

from flask import Flask, jsonify, redirect, render_template, request, url_for

import gcal
import gsheets
import gtasks
import weather
from auth import NotAuthorized
from config import (
    get_calendar_ids,
    get_points_log_enabled,
    get_points_log_ids,
    get_points_log_tabs,
    get_points_tracking,
    get_sleep_enabled,
    get_task_list_ids,
    get_ui_scale,
    get_view,
    get_zip_code,
    set_calendar_ids,
    set_points_log_enabled,
    set_points_log_ids,
    set_points_log_tab,
    set_points_tracking,
    set_sleep_enabled,
    set_task_list_ids,
    set_ui_scale,
    set_view,
    set_zip_code,
)

app = Flask(__name__)

# The kiosk runs cursor-free on the Pi (touchscreen), but a visible cursor is
# convenient when running the app on a desktop OS for development.
SHOW_CURSOR = platform.system() == "Windows"


def _rfc3339(date_str, end_of_day=False):
    """Turn a YYYY-MM-DD string into an RFC3339 timestamp at the start or
    end of that day, in the host machine's local timezone."""
    local_tz = dt.datetime.now().astimezone().tzinfo
    time_part = dt.time(23, 59, 59) if end_of_day else dt.time(0, 0, 0)
    day = dt.date.fromisoformat(date_str)
    return dt.datetime.combine(day, time_part, tzinfo=local_tz).isoformat()


@app.route("/")
def index():
    return render_template(
        "index.html",
        ui_scale=get_ui_scale(),
        show_cursor=SHOW_CURSOR,
        sleep_enabled=get_sleep_enabled(),
        view=get_view(),
        saved=request.args.get("saved"),
    )


def _saved_redirect():
    """Saving anything in Settings drops you back on the calendar, where the
    change is visible; the flag just tells that page to confirm the write."""
    return redirect(url_for("index", saved=1))


def _settings_context(zip_code=None, zip_error=None, points_log_error=None):
    try:
        calendars = gcal.list_calendars()
        cal_error = None
    except NotAuthorized as e:
        calendars, cal_error = [], str(e)

    try:
        task_lists = gtasks.list_task_lists()
        task_error = None
    except NotAuthorized as e:
        task_lists, task_error = [], str(e)

    _, log_spreadsheet_id = get_points_log_ids()

    return {
        "cal_error": cal_error,
        "calendars": calendars,
        "selected": set(get_calendar_ids()),
        "zip_code": zip_code if zip_code is not None else get_zip_code(),
        "zip_error": zip_error,
        "task_lists": task_lists,
        "task_error": task_error,
        "selected_task_lists": set(get_task_list_ids()),
        "points_tracking": get_points_tracking(),
        "points_log_enabled": get_points_log_enabled(),
        "points_log_url": gsheets.log_url(log_spreadsheet_id) if log_spreadsheet_id else "",
        "points_log_error": points_log_error,
        "ui_scale": get_ui_scale(),
        "show_cursor": SHOW_CURSOR,
        "sleep_enabled": get_sleep_enabled(),
    }


@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        selected_ids = request.form.getlist("calendar_id")
        set_calendar_ids(selected_ids or ["primary"])
        return _saved_redirect()

    return render_template("settings.html", **_settings_context())


@app.route("/settings/location", methods=["POST"])
def settings_location():
    zip_code = (request.form.get("zip_code") or "").strip()

    zip_error = None
    if not zip_code:
        zip_error = "Enter a zip code."
    else:
        try:
            resolved = weather.resolve_zip(zip_code)
        except Exception as e:
            resolved = None
            zip_error = f"Couldn't look up that zip code: {e}"
        if resolved is None and zip_error is None:
            zip_error = f'"{zip_code}" didn\'t match a location.'

    if zip_error:
        return render_template("settings.html", **_settings_context(zip_code=zip_code, zip_error=zip_error))

    set_zip_code(zip_code)
    return _saved_redirect()


@app.route("/settings/tasks", methods=["POST"])
def settings_tasks():
    set_task_list_ids(request.form.getlist("task_list_id"))
    return _saved_redirect()


@app.route("/settings/points", methods=["POST"])
def settings_points():
    set_points_tracking(bool(request.form.get("points_tracking")))
    return _saved_redirect()


@app.route("/settings/points-log", methods=["POST"])
def settings_points_log():
    """Create the log spreadsheet, or turn logging on and off.

    Creating is a button rather than a paste-the-URL field on purpose: the
    app holds only per-file Drive access, so it can write to a sheet it made
    and to nothing else (see gsheets)."""
    if request.form.get("use_existing"):
        spreadsheet_id = gsheets.parse_spreadsheet_id(request.form.get("spreadsheet"))
        if not spreadsheet_id:
            return render_template(
                "settings.html",
                **_settings_context(points_log_error="That doesn't look like a Google Sheets link or ID."),
            )
        try:
            gsheets.verify_log(spreadsheet_id)
        except (NotAuthorized, gsheets.LogUnavailable) as e:
            return render_template("settings.html", **_settings_context(points_log_error=str(e)))
        except Exception as e:
            return render_template(
                "settings.html",
                **_settings_context(points_log_error=f"Couldn't open that sheet: {e}"),
            )
        # Keep the folder as-is; it only matters when creating. Switching
        # sheets clears the per-list tab map, since tab IDs belong to the
        # spreadsheet they came from -- carrying them over could point a
        # list at an unrelated tab that happens to share an ID.
        folder_id, _ = get_points_log_ids()
        set_points_log_ids(folder_id, spreadsheet_id)
        set_points_log_enabled(True)
        return _saved_redirect()

    if request.form.get("create"):
        folder_id, _ = get_points_log_ids()
        try:
            folder_id, spreadsheet_id, _ = gsheets.create_log(folder_id)
        except NotAuthorized as e:
            return render_template("settings.html", **_settings_context(points_log_error=str(e)))
        except Exception as e:
            return render_template(
                "settings.html",
                **_settings_context(points_log_error=f"Couldn't create the log sheet: {e}"),
            )
        set_points_log_ids(folder_id, spreadsheet_id)
        set_points_log_enabled(True)
        return _saved_redirect()

    set_points_log_enabled(bool(request.form.get("points_log_enabled")))
    return _saved_redirect()


@app.route("/settings/sleep", methods=["POST"])
def settings_sleep():
    set_sleep_enabled(bool(request.form.get("sleep_enabled")))
    return _saved_redirect()


@app.route("/settings/display", methods=["POST"])
def settings_display():
    try:
        scale = float(request.form.get("ui_scale", 1.0))
    except ValueError:
        scale = 1.0
    set_ui_scale(scale)
    return _saved_redirect()


@app.route("/api/weather")
def api_weather():
    try:
        forecast = weather.get_forecast()
    except Exception as e:
        return jsonify({"error": "unknown", "message": str(e)}), 500
    if forecast is None:
        return jsonify({"error": "no_location", "message": "Set a location in Settings"}), 404
    return jsonify({"forecast": forecast})


@app.route("/api/weather/hourly")
def api_weather_hourly():
    date = request.args.get("date")
    if not date:
        return jsonify({"error": "bad_request", "message": "date (YYYY-MM-DD) is required"}), 400

    try:
        hours = weather.get_hourly(date)
    except Exception as e:
        return jsonify({"error": "unknown", "message": str(e)}), 500
    if hours is None:
        return jsonify({"error": "not_found", "message": "No hourly data for that date"}), 404
    return jsonify({"hours": hours})


@app.route("/api/events")
def api_events():
    start = request.args.get("start")
    end = request.args.get("end")
    if not start or not end:
        return jsonify({"error": "bad_request", "message": "start and end (YYYY-MM-DD) are required"}), 400

    try:
        time_min = _rfc3339(start, end_of_day=False)
        time_max = _rfc3339(end, end_of_day=True)
        events = gcal.get_events(time_min, time_max)
    except NotAuthorized as e:
        return jsonify({"error": "not_authorized", "message": str(e)}), 401
    except ValueError:
        return jsonify({"error": "bad_request", "message": "start/end must be YYYY-MM-DD"}), 400
    except Exception as e:
        return jsonify({"error": "unknown", "message": str(e)}), 500

    return jsonify({"events": events})


@app.route("/api/tasklists")
def api_tasklists():
    try:
        task_lists = gtasks.list_task_lists()
    except NotAuthorized as e:
        return jsonify({"error": "not_authorized", "message": str(e)}), 401
    except Exception as e:
        return jsonify({"error": "unknown", "message": str(e)}), 500

    # An empty selection means "show them all", so resolve it here rather
    # than leaving the frontend to know that rule.
    enabled_ids = get_task_list_ids()
    if enabled_ids:
        known = {tl["id"] for tl in task_lists}
        enabled_ids = [i for i in enabled_ids if i in known]
    if not enabled_ids:
        enabled_ids = [tl["id"] for tl in task_lists]

    return jsonify({"taskLists": task_lists, "enabledIds": enabled_ids})


@app.route("/api/tasks")
def api_tasks():
    enabled_ids = get_task_list_ids()
    task_list_id = request.args.get("tasklist") or (enabled_ids[0] if enabled_ids else "@default")
    points_enabled = get_points_tracking()

    try:
        tasks = gtasks.get_tasks(task_list_id, points_enabled=points_enabled)
        total_points = gtasks.get_total_points(task_list_id) if points_enabled else None
    except NotAuthorized as e:
        return jsonify({"error": "not_authorized", "message": str(e)}), 401
    except Exception as e:
        return jsonify({"error": "unknown", "message": str(e)}), 500

    return jsonify({"tasks": tasks, "pointsEnabled": points_enabled, "totalPoints": total_points})


def _log_completion(task_list_id, task, completed, total_points):
    """Append a row to the points log, or quietly do nothing if logging is
    off or the sheet is unreachable. Never raises: a failed log must not
    turn a working task toggle into an error the kiosk shows.

    An un-check logs its own row with the points negated, so the log stays an
    append-only ledger whose Points column sums to the running total."""
    if not get_points_log_enabled():
        return

    _, spreadsheet_id = get_points_log_ids()
    if not spreadsheet_id:
        return

    try:
        list_title = gtasks.get_task_list_title(task_list_id)
        tab_title, sheet_id, is_new = gsheets.resolve_tab(
            spreadsheet_id, task_list_id, list_title, get_points_log_tabs()
        )
        if is_new:
            set_points_log_tab(task_list_id, sheet_id)

        points = task.get("points")
        if points is not None and not completed:
            points = -points

        gsheets.append_completion(spreadsheet_id, tab_title, task["title"], points, total_points)
    except gsheets.LogUnavailable as e:
        app.logger.warning("Points log unavailable: %s", e)
    except Exception as e:
        app.logger.warning("Couldn't write to the points log: %s", e)


@app.route("/api/tasks/toggle", methods=["POST"])
def api_tasks_toggle():
    data = request.get_json(silent=True) or {}
    task_list_id = data.get("tasklist")
    task_id = data.get("task")
    completed = bool(data.get("completed"))

    if not task_list_id or not task_id:
        return jsonify({"error": "bad_request", "message": "tasklist and task are required"}), 400

    points_enabled = get_points_tracking()

    try:
        task, total_points, changed = gtasks.set_task_completed(
            task_list_id, task_id, completed, points_enabled=points_enabled
        )
        if changed:
            _log_completion(task_list_id, task, completed, total_points)
        elif points_enabled:
            # Nothing moved, so the client's optimistic guess was wrong.
            # Send back the real total for it to correct itself with.
            total_points = gtasks.get_total_points(task_list_id)
    except NotAuthorized as e:
        return jsonify({"error": "not_authorized", "message": str(e)}), 401
    except Exception as e:
        return jsonify({"error": "unknown", "message": str(e)}), 500

    return jsonify({"task": task, "totalPoints": total_points, "changed": changed})


@app.route("/api/view", methods=["POST"])
def api_view():
    """Remember which view the kiosk is on, so a restart comes back to it."""
    data = request.get_json(silent=True) or {}
    view = data.get("view")
    if not set_view(view):
        return jsonify({"error": "bad_request", "message": "unknown view"}), 400
    return jsonify({"view": view})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
