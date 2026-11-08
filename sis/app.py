import csv
import io
import json
import time
import uuid
from pathlib import Path

import segno
from flask import Flask, Response, abort, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename

from .moonraker import Moonraker, MoonrakerError
from .gcode_meta import normalize_model
from .service import Service, normalize_url

RESULTS = ("unrated", "success", "fail")

# Settings included in CSV exports when "key settings" is chosen.
KEY_SETTINGS = [
    "slicer", "slicer_version", "layer_height", "first_layer_height", "nozzle_diameter",
    "temperature", "first_layer_temperature", "nozzle_temperature", "bed_temperature",
    "first_layer_bed_temperature", "filament_type", "filament_settings_id", "print_settings_id",
    "printer_settings_id", "perimeters", "wall_loops", "top_solid_layers", "bottom_solid_layers",
    "fill_density", "sparse_infill_density", "fill_pattern", "sparse_infill_pattern",
    "perimeter_speed", "external_perimeter_speed", "outer_wall_speed", "inner_wall_speed",
    "infill_speed", "sparse_infill_speed", "max_volumetric_speed", "filament_max_volumetric_speed",
    "retract_length", "retraction_length", "retract_speed", "retraction_speed",
    "min_fan_speed", "max_fan_speed", "cooling", "extrusion_multiplier", "filament_flow_ratio",
    "brim_width", "support_material", "enable_support", "seam_position",
    "meta.estimated_time", "meta.filament_total", "meta.filament_name",
]

CORE_COLS = ["sample", "model", "printer", "result", "title", "tags", "notes", "filename", "reprint_of", "photos",
             "moonraker_status", "start_time", "print_minutes", "filament_used_mm", "gcode_archive"]


def create_app(cfg, service=None):
    svc = service or Service(cfg)
    static = Path(__file__).parent / "static"
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024
    app.svc = svc

    @app.after_request
    def no_stale_ui(resp):
        # Always revalidate the UI files so updates show up without a hard refresh.
        if request.path == "/" or request.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    def err(msg, code=400):
        return jsonify(error=msg), code

    def base_url():
        return cfg["public_url"] or request.host_url.rstrip("/")

    # ------------------------------------------------------------- static
    @app.get("/")
    def index():
        return send_from_directory(static, "index.html")

    @app.get("/static/<path:p>")
    def static_files(p):
        return send_from_directory(static, p)

    @app.get("/photos/<path:p>")
    def photo(p):
        return send_from_directory(svc.photo_dir, p)

    # ---------------------------------------------------------------- info
    @app.get("/api/info")
    def info():
        return jsonify(printers=[{"id": p["id"], "name": p["name"], "url": p["url"], "enabled": bool(p["enabled"])}
                                 for p in svc.db.list_printers()],
                       last_sync=svc.last_sync,
                       next_number=(svc.db.one("SELECT COALESCE(MAX(id),0)+1 AS n FROM samples")["n"]))

    @app.post("/api/sync")
    def sync():
        only = request.args.get("printer", type=int)
        return jsonify(svc.sync_once(only))

    # ------------------------------------------------------------- samples
    @app.get("/api/samples")
    def samples():
        return jsonify(svc.db.list_samples())

    @app.get("/api/samples/<int:sid>")
    def sample(sid):
        s = svc.db.get_sample(sid)
        if not s:
            abort(404)
        return jsonify(s)

    @app.patch("/api/samples/<int:sid>")
    def patch_sample(sid):
        if not svc.db.get_sample(sid):
            abort(404)
        body = request.get_json(force=True) or {}
        fields = {}
        if "result" in body:
            if body["result"] not in RESULTS:
                return err("result must be one of " + ", ".join(RESULTS))
            fields["result"] = body["result"]
        if "model" in body:
            model = str(body["model"]).strip()[:300]
            fields["model"], fields["model_key"] = model, normalize_model(model)
        if "archived" in body:
            fields["archived"] = 1 if body["archived"] else 0
        for k in ("title", "notes", "tags"):
            if k in body:
                fields[k] = str(body[k])[:5000]
        svc.db.update_sample(sid, **fields)
        return jsonify(svc.db.get_sample(sid))

    @app.get("/api/samples/<int:sid>/related")
    def related(sid):
        """Other samples made from the same source model (STL/3MF)."""
        s = svc.db.get_sample(sid)
        if not s:
            abort(404)
        if not s["model_key"]:
            return jsonify([])
        return jsonify(svc.db.q(
            "SELECT s.id, s.result, s.archived, s.title, s.start_time, s.model, s.reprint_of,"
            " (SELECT name FROM printers p WHERE p.id=s.printer_id) AS printer_name"
            " FROM samples s WHERE s.model_key=? AND s.id<>? ORDER BY s.id", (s["model_key"], sid)))

    @app.post("/api/samples/archive")
    def bulk_archive():
        """{ids:[...], archived:true|false}  or  {unrated:true} to archive every unrated sample."""
        body = request.get_json(force=True) or {}
        flag = 1 if body.get("archived", True) else 0
        if body.get("unrated"):
            svc.db.x("UPDATE samples SET archived=? WHERE result='unrated' AND archived<>?", (flag, flag))
        else:
            ids = [int(i) for i in body.get("ids", [])]
            if ids:
                svc.db.x(f"UPDATE samples SET archived=? WHERE id IN ({','.join('?' * len(ids))})",
                         (flag, *ids))
        return jsonify(ok=True)

    @app.delete("/api/samples/<int:sid>")
    def delete_sample(sid):
        # The archived gcode is deliberately kept on disk.
        svc.db.x("DELETE FROM samples WHERE id=?", (sid,))
        return jsonify(ok=True)

    @app.get("/api/samples/<int:sid>/gcode")
    def download_gcode(sid):
        s = svc.db.get_sample(sid)
        if not s or not s["gcode_path"]:
            abort(404)
        return send_from_directory(svc.gcode_dir, s["gcode_path"], as_attachment=True,
                                   download_name=Path(s["filename"]).name)

    @app.post("/api/samples/<int:sid>/reprint")
    def reprint(sid):
        body = request.get_json(silent=True) or {}
        try:
            return jsonify(svc.reprint(sid, body.get("printer_id")))
        except MoonrakerError as e:
            return err(str(e), 502)

    # ------------------------------------------------------------- printers
    def printer_json(p):
        out = {k: p[k] for k in ("id", "name", "url", "enabled", "last_sync", "last_error", "last_new",
                                 "sample_count") if k in p}
        out["enabled"] = bool(p["enabled"])
        out["has_key"] = bool(p["api_key"])
        return out

    @app.get("/api/printers")
    def printers():
        return jsonify([printer_json(p) for p in svc.db.list_printers()])

    @app.post("/api/printers/test")
    def test_printer():
        body = request.get_json(force=True) or {}
        try:
            url = normalize_url(body.get("url"))
            key = body.get("api_key")
            if not key and body.get("id"):                 # re-test a saved printer with its stored key
                key = (svc.db.get_printer(int(body["id"])) or {}).get("api_key", "")
            info = Moonraker(url, key or "").server_info()
        except (ValueError, MoonrakerError) as e:
            return err(str(e), 502)
        return jsonify(ok=True, klippy_state=info.get("klippy_state"),
                       moonraker_version=info.get("moonraker_version"), url=url)

    @app.post("/api/printers")
    def add_printer():
        body = request.get_json(force=True) or {}
        try:
            url = normalize_url(body.get("url"))
        except ValueError as e:
            return err(str(e))
        name = (body.get("name") or "").strip() or url.split("//", 1)[1].split(":")[0]
        pid = svc.db.add_printer(name, url, body.get("api_key") or "")
        svc.sync_once(pid)
        return jsonify(printer_json(next(p for p in svc.db.list_printers() if p["id"] == pid)))

    @app.patch("/api/printers/<int:pid>")
    def patch_printer(pid):
        if not svc.db.get_printer(pid):
            abort(404)
        body = request.get_json(force=True) or {}
        f = {}
        if "name" in body and str(body["name"]).strip():
            f["name"] = str(body["name"]).strip()
        if "url" in body:
            try:
                f["url"] = normalize_url(body["url"])
            except ValueError as e:
                return err(str(e))
        if "api_key" in body:
            f["api_key"] = body["api_key"] or ""
        if "enabled" in body:
            f["enabled"] = 1 if body["enabled"] else 0
        svc.db.update_printer(pid, **f)
        return jsonify(printer_json(next(p for p in svc.db.list_printers() if p["id"] == pid)))

    @app.delete("/api/printers/<int:pid>")
    def delete_printer(pid):
        p = next((p for p in svc.db.list_printers() if p["id"] == pid), None)
        if not p:
            abort(404)
        if p["sample_count"]:
            return err(f"{p['name']} has {p['sample_count']} samples; disable it instead of deleting.", 409)
        svc.db.x("DELETE FROM printers WHERE id=?", (pid,))
        return jsonify(ok=True)

    # -------------------------------------------------------------- photos
    @app.post("/api/samples/<int:sid>/photos")
    def add_photos(sid):
        if not svc.db.get_sample(sid):
            abort(404)
        added = []
        for f in request.files.getlist("photo"):
            ext = Path(secure_filename(f.filename or "")).suffix.lower()
            if ext not in (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif"):
                ext = ".jpg"
            d = svc.photo_dir / f"S{sid:04d}"
            d.mkdir(exist_ok=True)
            name = f"{int(time.time())}_{uuid.uuid4().hex[:6]}{ext}"
            f.save(d / name)
            rel = f"S{sid:04d}/{name}"
            pid = svc.db.x("INSERT INTO photos(sample_id, filename, created_at) VALUES(?,?,?)",
                           (sid, rel, time.time()))
            added.append({"id": pid, "filename": rel})
        return jsonify(added)

    @app.delete("/api/photos/<int:pid>")
    def delete_photo(pid):
        p = svc.db.one("SELECT * FROM photos WHERE id=?", (pid,))
        if not p:
            abort(404)
        (svc.photo_dir / p["filename"]).unlink(missing_ok=True)
        svc.db.x("DELETE FROM photos WHERE id=?", (pid,))
        return jsonify(ok=True)

    # ------------------------------------------------------------- compare
    def parse_ids():
        try:
            return [int(x) for x in request.args.get("ids", "").split(",") if x.strip()]
        except ValueError:
            abort(400)

    @app.get("/api/compare")
    def compare():
        ss = [s for s in (svc.db.get_sample(i) for i in parse_ids()) if s]
        keys = sorted({k for s in ss for k in s["settings"]})
        rows = []
        for k in keys:
            vals = [s["settings"].get(k) for s in ss]
            rows.append({"key": k, "values": vals, "differs": len(set(vals)) > 1})
        rows.sort(key=lambda r: (not r["differs"], r["key"]))
        for s in ss:
            s.pop("settings")
        return jsonify(samples=ss, rows=rows)

    # -------------------------------------------------------------- export
    def export_rows(ids, mode):
        ss = [svc.db.get_sample(i) for i in ids] if ids else \
            [svc.db.get_sample(r["id"]) for r in reversed(svc.db.list_samples()) if not r["archived"]]
        ss = [s for s in ss if s]
        if mode == "all":
            keys = sorted({k for s in ss for k in s["settings"]})
        elif mode == "key":
            present = {k for s in ss for k in s["settings"]}
            keys = [k for k in KEY_SETTINGS if k in present]
        else:
            keys = []
        rows = []
        for s in ss:
            row = {
                "sample": s["id"], "model": s["model"], "printer": s["printer_name"] or "", "result": s["result"], "title": s["title"], "tags": s["tags"],
                "notes": s["notes"], "filename": s["filename"], "reprint_of": s["reprint_of"] or "",
                "photos": len(s["photos"]), "moonraker_status": s["moonraker_status"],
                "start_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(s["start_time"]))
                if s["start_time"] else "",
                "print_minutes": round((s["print_duration"] or 0) / 60, 1),
                "filament_used_mm": round(s["filament_used"] or 0, 1),
                "gcode_archive": s["gcode_path"] or "",
            }
            row.update({k: s["settings"].get(k, "") for k in keys})
            rows.append(row)
        return CORE_COLS + keys, rows

    @app.get("/api/export.csv")
    def export_csv():
        ids = parse_ids()
        cols, rows = export_rows(ids, request.args.get("settings", "key"))
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
        # BOM so Excel opens UTF-8 correctly.
        return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=samples.csv"})

    @app.get("/api/export.json")
    def export_json():
        ids = parse_ids()
        out = [svc.db.get_sample(i) for i in ids] if ids else \
            [svc.db.get_sample(r["id"]) for r in reversed(svc.db.list_samples()) if not r["archived"]]
        return Response(json.dumps([s for s in out if s], indent=2), mimetype="application/json",
                        headers={"Content-Disposition": "attachment; filename=samples.json"})

    # ---------------------------------------------------- QR codes / labels
    def sample_url(sid):
        return f"{base_url()}/#/sample/{sid}"

    @app.get("/api/samples/<int:sid>/qr.svg")
    def qr(sid):
        buf = io.BytesIO()
        segno.make(sample_url(sid), error="m").save(buf, kind="svg", scale=4, border=1, xmldecl=False)
        return Response(buf.getvalue(), mimetype="image/svg+xml")

    @app.get("/labels")
    def labels():
        ids = parse_ids()
        cells = []
        for i in ids:
            buf = io.BytesIO()
            segno.make(sample_url(i), error="m").save(buf, kind="svg", scale=3, border=0, xmldecl=False)
            cells.append(f'<div class="l"><b>{i}</b>{buf.getvalue().decode()}</div>')
        html = ("<!doctype html><meta charset=utf-8><title>Labels</title><style>"
                "body{font-family:sans-serif;display:flex;flex-wrap:wrap;gap:6px;margin:10px}"
                ".l{border:1px dashed #888;padding:6px;display:flex;align-items:center;gap:8px}"
                ".l b{font-size:34px}.l svg{width:70px;height:70px}</style>" + "".join(cells) +
                "<script>window.print()</script>")
        return Response(html, mimetype="text/html")

    return app
