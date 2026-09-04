"""Thin wrapper around the Google Tasks API: list task lists, fetch the
tasks within one, and toggle a task's completion status."""

import re

from googleapiclient.discovery import build

from auth import get_credentials

# Matches "5 pts", "5pts", and "5 pts." (case-insensitive) inside a task
# title, but not as a prefix of some other word (e.g. "5ptsomething").
POINTS_PATTERN_RE = re.compile(r"(\d+)\s?pts\.?(?![a-zA-Z])", re.IGNORECASE)

# The hidden bookkeeping task that stores the cumulative points total.
TOTAL_POINTS_TITLE_RE = re.compile(r"^Total Points:\s*(\d+)$")


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


def get_tasks(task_list_id, points_enabled=False):
    """Fetch every task in a list (including completed ones, so a checked
    task can be un-checked), incomplete tasks first. The hidden points
    bookkeeping task, if any, is never included."""
    service = _service()
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
    task, or 0 if it doesn't exist yet."""
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

    return new_total


def set_task_completed(task_list_id, task_id, completed, points_enabled=False):
    """Mark a task completed or not-completed, returning (task, total_points).
    When points tracking is enabled and the task carries a point value, the
    cumulative points total is adjusted accordingly; total_points is the new
    total, or None when points tracking is off or the task has no points."""
    service = _service()
    body = {"status": "completed" if completed else "needsAction"}
    raw_task = service.tasks().patch(tasklist=task_list_id, task=task_id, body=body).execute()
    task = _normalize_task(raw_task, points_enabled=points_enabled)

    total_points = None
    if points_enabled and task["points"]:
        delta = task["points"] if completed else -task["points"]
        total_points = adjust_total_points(task_list_id, delta)

    return task, total_points
