"""Download the uploaded PDFs from Google Drive.

DRIVE_CREDENTIALS is either an OAuth token made with  python -m ats_worker authorize  (the Google
account that owns the resume folder), or a service-account key that is a member of the shared drive."""
import json

import requests

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
FILES_URL = "https://www.googleapis.com/drive/v3/files"
MAX_BYTES = 10 * 1024 * 1024


class DriveError(Exception):
    def __init__(self, message, missing=False):
        super().__init__(message)
        self.missing = missing      # the file is gone (deleted by the user): no point retrying


class Drive:
    def __init__(self, credentials_path):
        import google.auth
        import google.auth.exceptions
        from google.auth.transport.requests import AuthorizedSession
        if not credentials_path.exists():
            raise SystemExit(f"{credentials_path} not found. Run: python -m ats_worker authorize")
        try:
            creds, _ = google.auth.load_credentials_from_file(str(credentials_path), default_scopes=SCOPES)
        except (ValueError, google.auth.exceptions.GoogleAuthError) as e:
            raise SystemExit(f"Can't load {credentials_path}: {e}")
        self.http = AuthorizedSession(creds)

    def download(self, file_id):
        import google.auth.exceptions
        try:
            resp = self.http.get(f"{FILES_URL}/{file_id}", params={"alt": "media", "supportsAllDrives": "true"},
                                 timeout=60, stream=True)
        except (requests.RequestException, google.auth.exceptions.GoogleAuthError) as e:
            raise DriveError(f"Drive download failed: {e}") from e
        if resp.status_code == 404:
            raise DriveError(f"Drive file {file_id} not found", missing=True)
        if resp.status_code != 200:
            raise DriveError(f"Drive download failed: HTTP {resp.status_code} {resp.text[:200]}")
        data = b""
        for chunk in resp.iter_content(64 * 1024):
            data += chunk
            if len(data) > MAX_BYTES:
                raise DriveError("file is larger than 10 MB", missing=True)
        return data


def authorize(client_secrets, token_path):
    """One-time browser sign-in that writes token.json (read-only Drive access)."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), SCOPES)
    creds = flow.run_local_server(port=0)
    # to_json() leaves out "type", which google.auth.load_credentials_from_file needs
    token_path.write_text(json.dumps({**json.loads(creds.to_json()), "type": "authorized_user"}, indent=1))
    token_path.chmod(0o600)
    return token_path
