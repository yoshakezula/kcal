"""Thin wrapper around the Google Calendar API: list calendars, fetch events
across multiple calendars for a date range, and normalize them into a single
flat shape the frontend can render without knowing anything about Google's
event schema.
"""

import datetime

from googleapiclient.discovery import build

from auth import get_credentials

_DEFAULT_COLOR = "#4285F4"  # Google "blue" as a fallback
_EVENT_COLORS_TTL = datetime.timedelta(hours=24)

_event_colors_cache = None
_event_colors_fetched_at = None


def _service():
    return build("calendar", "v3", credentials=get_credentials(), cache_discovery=False)


def _event_colors(service):
    """Map of Google's per-event colorId -> hex background color (the
    "Tomato"/"Basil"/etc. swatches you can assign to an individual event).
    Cached in memory and refreshed once a day, since this palette is
    effectively static but this avoids ever serving a stale value forever."""
    global _event_colors_cache, _event_colors_fetched_at

    now = datetime.datetime.now()
    is_stale = _event_colors_fetched_at is None or (now - _event_colors_fetched_at) > _EVENT_COLORS_TTL
    if _event_colors_cache is None or is_stale:
        response = service.colors().get().execute()
        _event_colors_cache = {
            color_id: info["background"] for color_id, info in response.get("event", {}).items()
        }
        _event_colors_fetched_at = now

    return _event_colors_cache


def list_calendars():
    """Return [{id, summary, color, primary}] for every calendar the user
    has in their calendar list, paging through all results."""
    service = _service()
    calendars = []
    page_token = None
    while True:
        response = (
            service.calendarList()
            .list(pageToken=page_token)
            .execute()
        )
        for entry in response.get("items", []):
            calendars.append(
                {
                    "id": entry["id"],
                    "summary": entry.get("summary", entry["id"]),
                    "color": entry.get("backgroundColor", _DEFAULT_COLOR),
                    "primary": entry.get("primary", False),
                }
            )
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    calendars.sort(key=lambda c: (not c["primary"], c["summary"].lower()))
    return calendars


def _normalize_event(raw_event, calendar_id, calendar_color, calendar_name, event_colors):
    start_info = raw_event.get("start", {})
    end_info = raw_event.get("end", {})
    all_day = "date" in start_info

    # An event's own colorId (if the user picked a one-off color for it)
    # overrides the calendar's default color.
    color = event_colors.get(raw_event.get("colorId"), calendar_color)

    return {
        "id": raw_event.get("id"),
        "calendarId": calendar_id,
        "calendarName": calendar_name,
        "title": raw_event.get("summary", "(No title)"),
        "start": start_info.get("date") or start_info.get("dateTime"),
        "end": end_info.get("date") or end_info.get("dateTime"),
        "allDay": all_day,
        "location": raw_event.get("location", ""),
        "color": color,
    }


def get_events(start_iso, end_iso):
    """Fetch and merge events from every configured calendar between
    start_iso and end_iso (both RFC3339 datetimes), sorted by start time."""
    from config import get_calendar_ids

    service = _service()
    calendars = {c["id"]: c for c in list_calendars()}
    calendar_ids = get_calendar_ids()
    event_colors = _event_colors(service)

    events = []
    for calendar_id in calendar_ids:
        cal_info = calendars.get(calendar_id, {})
        color = cal_info.get("color", _DEFAULT_COLOR)
        calendar_name = cal_info.get("summary", calendar_id)
        page_token = None
        while True:
            try:
                response = (
                    service.events()
                    .list(
                        calendarId=calendar_id,
                        timeMin=start_iso,
                        timeMax=end_iso,
                        singleEvents=True,
                        orderBy="startTime",
                        pageToken=page_token,
                    )
                    .execute()
                )
            except Exception:
                # Skip a calendar we can no longer access rather than
                # failing the whole merged view.
                break

            for raw_event in response.get("items", []):
                if raw_event.get("status") == "cancelled":
                    continue
                events.append(
                    _normalize_event(raw_event, calendar_id, color, calendar_name, event_colors)
                )

            page_token = response.get("nextPageToken")
            if not page_token:
                break

    events.sort(key=lambda e: e["start"] or "")
    return events
