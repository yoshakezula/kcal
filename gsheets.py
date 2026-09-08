"""Append completed tasks to a Google Sheets points log.

The app holds only the narrow "drive.file" scope, which grants access to
files this app created and nothing else in Drive. That shapes the whole
module: the folder and spreadsheet must be created from Settings (see
create_log), because a sheet the app has never touched is invisible to it
no matter how the ID arrives. An ID we created earlier can be re-pointed
at freely, though -- the grant lives with the file, not with config.json.

Layout: one tab per task list, named after the list, each with its own
running total. Tabs are tracked by their numeric sheet ID rather than
their title, so renaming a tab in the Sheets UI doesn't break logging.
Rows are an append-only ledger: un-checking a task logs a second row with
negative points, so the Points column always sums to the running total.
"""

import datetime as dt
import os
import re

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from auth import NotAuthorized, get_credentials

FOLDER_NAME = "kcal"
SPREADSHEET_TITLE = "Kcal Points Tracking"
FOLDER_MIME = "application/vnd.google-apps.folder"
HEADERS = ("When", "Task", "Points", "Total")

# Characters Sheets rejects in a tab title, plus its 100-character limit.
TAB_TITLE_BAD_CHARS_RE = re.compile(r"[][*?/:\\]")
TAB_TITLE_MAX = 100


class LogUnavailable(Exception):
    """The log spreadsheet can't be written to -- trashed, deleted, or the
    app's per-file grant is gone. Callers should surface this in Settings
    and offer to create a fresh log; never fail a task toggle over it."""


def _drive():
    return build("drive", "v3", credentials=get_credentials(), cache_discovery=False)


def _sheets():
    return build("sheets", "v4", credentials=get_credentials(), cache_discovery=False)


def _local_timezone_name():
    """The host's IANA timezone name, so timestamps in the sheet are read in
    the same zone the Pi wrote them. Returns None when it can't be determined
    -- Windows has no IANA name to read -- in which case Sheets creates the
    spreadsheet on Etc/GMT (not the Google account's timezone), and its
    File -> Settings needs fixing by hand. Timestamps are written as local
    wall-clock text either way, so this only affects date math in the sheet."""
    env = os.environ.get("TZ")
    if env and "/" in env:
        return env
    try:
        with open("/etc/timezone", "r", encoding="utf-8") as f:
            name = f.read().strip()
        return name or None
    except OSError:
        return None


def _tab_title(list_title):
    """Turn a task list name into a legal, non-empty tab title."""
    title = TAB_TITLE_BAD_CHARS_RE.sub(" ", list_title or "").strip()
    title = re.sub(r"\s{2,}", " ", title)
    return (title or "Tasks")[:TAB_TITLE_MAX]


def _a1(tab_title, span="A:D"):
    """A1 notation for a range on one tab, quoting the title (a literal
    single quote in a tab name is doubled)."""
    return "'{}'!{}".format(tab_title.replace("'", "''"), span)


def _insufficient_scope(error):
    """True when Google refused because token.json predates the drive.file
    scope -- a "go re-authorize" situation, not a broken spreadsheet."""
    if getattr(getattr(error, "resp", None), "status", None) != 403:
        return False
    return "insufficient" in str(error).lower()


def _check_scope(error):
    """Re-raise a missing-scope 403 as NotAuthorized, which every caller
    already knows how to present."""
    if _insufficient_scope(error):
        raise NotAuthorized(
            "This sign-in predates the points log and doesn't include Drive access. "
            "Delete token.json and run `python authorize.py` again."
        ) from error


def _unavailable(error):
    """True for the errors that mean "this sheet is gone", as opposed to a
    transient failure worth reporting as-is."""
    status = getattr(getattr(error, "resp", None), "status", None)
    return status in (403, 404) and not _insufficient_scope(error)


def _find_existing_folder(drive):
    """Look for a "kcal" folder this app created earlier. Under drive.file
    the search only ever sees the app's own files, so a folder of the same
    name that the user made by hand is invisible here and a separate one
    gets created -- unavoidable without broad Drive access."""
    response = (
        drive.files()
        .list(
            q=f"mimeType='{FOLDER_MIME}' and name='{FOLDER_NAME}' and trashed=false",
            fields="files(id)",
            pageSize=1,
        )
        .execute()
    )
    files = response.get("files", [])
    return files[0]["id"] if files else None


def _ensure_folder(drive, folder_id):
    """Return a usable folder ID, reusing the stored one when it's still
    there and creating the folder otherwise."""
    if folder_id:
        try:
            existing = drive.files().get(fileId=folder_id, fields="id,trashed").execute()
            if not existing.get("trashed"):
                return folder_id
        except HttpError as e:
            if not _unavailable(e):
                raise  # a transient error shouldn't spawn a duplicate folder

    existing = _find_existing_folder(drive)
    if existing:
        return existing

    created = (
        drive.files()
        .create(body={"name": FOLDER_NAME, "mimeType": FOLDER_MIME}, fields="id")
        .execute()
    )
    return created["id"]


def create_log(folder_id=""):
    """Create the log spreadsheet inside the "kcal" folder, creating the
    folder if needed. Returns (folder_id, spreadsheet_id, url).

    The spreadsheet's single starting tab is left alone; the first task list
    that logs a completion claims and renames it, so a fresh log never
    carries a stray empty "Sheet1"."""
    drive = _drive()
    try:
        folder_id = _ensure_folder(drive, folder_id)
    except HttpError as e:
        _check_scope(e)
        raise

    properties = {"title": SPREADSHEET_TITLE}
    timezone = _local_timezone_name()
    if timezone:
        properties["timeZone"] = timezone

    try:
        created = (
            _sheets()
            .spreadsheets()
            .create(body={"properties": properties}, fields="spreadsheetId,spreadsheetUrl")
            .execute()
        )
    except HttpError as e:
        _check_scope(e)
        raise
    spreadsheet_id = created["spreadsheetId"]

    # Sheets creates the file in My Drive's root; move it under the folder.
    # Best-effort: the sheet is already usable, and losing a working log over
    # a filing failure would be a bad trade. It stays in the root if this
    # fails, and the caller still records the ID.
    try:
        parents = drive.files().get(fileId=spreadsheet_id, fields="parents").execute().get("parents", [])
        move = {"fileId": spreadsheet_id, "addParents": folder_id, "fields": "id"}
        if parents:
            move["removeParents"] = ",".join(parents)
        drive.files().update(**move).execute()
    except HttpError:
        pass

    return folder_id, spreadsheet_id, created.get("spreadsheetUrl") or log_url(spreadsheet_id)


def log_url(spreadsheet_id):
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"


def _sheet_properties(sheets, spreadsheet_id):
    """[{sheetId, title}] for every tab, or LogUnavailable if the sheet is
    gone."""
    try:
        meta = (
            sheets.spreadsheets()
            .get(spreadsheetId=spreadsheet_id, fields="sheets.properties(sheetId,title)")
            .execute()
        )
    except HttpError as e:
        _check_scope(e)
        if _unavailable(e):
            raise LogUnavailable(
                "The log spreadsheet isn't reachable. It may have been deleted, "
                "moved to the trash, or had the kiosk's access revoked."
            ) from e
        raise
    return [s["properties"] for s in meta.get("sheets", [])]


def _new_tab_format_requests(sheet_id):
    """Header bold and frozen, timestamps shown as dates rather than as the
    raw serial numbers Sheets stores them as, and columns wide enough to read.
    Split out so an existing tab can be brought up to date with the same
    formatting (see format_existing_tabs)."""
    return [
        {
            "updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount",
            }
        },
        {
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.textFormat.bold",
            }
        },
        {
            # Without a date format the When column reads as "46272.67176".
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 1,
                },
                "cell": {
                    "userEnteredFormat": {
                        "numberFormat": {"type": "DATE_TIME", "pattern": "yyyy-mm-dd hh:mm:ss"}
                    }
                },
                "fields": "userEnteredFormat.numberFormat",
            }
        },
        {
            "autoResizeDimensions": {
                "dimensions": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 0,
                    "endIndex": len(HEADERS),
                }
            }
        },
    ]


def _format_new_tab(sheets, spreadsheet_id, sheet_id, tab_title):
    """Give a just-created tab its header row: written, bold, and frozen."""
    sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=_a1(tab_title, "A1:D1"),
        valueInputOption="RAW",
        body={"values": [list(HEADERS)]},
    ).execute()
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body={"requests": _new_tab_format_requests(sheet_id)}
    ).execute()


def _unique_title(wanted, taken):
    """Tab titles must be unique within a spreadsheet, so a second list with
    the same name gets a numeric suffix."""
    if wanted not in taken:
        return wanted
    for n in range(2, 100):
        candidate = f"{wanted} ({n})"[:TAB_TITLE_MAX]
        if candidate not in taken:
            return candidate
    return wanted[: TAB_TITLE_MAX - 12] + f" ({os.urandom(3).hex()})"


def resolve_tab(spreadsheet_id, task_list_id, list_title, known_tabs):
    """Return (tab_title, sheet_id, is_new) for a task list's tab, creating
    it when it doesn't exist yet. known_tabs maps task list ID -> sheet ID
    (from config); tabs are looked up by that numeric ID so a tab renamed in
    the Sheets UI is still found."""
    sheets = _sheets()
    properties = _sheet_properties(sheets, spreadsheet_id)
    by_id = {p["sheetId"]: p["title"] for p in properties}

    known_id = known_tabs.get(str(task_list_id))
    if known_id in by_id:
        return by_id[known_id], known_id, False

    wanted = _unique_title(_tab_title(list_title), set(by_id.values()))

    # A brand-new spreadsheet arrives with one empty default tab and nothing
    # mapped to it; claim that instead of leaving it behind.
    if len(properties) == 1 and not known_tabs:
        sheet_id = properties[0]["sheetId"]
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "requests": [
                    {
                        "updateSheetProperties": {
                            "properties": {"sheetId": sheet_id, "title": wanted},
                            "fields": "title",
                        }
                    }
                ]
            },
        ).execute()
    else:
        response = (
            sheets.spreadsheets()
            .batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": wanted}}}]},
            )
            .execute()
        )
        sheet_id = response["replies"][0]["addSheet"]["properties"]["sheetId"]

    _format_new_tab(sheets, spreadsheet_id, sheet_id, wanted)
    return wanted, sheet_id, True


def append_completion(spreadsheet_id, tab_title, task_title, points, total, when=None):
    """Append one ledger row. points is negative for an un-checked task;
    points and total are left blank when the task carries no point value."""
    when = when or dt.datetime.now().astimezone()
    row = [
        when.strftime("%Y-%m-%d %H:%M:%S"),
        task_title,
        points if points is not None else "",
        total if total is not None else "",
    ]
    try:
        _sheets().spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=_a1(tab_title),
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()
    except HttpError as e:
        _check_scope(e)
        if _unavailable(e):
            raise LogUnavailable("The log spreadsheet isn't reachable.") from e
        raise
