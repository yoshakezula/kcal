"""One-time setup script: run the interactive Google OAuth consent flow.

Usage:
    python authorize.py

Requires client_secret.json (downloaded from Google Cloud Console, OAuth
client type "Desktop app") to be present in this same directory. Opens your
browser for the Google sign-in/consent screen, then saves the resulting
credentials to token.json for the Flask app to use.
"""

import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

from auth import SCOPES, TOKEN_PATH

CLIENT_SECRET_PATH = os.path.join(os.path.dirname(__file__), "client_secret.json")


def main():
    if not os.path.exists(CLIENT_SECRET_PATH):
        print(
            "client_secret.json not found.\n\n"
            "Create an OAuth client (type: Desktop app) in the Google Cloud "
            "Console, download its JSON, and save it as:\n"
            f"  {CLIENT_SECRET_PATH}\n\n"
            "See README.md for step-by-step instructions."
        )
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_PATH, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(TOKEN_PATH, "w", encoding="utf-8") as f:
        f.write(creds.to_json())

    print(f"Success. Credentials saved to {TOKEN_PATH}")
    print("You can now run: python app.py")


if __name__ == "__main__":
    main()
