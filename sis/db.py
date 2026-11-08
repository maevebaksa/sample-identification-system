import json
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS printers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT NOT NULL,                         -- Moonraker base URL
    api_key TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    baseline_time REAL,                        -- only prints started after this are imported
    last_sync REAL, last_error TEXT, last_new INTEGER DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,      -- the sample number you write on the part
    printer_id INTEGER,                        -- which printer made it
    job_id TEXT UNIQUE,                        -- '<printer_id>:<Moonraker job id>'
    filename TEXT NOT NULL,                    -- path inside Moonraker's gcodes root
    gcode_path TEXT,                           -- archived copy, relative to data_dir/gcodes
    gcode_sha256 TEXT,
    reprint_of INTEGER,                        -- earlier sample with identical gcode
    result TEXT NOT NULL DEFAULT 'unrated',    -- unrated | success | fail
    archived INTEGER NOT NULL DEFAULT 0,       -- 1 = hidden in the Archive (sample no longer exists)
    moonraker_status TEXT,
    start_time REAL, end_time REAL,
    print_duration REAL, total_duration REAL, filament_used REAL,
    model TEXT NOT NULL DEFAULT '',            -- source model (STL/3MF) name, auto-detected or edited
    model_key TEXT,                            -- normalised model name used to group samples
    title TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    settings TEXT NOT NULL DEFAULT '{}',
    archive_error TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id INTEGER NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


class DB:
    def __init__(self, path):
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            cols = {r[1] for r in self.conn.execute("PRAGMA table_info(samples)")}
            if "archived" not in cols:   # migrate databases created by v0.1.0
                self.conn.execute("ALTER TABLE samples ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
                self.conn.commit()
            for col, ddl in (("printer_id", "INTEGER"),               # single-printer databases
                             ("model", "TEXT NOT NULL DEFAULT ''"),    # source model (STL) name
                             ("model_key", "TEXT")):                   # normalised, for grouping
                if col not in cols:
                    self.conn.execute(f"ALTER TABLE samples ADD COLUMN {col} {ddl}")
                    self.conn.commit()

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def x(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur.lastrowid

    def get_meta(self, key, default=None):
        r = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return r["value"] if r else default

    def set_meta(self, key, value):
        self.x("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, str(value)))

    # --- samples -----------------------------------------------------------
    def add_sample(self, **f):
        f.setdefault("created_at", time.time())
        cols = ",".join(f)
        return self.x(f"INSERT INTO samples({cols}) VALUES({','.join('?' * len(f))})", tuple(f.values()))

    def update_sample(self, sid, **f):
        if not f:
            return
        sets = ",".join(f"{k}=?" for k in f)
        self.x(f"UPDATE samples SET {sets} WHERE id=?", (*f.values(), sid))

    def get_sample(self, sid):
        s = self.one("SELECT s.*, (SELECT name FROM printers p WHERE p.id=s.printer_id) AS printer_name "
                     "FROM samples s WHERE s.id=?", (sid,))
        if s:
            s["settings"] = json.loads(s["settings"] or "{}")
            s["photos"] = self.q("SELECT * FROM photos WHERE sample_id=? ORDER BY id", (sid,))
        return s

    def list_samples(self):
        rows = self.q(
            "SELECT s.*, (SELECT name FROM printers p WHERE p.id=s.printer_id) AS printer_name,"
            " (SELECT COUNT(*) FROM photos p WHERE p.sample_id=s.id) AS photo_count,"
            " (SELECT filename FROM photos p WHERE p.sample_id=s.id ORDER BY id DESC LIMIT 1) AS thumb"
            " FROM samples s ORDER BY s.id DESC")
        for r in rows:
            r.pop("settings", None)
        return rows

    # --- printers ----------------------------------------------------------
    def list_printers(self):
        return self.q("SELECT p.*, (SELECT COUNT(*) FROM samples s WHERE s.printer_id=p.id) AS sample_count "
                      "FROM printers p ORDER BY p.id")

    def get_printer(self, pid):
        return self.one("SELECT * FROM printers WHERE id=?", (pid,))

    def add_printer(self, name, url, api_key="", baseline_time=None):
        return self.x("INSERT INTO printers(name,url,api_key,baseline_time,created_at) VALUES(?,?,?,?,?)",
                      (name, url, api_key, baseline_time, time.time()))

    def update_printer(self, pid, **f):
        if f:
            self.x(f"UPDATE printers SET {','.join(k + '=?' for k in f)} WHERE id=?", (*f.values(), pid))
