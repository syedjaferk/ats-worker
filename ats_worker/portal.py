"""Client for the portal's worker API (course_portal/resumes/api.py). We only ever call out; nothing calls in."""
import requests

TIMEOUT = 30


class PortalError(Exception):
    pass


class Portal:
    def __init__(self, base_url, token):
        if not token:
            raise SystemExit("Set ATS_WORKER_TOKEN (the same value as on the portal).")
        self.base = f"{base_url}/api/ats"
        self.http = requests.Session()
        self.http.headers.update({"Authorization": f"Bearer {token}", "User-Agent": "ats-worker/1"})

    def _post(self, path, body=None):
        try:
            resp = self.http.post(f"{self.base}/{path}", json=body or {}, timeout=TIMEOUT)
        except requests.RequestException as e:
            raise PortalError(f"portal unreachable: {e}") from e
        if resp.status_code == 401:
            raise PortalError("portal refused the token: check ATS_WORKER_TOKEN")
        if resp.status_code >= 500:
            raise PortalError(f"portal error HTTP {resp.status_code}")
        return resp

    def claim(self):
        """The next job ({tracking_id, drive_file_id, target_role, attempt}) or None."""
        resp = self._post("claim/")
        if resp.status_code == 204:
            return None
        if resp.status_code != 200:
            raise PortalError(f"claim failed: HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json()

    def complete(self, tracking_id, result):
        """Returns (http status, body). 200 = stored, 422 = the result was rejected (body has errors),
        409 = we no longer hold this job."""
        resp = self._post(f"{tracking_id}/complete/", {"result": result})
        try:
            body = resp.json()
        except ValueError:
            body = {}
        return resp.status_code, body

    def fail(self, tracking_id, reason):
        """reason: 'unreadable', 'not_a_resume' or 'error'. The user sees a fixed message for each."""
        self._post(f"{tracking_id}/fail/", {"reason": reason})
