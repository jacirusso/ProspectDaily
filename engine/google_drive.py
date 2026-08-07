"""Deliver the daily report to Google Drive as a Google Sheet.

Two backends, auto-selected:
  * OAuth (preferred for personal Gmail): files are created in the user's own
    Drive, owned by them, inside an app-managed folder. No Shared Drive needed.
  * Service account (Workspace Shared Drives only): writes into a shared folder.

If neither is configured/available, delivery is skipped and the pipeline falls
back to the local CSV.
"""
import json
from typing import Optional

from . import config, google_oauth
from .report import rows_from, html_report

_SA_SCOPES = ["https://www.googleapis.com/auth/drive",
              "https://www.googleapis.com/auth/spreadsheets"]
_FOLDER_MIME = "application/vnd.google-apps.folder"
_SHEET_MIME = "application/vnd.google-apps.spreadsheet"
_DOC_MIME = "application/vnd.google-apps.document"


def _sa_credentials():
    from google.oauth2 import service_account
    raw = config.GOOGLE_SERVICE_ACCOUNT_JSON
    info = json.loads(raw) if raw.strip().startswith("{") else json.load(open(raw))
    return service_account.Credentials.from_service_account_info(info, scopes=_SA_SCOPES)


def mode() -> Optional[str]:
    """Which delivery backend is ready: 'oauth', 'service_account', or None."""
    try:
        import googleapiclient  # noqa: F401
    except Exception:
        return None
    if google_oauth.is_connected():
        return "oauth"
    if config.GOOGLE_SERVICE_ACCOUNT_JSON:
        return "service_account"
    return None


def is_available() -> bool:
    return mode() is not None


def _ensure_app_folder(drive, name: str) -> str:
    """Find (or create) the app's root report folder in the user's Drive. With
    the drive.file scope, list() only returns files the app created, so this
    both finds a previously-created folder and avoids touching anything else."""
    q = (f"name = '{name.replace(chr(39), '')}' and mimeType = '{_FOLDER_MIME}' "
         f"and trashed = false")
    found = drive.files().list(q=q, spaces="drive",
                               fields="files(id,name)").execute().get("files", [])
    if found:
        return found[0]["id"]
    meta = {"name": name, "mimeType": _FOLDER_MIME}
    created = drive.files().create(body=meta, fields="id").execute()
    return created["id"]


def _ensure_link_shared(drive, file_id: str) -> None:
    """Make a file/folder readable by anyone with the link (idempotent). Used
    to give each client an accessible link to their own folder."""
    try:
        drive.permissions().create(
            fileId=file_id, body={"type": "anyone", "role": "reader"}).execute()
    except Exception:
        pass  # already shared, or a benign race


def ensure_client_folder(drive, client_label: str):
    """Find/create a per-client subfolder under the root, shared by link.
    Returns (folder_id, folder_web_link). This is how each client gets their
    OWN Google folder while everything stays in one (your) Drive."""
    root = _ensure_app_folder(drive, config.GOOGLE_DRIVE_FOLDER_NAME)
    name = (client_label or "Client").replace("'", "")
    q = (f"name = '{name}' and mimeType = '{_FOLDER_MIME}' "
         f"and '{root}' in parents and trashed = false")
    found = drive.files().list(q=q, spaces="drive",
                               fields="files(id,webViewLink)").execute().get("files", [])
    if found:
        fid, link = found[0]["id"], found[0].get("webViewLink")
    else:
        created = drive.files().create(
            body={"name": name, "mimeType": _FOLDER_MIME, "parents": [root]},
            fields="id, webViewLink").execute()
        fid, link = created["id"], created.get("webViewLink")
    _ensure_link_shared(drive, fid)
    return fid, link


def _resolve_parent(drive, client_label):
    """The folder a report should be created in: the client's own subfolder
    when a label is given, else the root app folder."""
    if client_label:
        fid, _ = ensure_client_folder(drive, client_label)
        return fid
    return _ensure_app_folder(drive, config.GOOGLE_DRIVE_FOLDER_NAME)


def client_folder_link(client_label: str):
    """Public helper: get/create a client's folder and return its shareable
    link (for the dashboard / welcome email). OAuth mode only."""
    if mode() != "oauth":
        return None
    from googleapiclient.discovery import build
    drive = build("drive", "v3", credentials=google_oauth.load_credentials())
    _, link = ensure_client_folder(drive, client_label)
    return link


def deliver(prospects, folder_id: str, title: str,
            client_label: str = None) -> Optional[str]:
    """Create a Google Sheet named `title` filled with the report, and return
    its shareable URL. Returns None if no backend is available."""
    m = mode()
    if m is None:
        return None
    from googleapiclient.discovery import build

    if m == "oauth":
        creds = google_oauth.load_credentials()
        supports_all = False
        parent = None  # resolved below to the client's folder in the user's Drive
    else:
        creds = _sa_credentials()
        supports_all = True
        parent = folder_id

    drive = build("drive", "v3", credentials=creds)
    sheets = build("sheets", "v4", credentials=creds)

    if m == "oauth":
        parent = _resolve_parent(drive, client_label)

    file_meta = {"name": title, "mimeType": _SHEET_MIME, "parents": [parent]}
    created = drive.files().create(
        body=file_meta, fields="id, webViewLink",
        supportsAllDrives=supports_all).execute()
    spreadsheet_id = created["id"]

    sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range="A1", valueInputOption="RAW",
        body={"values": rows_from(prospects)}).execute()

    sheets.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{
            "repeatCell": {
                "range": {"sheetId": 0, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.textFormat.bold",
            }}]}).execute()

    return created.get("webViewLink")


def deliver_doc(prospects, folder_id: str, title: str,
                client_label: str = None) -> Optional[str]:
    """Deliver the report as a native Google DOC (document-style layout) by
    uploading HTML and letting Drive convert it. Returns the doc URL."""
    m = mode()
    if m is None:
        return None
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaInMemoryUpload

    if m == "oauth":
        creds = google_oauth.load_credentials()
        supports_all = False
        parent = None
    else:
        creds = _sa_credentials()
        supports_all = True
        parent = folder_id

    drive = build("drive", "v3", credentials=creds)
    if m == "oauth":
        parent = _resolve_parent(drive, client_label)

    html = html_report(prospects, title)
    media = MediaInMemoryUpload(html.encode("utf-8"), mimetype="text/html",
                                resumable=False)
    file_meta = {"name": title, "mimeType": _DOC_MIME, "parents": [parent]}
    created = drive.files().create(
        body=file_meta, media_body=media, fields="id, webViewLink",
        supportsAllDrives=supports_all).execute()
    return created.get("webViewLink")


def deliver_pdf(pdf_bytes: bytes, folder_id: str, title: str,
                client_label: str = None) -> Optional[str]:
    """Upload a rendered PDF report into the client's Drive folder (no Docs
    conversion — stays a pixel-perfect PDF). Returns the file's shareable URL."""
    m = mode()
    if m is None:
        return None
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaInMemoryUpload

    if m == "oauth":
        creds = google_oauth.load_credentials()
        supports_all = False
        parent = None
    else:
        creds = _sa_credentials()
        supports_all = True
        parent = folder_id

    drive = build("drive", "v3", credentials=creds)
    if m == "oauth":
        parent = _resolve_parent(drive, client_label)

    name = title if title.lower().endswith(".pdf") else title + ".pdf"
    media = MediaInMemoryUpload(pdf_bytes, mimetype="application/pdf", resumable=False)
    file_meta = {"name": name, "mimeType": "application/pdf", "parents": [parent]}
    created = drive.files().create(
        body=file_meta, media_body=media, fields="id, webViewLink",
        supportsAllDrives=supports_all).execute()
    return created.get("webViewLink")
