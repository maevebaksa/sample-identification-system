"""Configuration: defaults < config.json < SIS_* environment variables."""
import json
import os
from pathlib import Path

DEFAULTS = {
    # Where Moonraker's HTTP API lives (the same machine as Mainsail, usually).
    "moonraker_url": "http://localhost:7125",
    "moonraker_api_key": "",
    # This app's web server.
    "host": "0.0.0.0",
    "port": 8420,
    # Address phones should use in QR codes, e.g. "http://voron.local:8420".
    # Leave empty to use whatever address you browsed to.
    "public_url": "",
    # Where the database, archived gcode and photos are stored.
    "data_dir": "~/sample-identification-data",
    # How often to look for newly finished prints.
    "poll_seconds": 30,
    # On the very first run, import this many of the most recent prints.
    "initial_import_count": 10,
}


def load(path=None):
    cfg = dict(DEFAULTS)
    path = path or os.environ.get("SIS_CONFIG") or "config.json"
    if Path(path).is_file():
        with open(path, encoding="utf-8") as f:
            cfg.update(json.load(f))
    for key, default in DEFAULTS.items():
        env = os.environ.get("SIS_" + key.upper())
        if env is not None:
            cfg[key] = type(default)(env)
    cfg["moonraker_url"] = cfg["moonraker_url"].rstrip("/")
    cfg["public_url"] = cfg["public_url"].rstrip("/")
    cfg["data_dir"] = Path(cfg["data_dir"]).expanduser()
    return cfg
