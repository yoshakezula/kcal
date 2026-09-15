"""Thin wrapper around the Google Tasks API: list task lists, fetch the
tasks within one, and toggle a task's completion status."""

import datetime as dt
import re
import threading

from googleapiclient.discovery import build

from auth import get_credentials

# Matches "5 pts", "5pts", and "5 pts." (case-insensitive) inside a task
# title, but not as a prefix of some other word (e.g. "5ptsomething").
POINTS_PATTERN_RE = re.compile(r"(\d+)\s?pts\.?(?![a-zA-Z])", re.IGNORECASE)

# The hidden bookkeeping task that stores the cumulative points total.
TOTAL_POINTS_TITLE_RE = re.compile(r"^Total Points:\s*(\d+)$")

# Task list titles change rarely but are needed on every toggle (they name
# the tab in the points log), so they're cached rather than refetched.
_TITLE_TTL = dt.timedelta(hours=6)
_title_cache = {}  # task list ID -> (title, fetched_at)

# Tasks and the points total are re-read on every kiosk refresh (once per
# enabled list, every 5 minutes) and again after every toggle, and each read
# is a Google round-trip — the slowest thing the kiosk does. A short TTL
# keeps that off the critical path without letting an edit made in the Tasks
# app elsewhere sit stale for long. Every write below invalidates the list it
# touched, so a toggle still shows up immediately.
_TASKS_TTL = dt.timedelta(seconds=90)
_tasks_cache = {}  # (task list ID, points_enabled) -> (tasks, fetched_at)
_total_cache = {}  # task list ID -> (total, fetched_at)

# Guards the two dicts above, and only those — never held across a Google
# call. The kiosk fetches its lists concurrently, so locking around the
# fetch would serialize them and make a cold refresh N times slower. Two
# callers racing on the same cold key both fetch, which is just the
# uncached behavior and costs nothing but a duplicate request.
_cache_lock = threading.Lock()


def _cache_get(cache, key):
    """Return the cached value for key, or None if absent or expired."""
    with _cache_lock:
        entry = cache.get(key)
    if entry is None:
        return None
    value, fetched_at = entry
    if (dt.datetime.now() - fetched_at) > _TASKS_TTL:
        return None
    return value


def _cache_put(cache, key, value):
    with _cache_lock:
        cache[key] = (value, dt.datetime.now())


def _invalidate(task_list_id):
    """Drop every cached read for one list. Called after any write, so the
    next read reflects it rather than serving the pre-write snapshot."""
    with _cache_lock:
        for key in [k for k in _tasks_cache if k[0] == task_list_id]:
            del _tasks_cache[key]
        _total_cache.pop(task_list_id, None)


def _service():
    return build("tasks", "v1", credentials=get_credentials(), cache_discovery=False)


def _is_total_points_task(title):
    return bool(TOTAL_POINTS_TITLE_RE.match(title or ""))


def _parse_points(title):
    """Look for a '5 pts' / '5pts' / '5 pts.' style point value anywhere in
    a task title. Returns (points_or_None, title_with_points_stripped)."""
    match = POINTS_PATTERN_RE.search(title)
    if not match:
        return None, title
    points = int(match.group(1))
    stripped = title[: match.start()] + title[match.end() :]
    stripped = re.sub(r"\s{2,}", " ", stripped).strip()
    return points, stripped


def list_task_lists():
    """Return [{id, title}] for every task list the user has, paging
    through all results."""
    service = _service()
    task_lists = []
    page_token = None
    while True:
        response = service.tasklists().list(pageToken=page_token).execute()
        for entry in response.get("items", []):
            task_lists.append({"id": entry["id"], "title": entry.get("title", entry["id"])})
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return task_lists


def get_task_list_title(task_list_id):
    """The display name of one task list, cached in memory. Falls back to the
    ID if the lookup fails, so a caller that only needs a label never has to
    handle an error."""
    cached = _title_cache.get(task_list_id)
    now = dt.datetime.now()
    if cached and (now - cached[1]) < _TITLE_TTL:
        return cached[0]
    try:
        entry = _service().tasklists().get(tasklist=task_list_id).execute()
        title = entry.get("title") or task_list_id
    except Exception:
        return cached[0] if cached else task_list_id
    _title_cache[task_list_id] = (title, now)
    return title


def _normalize_task(raw_task, points_enabled=False):
    title = raw_task.get("title") or "(No title)"
    points = None
    if points_enabled:
        points, title = _parse_points(title)
    return {
        "id": raw_task["id"],
        "title": title,
        "notes": raw_task.get("notes", ""),
        "due": raw_task.get("due"),
        "completed": raw_task.get("status") == "completed",
        "points": points,
    }


def _today_start():
    """Midnight at the start of today, in the host machine's local timezone."""
    return dt.datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)


def _parse_timestamp(value):
    """Parse an RFC3339 timestamp from the API, or None if it can't be read."""
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_stale_completion(raw_task, today_start):
    """True for a task that was completed on an earlier day, so it should
    drop out of view once midnight passes.

    Recurring tasks are unaffected: Google Tasks replaces a completed
    occurrence with a fresh, incomplete one for the next date, so what the
    API returns for them is either still incomplete or completed today."""
    if raw_task.get("status") != "completed":
        return False
    completed_at = _parse_timestamp(raw_task.get("completed"))
    if completed_at is None:
        # No usable completion time, so don't guess at hiding the task.
        return False
    return completed_at < today_start


def get_tasks(task_list_id, points_enabled=False):
    """Fetch the tasks in a list, incomplete ones first. Tasks completed
    earlier today are included so a checked task can be un-checked, but
    those completed on an earlier day are left out. The hidden points
    bookkeeping task, if any, is never included.

    Served from a short-lived cache; see _TASKS_TTL."""
    key = (task_list_id, bool(points_enabled))
    cached = _cache_get(_tasks_cache, key)
    if cached is not None:
        return cached
    tasks = _fetch_tasks(task_list_id, points_enabled=points_enabled)
    _cache_put(_tasks_cache, key, tasks)
    return tasks


def _fetch_tasks(task_list_id, points_enabled=False):
    service = _service()
    today_start = _today_start()
    tasks = []
    page_token = None
    while True:
        response = (
            service.tasks()
            .list(tasklist=task_list_id, showCompleted=True, showHidden=True, pageToken=page_token)
            .execute()
        )
        for raw_task in response.get("items", []):
            if _is_total_points_task(raw_task.get("title")):
                continue
            if _is_stale_completion(raw_task, today_start):
                continue
            tasks.append(_normalize_task(raw_task, points_enabled=points_enabled))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    tasks.sort(key=lambda t: t["completed"])
    return tasks


def _find_total_points_raw_task(service, task_list_id):
    page_token = None
    while True:
        response = (
            service.tasks()
            .list(tasklist=task_list_id, showCompleted=True, showHidden=True, pageToken=page_token)
            .execute()
        )
        for raw_task in response.get("items", []):
            if _is_total_points_task(raw_task.get("title")):
                return raw_task
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return None


def get_total_points(task_list_id):
    """Return the cumulative points total stored in the hidden bookkeeping
    task, or 0 if it doesn't exist yet.

    Served from a short-lived cache; see _TASKS_TTL."""
    cached = _cache_get(_total_cache, task_list_id)
    if cached is not None:
        return cached
    total = _fetch_total_points(task_list_id)
    _cache_put(_total_cache, task_list_id, total)
    return total


def _fetch_total_points(task_list_id):
    raw_task = _find_total_points_raw_task(_service(), task_list_id)
    if not raw_task:
        return 0
    match = TOTAL_POINTS_TITLE_RE.match(raw_task.get("title") or "")
    return int(match.group(1)) if match else 0


def adjust_total_points(task_list_id, delta):
    """Add delta (may be negative) to the cumulative points total, creating
    the hidden bookkeeping task in this list if it doesn't exist yet.
    Returns the new total."""
    service = _service()
    existing = _find_total_points_raw_task(service, task_list_id)
    current = 0
    if existing:
        match = TOTAL_POINTS_TITLE_RE.match(existing.get("title") or "")
        current = int(match.group(1)) if match else 0
    new_total = max(0, current + delta)
    title = f"Total Points: {new_total}"

    if existing:
        service.tasks().patch(tasklist=task_list_id, task=existing["id"], body={"title": title}).execute()
    else:
        service.tasks().insert(tasklist=task_list_id, body={"title": title, "status": "completed"}).execute()

    _invalidate(task_list_id)
    return new_total


def set_task_completed(task_list_id, task_id, completed, points_enabled=False):
    """Mark a task completed or not-completed, returning
    (task, total_points, changed).

    The status is read first and the write skipped when it already matches,
    which makes a repeated request a no-op. That matters because the points
    total is kept as a running counter: patching an already-completed task
    would add its points a second time. `changed` is False for such a
    no-op, so the caller can skip logging it too.

    total_points is the new cumulative total, or None when points tracking
    is off, the task has no point value, or nothing changed."""
    service = _service()
    target = "completed" if completed else "needsAction"

    current = service.tasks().get(tasklist=task_list_id, task=task_id).execute()
    if current.get("status") == target:
        return _normalize_task(current, points_enabled=points_enabled), None, False

    raw_task = service.tasks().patch(
        tasklist=task_list_id, task=task_id, body={"status": target}
    ).execute()
    task = _normalize_task(raw_task, points_enabled=points_enabled)
    _invalidate(task_list_id)

    total_points = None
    if points_enabled and task["points"]:
        delta = task["points"] if completed else -task["points"]
        total_points = adjust_total_points(task_list_id, delta)

    return task, total_points, True
