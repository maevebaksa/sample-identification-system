"""Thin client for the parts of the Moonraker API we need."""
import os
import posixpath
from urllib.parse import quote

import requests


class MoonrakerError(Exception):
    pass


class Moonraker:
    def __init__(self, base_url, api_key=""):
        self.base = base_url.rstrip("/")
        self.s = requests.Session()
        if api_key:
            self.s.headers["X-Api-Key"] = api_key

    def _req(self, method, path, timeout=15, **kw):
        try:
            r = self.s.request(method, self.base + path, timeout=timeout, **kw)
        except requests.RequestException as e:
            raise MoonrakerError(f"Cannot reach Moonraker at {self.base}: {e}") from e
        if r.status_code >= 400:
            msg = r.text[:300]
            try:
                msg = r.json()["error"]["message"]
            except Exception:
                pass
            raise MoonrakerError(f"Moonraker {r.status_code}: {msg}")
        return r

    def list_history(self, limit=200):
        r = self._req("GET", "/server/history/list", params={"limit": limit, "order": "desc"})
        return r.json()["result"]["jobs"]

    def file_metadata(self, filename):
        """Return Moonraker's metadata dict, or None if the file doesn't exist."""
        try:
            r = self._req("GET", "/server/files/metadata", params={"filename": filename})
        except MoonrakerError as e:
            if "404" in str(e):
                return None
            raise
        return r.json()["result"]

    def download(self, filename, dest_path):
        """Stream a file from the gcodes root to dest_path. Returns bytes written."""
        url = "/server/files/gcodes/" + quote(filename)
        try:
            r = self.s.get(self.base + url, stream=True, timeout=(10, 120))
        except requests.RequestException as e:
            raise MoonrakerError(f"Download failed: {e}") from e
        if r.status_code != 200:
            raise MoonrakerError(f"File not found in Moonraker ({r.status_code}): {filename}")
        n = 0
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(1024 * 256):
                f.write(chunk)
                n += len(chunk)
        return n

    def print_state(self):
        r = self._req("GET", "/printer/objects/query", params={"print_stats": ""})
        return r.json()["result"]["status"]["print_stats"]["state"]

    def start_print(self, filename):
        self._req("POST", "/printer/print/start", params={"filename": filename})

    def upload(self, local_path, filename, start=False):
        """Upload local_path into gcodes/<filename> (optionally starting it)."""
        folder, base = posixpath.split(filename)
        data = {"root": "gcodes", "print": "true" if start else "false"}
        if folder:
            data["path"] = folder
        with open(local_path, "rb") as f:
            self._req("POST", "/server/files/upload", timeout=(10, 300),
                      data=data, files={"file": (base, f)})
