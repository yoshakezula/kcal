"""Thin wrapper around the Google Tasks API: list task lists, fetch the
tasks within one, and toggle a task's completion status."""

from googleapiclient.discovery import build

from auth import get_credentials


def _service():
    return build("tasks", "v1", credentials=get_credentials(), cache_discovery=False)


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


def _normalize_task(raw_task):
    return {
        "id": raw_task["id"],
        "title": raw_task.get("title") or "(No title)",
        "notes": raw_task.get("notes", ""),
        "due": raw_task.get("due"),
        "completed": raw_task.get("status") == "completed",
    }


def get_tasks(task_list_id):
    """Fetch every task in a list (including completed ones, so a checked
    task can be un-checked), incomplete tasks first."""
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
            tasks.append(_normalize_task(raw_task))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    tasks.sort(key=lambda t: t["completed"])
    return tasks


def set_task_completed(task_list_id, task_id, completed):
    """Mark a task completed or not-completed, returning the updated task."""
    service = _service()
    body = {"status": "completed" if completed else "needsAction"}
    raw_task = service.tasks().patch(tasklist=task_list_id, task=task_id, body=body).execute()
    return _normalize_task(raw_task)
