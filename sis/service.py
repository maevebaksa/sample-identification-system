"""Core logic: pull finished prints from Moonraker, archive gcode, reprint."""
import hashlib
import json
import logging
import os
import posixpath
import re
import shutil
import threading
import time

from .db import DB
from .gcode_meta import flatten_moonraker_metadata, parse_gcode_settings
from .moonraker import Moonraker, MoonrakerError

log = logging.getLogger("sis")


def _safe_name(name):
    return re.sub(r"[^A-Za-z0-9._\-]+", "_", posixpath.basename(name)) or "file.gcode"


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class Service:
    def __init__(self, cfg, moonraker=None):
        self.cfg = cfg
        self.data = cfg["data_dir"]
        self.gcode_dir = self.data / "gcodes"
        self.photo_dir = self.data / "photos"
        for d in (self.data, self.gcode_dir, self.photo_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.db = DB(self.data / "samples.db")
        self.mr = moonraker or Moonraker(cfg["moonraker_url"], cfg.get("moonraker_api_key", ""))
        self._sync_lock = threading.Lock()
        self.last_sync = {"time": None, "error": None, "new": 0}

    # ------------------------------------------------------------------ sync
    def sync_once(self):
        if not self._sync_lock.acquire(blocking=False):
            return self.last_sync
        try:
            new = self._sync()
            self.last_sync = {"time": time.time(), "error": None, "new": new}
        except MoonrakerError as e:
            self.last_sync = {"time": time.time(), "error": str(e), "new": 0}
            log.warning("sync failed: %s", e)
        finally:
            self._sync_lock.release()
        return self.last_sync

    def _sync(self):
        jobs = self.mr.list_history(limit=200)
        finished = [j for j in jobs if j.get("status") != "in_progress"]
        finished.sort(key=lambda j: j.get("start_time") or 0)

        baseline = self.db.get_meta("baseline_time")
        if baseline is None:
            n = int(self.cfg["initial_import_count"])
            chosen = finished[-n:] if n > 0 else []
            starts = [j["start_time"] for j in chosen] + [
                j["start_time"] for j in jobs if j.get("status") == "in_progress" and j.get("start_time")]
            baseline = min(starts) if starts else time.time()
            self.db.set_meta("baseline_time", baseline)
        baseline = float(baseline)

        known = {r["job_id"] for r in self.db.q("SELECT job_id FROM samples")}
        count = 0
        for job in finished:
            jid = str(job["job_id"])
            if jid in known or (job.get("start_time") or 0) < baseline:
                continue
            self._import_job(job)
            count += 1
        # Retry archiving for samples whose file couldn't be fetched earlier.
        for s in self.db.q("SELECT id, filename FROM samples WHERE gcode_path IS NULL AND archive_error IS NOT NULL"):
            self._archive(s["id"], s["filename"])
        return count

    def _import_job(self, job):
        settings = flatten_moonraker_metadata(job.get("metadata"))
        sid = self.db.add_sample(
            job_id=str(job["job_id"]), filename=job["filename"],
            moonraker_status=job.get("status"),
            start_time=job.get("start_time"), end_time=job.get("end_time"),
            print_duration=job.get("print_duration"), total_duration=job.get("total_duration"),
            filament_used=job.get("filament_used"),
            settings=json.dumps(settings))
        log.info("new sample #%s from %s", sid, job["filename"])
        self._archive(sid, job["filename"])

    def _archive(self, sid, filename):
        """Copy the gcode into the safe folder and parse its settings."""
        tmp = self.gcode_dir / f".tmp_{sid}"
        try:
            self.mr.download(filename, tmp)
        except MoonrakerError as e:
            self.db.update_sample(sid, archive_error=str(e))
            return
        digest = _sha256(tmp)
        dup = self.db.one("SELECT id, gcode_path FROM samples WHERE gcode_sha256=? AND id<>? "
                          "AND gcode_path IS NOT NULL ORDER BY id LIMIT 1", (digest, sid))
        if dup:
            rel = dup["gcode_path"]          # identical file already archived: reuse it
            tmp.unlink()
        else:
            rel = f"S{sid:04d}_{_safe_name(filename)}"
            shutil.move(str(tmp), self.gcode_dir / rel)
        settings = self.db.get_sample(sid)["settings"]
        parsed = parse_gcode_settings(self.gcode_dir / rel)
        settings = {**settings, **parsed}
        # Older metadata snapshots may be missing: fetch live metadata as a fallback.
        if not any(k.startswith("meta.") for k in settings):
            try:
                settings.update(flatten_moonraker_metadata(self.mr.file_metadata(filename)))
            except MoonrakerError:
                pass
        self.db.update_sample(sid, gcode_path=rel, gcode_sha256=digest, archive_error=None,
                              reprint_of=dup["id"] if dup else None,
                              settings=json.dumps(settings))

    # --------------------------------------------------------------- reprint
    def reprint(self, sid):
        s = self.db.get_sample(sid)
        if not s or not s["gcode_path"]:
            raise MoonrakerError("This sample has no archived gcode to reprint.")
        local = self.gcode_dir / s["gcode_path"]
        if not local.is_file():
            raise MoonrakerError("Archived gcode is missing from disk: " + s["gcode_path"])
        state = self.mr.print_state()
        if state in ("printing", "paused"):
            raise MoonrakerError(f"Printer is busy ({state}).")
        name = s["filename"]
        meta = self.mr.file_metadata(name)
        if meta is not None and meta.get("size") == local.stat().st_size:
            self.mr.start_print(name)                 # original is still there
            return {"started": name, "uploaded": False}
        if meta is not None:                          # same name, different content
            name = posixpath.join("sis_reprints", f"S{sid:04d}_{_safe_name(name)}")
        self.mr.upload(local, name, start=True)       # restore from archive and print
        return {"started": name, "uploaded": True}

    # ----------------------------------------------------------------- misc
    def poll_forever(self):
        while True:
            self.sync_once()
            time.sleep(max(5, int(self.cfg["poll_seconds"])))
