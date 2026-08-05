"""OAuth 'Connect Google' flow for personal Google accounts.

The user authorizes once (clicking 'Allow' in their browser); we store a
long-lived token and use it to create report files in THEIR Drive, owned by
them. Uses the drive.file scope (per-file, non-sensitive) so no app
verification is required and refresh tokens stay long-lived once the OAuth app
is published to production.
"""
import os
from typing import Optional

from . import config

# drive.file = the app can only touch files/folders IT creates. Enough to make
# the reports folder + sheets, and it keeps the consent non-scary + unverified-ok.
SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def is_connected() -> bool:
    return os.path.exists(config.GOOGLE_OAUTH_TOKEN)


def load_credentials():
    """Load stored user credentials, refreshing the access token if needed."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    if not is_connected():
        raise RuntimeError("Google is not connected yet (no token). Run authorize().")
    creds = Credentials.from_authorized_user_file(config.GOOGLE_OAUTH_TOKEN, SCOPES)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save(creds)
    return creds


def authorize() -> str:
    """Run the one-time consent flow in the user's browser and store the token.
    Returns the authorized account email. Must run on a machine with a browser
    (their Mac) because it briefly opens a localhost callback."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    if not os.path.exists(config.GOOGLE_OAUTH_CLIENT):
        raise RuntimeError(
            f"OAuth client file not found at {config.GOOGLE_OAUTH_CLIENT}. "
            "Create a Desktop OAuth client in Google Cloud and save it there.")
    flow = InstalledAppFlow.from_client_secrets_file(
        config.GOOGLE_OAUTH_CLIENT, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent",
                                  authorization_prompt_message="")
    _save(creds)
    return _whoami(creds)


def _save(creds) -> None:
    with open(config.GOOGLE_OAUTH_TOKEN, "w") as f:
        f.write(creds.to_json())
    os.chmod(config.GOOGLE_OAUTH_TOKEN, 0o600)


def _whoami(creds) -> str:
    try:
        from googleapiclient.discovery import build
        about = build("drive", "v3", credentials=creds).about().get(
            fields="user(emailAddress)").execute()
        return about.get("user", {}).get("emailAddress", "unknown")
    except Exception:
        return "connected"


if __name__ == "__main__":
    print("Opening your browser to connect Google…")
    email = authorize()
    print(f"✅ Connected as {email}. Token saved to {config.GOOGLE_OAUTH_TOKEN}")
