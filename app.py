import datetime as dt

from flask import Flask, jsonify, redirect, render_template, request, url_for

import gcal
import gtasks
import weather
from auth import NotAuthorized
from config import (
    get_calendar_ids,
    get_points_tracking,
    get_task_list_id,
    get_zip_code,
    set_calendar_ids,
    set_points_tracking,
    set_task_list_id,
    set_zip_code,
)

app = Flask(__name__)


def _rfc3339(date_str, end_of_day=False):
    """Turn a YYYY-MM-DD string into an RFC3339 timestamp at the start or
    end of that day, in the host machine's local timezone."""
    local_tz = dt.datetime.now().astimezone().tzinfo
    time_part = dt.time(23, 59, 59) if end_of_day else dt.time(0, 0, 0)
    day = dt.date.fromisoformat(date_str)
    return dt.datetime.combine(day, time_part, tzinfo=local_tz).isoformat()


@app.route("/")
def index():
    return render_template("index.html")


def _settings_context(zip_code=None, zip_error=None):
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

    return {
        "cal_error": cal_error,
        "calendars": calendars,
        "selected": set(get_calendar_ids()),
        "zip_code": zip_code if zip_code is not None else get_zip_code(),
        "zip_error": zip_error,
        "task_lists": task_lists,
        "task_error": task_error,
        "selected_task_list": get_task_list_id(),
        "points_tracking": get_points_tracking(),
    }


@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        selected_ids = request.form.getlist("calendar_id")
        set_calendar_ids(selected_ids or ["primary"])
        return redirect(url_for("settings"))

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
    return redirect(url_for("settings"))


@app.route("/settings/tasks", methods=["POST"])
def settings_tasks():
    task_list_id = request.form.get("task_list_id") or None
    set_task_list_id(task_list_id)
    return redirect(url_for("settings"))


@app.route("/settings/points", methods=["POST"])
def settings_points():
    set_points_tracking(bool(request.form.get("points_tracking")))
    return redirect(url_for("settings"))


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

    return jsonify({"taskLists": task_lists, "defaultId": get_task_list_id()})


@app.route("/api/tasks")
def api_tasks():
    task_list_id = request.args.get("tasklist") or get_task_list_id() or "@default"
    points_enabled = get_points_tracking()

    try:
        tasks = gtasks.get_tasks(task_list_id, points_enabled=points_enabled)
        total_points = gtasks.get_total_points(task_list_id) if points_enabled else None
    except NotAuthorized as e:
        return jsonify({"error": "not_authorized", "message": str(e)}), 401
    except Exception as e:
        return jsonify({"error": "unknown", "message": str(e)}), 500

    return jsonify({"tasks": tasks, "pointsEnabled": points_enabled, "totalPoints": total_points})


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
        task, total_points = gtasks.set_task_completed(task_list_id, task_id, completed, points_enabled=points_enabled)
    except NotAuthorized as e:
        return jsonify({"error": "not_authorized", "message": str(e)}), 401
    except Exception as e:
        return jsonify({"error": "unknown", "message": str(e)}), 500

    return jsonify({"task": task, "totalPoints": total_points})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
