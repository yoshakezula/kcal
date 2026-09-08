"""Load and refresh the OAuth credentials saved by authorize.py.

This module never runs the interactive consent flow itself -- that only
happens once, via authorize.py, run manually by the user. Here we just load
the resulting token.json, transparently refresh the access token when it has
expired, and persist the refreshed token back to disk.
"""

import os

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/tasks",
    # Per-file Drive access, for the points log spreadsheet. This grants the
    # app nothing but the folder and sheet it creates itself -- it cannot see
    # anything else in Drive, which is why the log sheet has to be created
    # from Settings rather than pointed at an existing one.
    "https://www.googleapis.com/auth/drive.file",
]
TOKEN_PATH = os.path.join(os.path.dirname(__file__), "token.json")


class NotAuthorized(Exception):
    """Raised when there is no usable token yet; caller should tell the
    user to run `python authorize.py`."""


def get_credentials():
    if not os.path.exists(TOKEN_PATH):
        raise NotAuthorized("No token.json found -- this machine isn't authorized yet.")

    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            # Google revokes refresh tokens (an app still in "Testing" expires
            # them after a week). That's the same "go re-authorize" situation
            # as a missing token, so raise it as such instead of letting a
            # generic error surface to the frontend as a silent empty calendar.
            # Every caller pairs this with its own "run authorize.py", so the
            # message stays purely diagnostic.
            detail = e.args[0] if e.args else e
            raise NotAuthorized(f"Google rejected the saved sign-in: {detail}") from e
        with open(TOKEN_PATH, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    if not creds or not creds.valid:
        raise NotAuthorized("The saved sign-in is no longer valid.")

    return creds
