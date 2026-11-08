"""Core logic: pull finished prints from every printer, archive gcode, reprint anywhere."""
import hashlib
import json
import logging
import posixpath
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor

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


def normalize_url(url):
    url = (url or "").strip().rstrip("/")
    if not url:
        raise ValueError("URL is required")
    if not re.match(r"^https?://", url, re.I):
        url = "http://" + url
    return url


class Service:
    def __init__(self, cfg):
        self.cfg = cfg
        self.data = cfg["data_dir"]
        self.gcode_dir = self.data / "gcodes"
        self.photo_dir = self.data / "photos"
        for d in (self.data, self.gcode_dir, self.photo_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.db = DB(self.data / "samples.db")
        self._clients = {}
        self._locks = {}
        self.last_sync = {"time": None, "error": None, "new": 0}
        self._seed_default_printer()

    # --------------------------------------------------------------- printers
    def _seed_default_printer(self):
        """First run (or upgrade from the single-printer version): create a printer from config."""
        if not self.db.list_printers() and not (self.cfg.get("moonraker_url") or "").strip():
            return                      # e.g. Docker: printers are added in the web UI
        if not self.db.list_printers():
            pid = self.db.add_printer("Printer", normalize_url(self.cfg["moonraker_url"]),
                                      self.cfg.get("moonraker_api_key", ""),
                                      baseline_time=float(self.db.get_meta("baseline_time") or 0) or None)
        else:
            pid = self.db.list_printers()[0]["id"]
        # Samples from the single-printer version belong to the first printer.
        self.db.x("UPDATE samples SET job_id = ? || ':' || job_id, printer_id = ? WHERE printer_id IS NULL",
                  (pid, pid))

    def client(self, pid):
        p = self.db.get_printer(pid)
        if not p:
            raise MoonrakerError("Unknown printer")
        key = (p["id"], p["url"], p["api_key"])
        if key not in self._clients:
            self._clients[key] = Moonraker(p["url"], p["api_key"])
        return self._clients[key]

    # ------------------------------------------------------------------ sync
    def sync_once(self, only=None):
        printers = [p for p in self.db.list_printers() if p["enabled"] and (only is None or p["id"] == only)]
        if not printers:
            return self.last_sync
        with ThreadPoolExecutor(max_workers=min(8, len(printers))) as ex:
            results = list(ex.map(self._sync_printer, printers))
        errors = [f"{p['name']}: {e}" for p, (n, e) in zip(printers, results) if e]
        agg = {"time": time.time(), "error": "; ".join(errors) or None, "new": sum(n for n, _ in results)}
        self.last_sync = agg
        return agg

    def _sync_printer(self, p):
        lock = self._locks.setdefault(p["id"], threading.Lock())
        if not lock.acquire(blocking=False):
            return 0, None
        n, err = 0, None
        try:
            n = self._sync(p)
        except MoonrakerError as e:
            err = str(e)
            log.warning("sync failed for %s: %s", p["name"], e)
        finally:
            lock.release()
        self.db.update_printer(p["id"], last_sync=time.time(), last_error=err, last_new=n)
        return n, err

    def _sync(self, p):
        mr = self.client(p["id"])
        jobs = mr.list_history(limit=200)
        finished = sorted((j for j in jobs if j.get("status") != "in_progress"),
                          key=lambda j: j.get("start_time") or 0)

        baseline = self.db.get_printer(p["id"])["baseline_time"]
        if baseline is None:
            n = int(self.cfg["initial_import_count"])
            chosen = finished[-n:] if n > 0 else []
            starts = [j["start_time"] for j in chosen] + [
                j["start_time"] for j in jobs if j.get("status") == "in_progress" and j.get("start_time")]
            baseline = min(starts) if starts else time.time()
            self.db.update_printer(p["id"], baseline_time=baseline)

        known = {r["job_id"] for r in self.db.q("SELECT job_id FROM samples WHERE printer_id=?", (p["id"],))}
        count = 0
        for job in finished:
            if f"{p['id']}:{job['job_id']}" in known or (job.get("start_time") or 0) < baseline:
                continue
            self._import_job(p, mr, job)
            count += 1
        # Retry archiving for samples whose file couldn't be fetched earlier.
        for s in self.db.q("SELECT id, filename FROM samples WHERE printer_id=? AND gcode_path IS NULL "
                           "AND archive_error IS NOT NULL", (p["id"],)):
            self._archive(s["id"], s["filename"], mr)
        return count

    def _import_job(self, p, mr, job):
        settings = flatten_moonraker_metadata(job.get("metadata"))
        sid = self.db.add_sample(
            printer_id=p["id"], job_id=f"{p['id']}:{job['job_id']}", filename=job["filename"],
            moonraker_status=job.get("status"),
            start_time=job.get("start_time"), end_time=job.get("end_time"),
            print_duration=job.get("print_duration"), total_duration=job.get("total_duration"),
            filament_used=job.get("filament_used"),
            settings=json.dumps(settings))
        log.info("new sample #%s from %s (%s)", sid, job["filename"], p["name"])
        self._archive(sid, job["filename"], mr)

    def _archive(self, sid, filename, mr):
        """Copy the gcode into the safe folder and parse its settings."""
        tmp = self.gcode_dir / f".tmp_{sid}"
        try:
            mr.download(filename, tmp)
        except MoonrakerError as e:
            self.db.update_sample(sid, archive_error=str(e))
            tmp.unlink(missing_ok=True)
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
        settings.update(parse_gcode_settings(self.gcode_dir / rel))
        # Older metadata snapshots may be missing: fetch live metadata as a fallback.
        if not any(k.startswith("meta.") for k in settings):
            try:
                settings.update(flatten_moonraker_metadata(mr.file_metadata(filename)))
            except MoonrakerError:
                pass
        self.db.update_sample(sid, gcode_path=rel, gcode_sha256=digest, archive_error=None,
                              reprint_of=dup["id"] if dup else None,
                              settings=json.dumps(settings))

    # --------------------------------------------------------------- reprint
    def reprint(self, sid, printer_id=None):
        """Print a sample's archived gcode on its original printer, or on any other printer."""
        s = self.db.get_sample(sid)
        if not s or not s["gcode_path"]:
            raise MoonrakerError("This sample has no archived gcode to reprint.")
        local = self.gcode_dir / s["gcode_path"]
        if not local.is_file():
            raise MoonrakerError("Archived gcode is missing from disk: " + s["gcode_path"])
        pid = printer_id or s["printer_id"]
        printer = self.db.get_printer(pid)
        mr = self.client(pid)
        state = mr.print_state()
        if state in ("printing", "paused"):
            raise MoonrakerError(f"{printer['name']} is busy ({state}).")
        name = s["filename"]
        meta = mr.file_metadata(name)
        if meta is not None and meta.get("size") == local.stat().st_size:
            mr.start_print(name)                      # identical file is already there
            return {"started": name, "uploaded": False, "printer": printer["name"]}
        if meta is not None:                          # same name, different content
            name = posixpath.join("sis_reprints", f"S{sid:04d}_{_safe_name(name)}")
        mr.upload(local, name, start=True)            # restore from archive and print
        return {"started": name, "uploaded": True, "printer": printer["name"]}

    # ----------------------------------------------------------------- misc
    def poll_forever(self):
        while True:
            try:
                self.sync_once()
            except Exception:
                log.exception("sync loop error")
            time.sleep(max(5, int(self.cfg["poll_seconds"])))
