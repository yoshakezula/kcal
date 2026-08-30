"""Load and refresh the OAuth credentials saved by authorize.py.

This module never runs the interactive consent flow itself -- that only
happens once, via authorize.py, run manually by the user. Here we just load
the resulting token.json, transparently refresh the access token when it has
expired, and persist the refreshed token back to disk.
"""

import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/tasks",
]
TOKEN_PATH = os.path.join(os.path.dirname(__file__), "token.json")


class NotAuthorized(Exception):
    """Raised when there is no usable token yet; caller should tell the
    user to run `python authorize.py`."""


def get_credentials():
    if not os.path.exists(TOKEN_PATH):
        raise NotAuthorized("No token.json found. Run: python authorize.py")

    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    if not creds or not creds.valid:
        raise NotAuthorized(
            "Stored credentials are invalid or revoked. Run: python authorize.py"
        )

    return creds
