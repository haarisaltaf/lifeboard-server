"""SQLite layer for lifeboard: schema, connection, and FTS5 search index.

Single-file database. Notes and journal entries share one table (`entries`)
and one FTS index, so a single search hits both with a kind filter.
"""
import sqlite3
import os
import json
from datetime import datetime

DATA_DIR = os.environ.get("LIFEBOARD_DATA", os.path.join(os.path.dirname(__file__), "data"))
DB_PATH = os.path.join(DATA_DIR, "lifeboard.db")


def get_conn():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS tabs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    position   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS widgets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    tab_id     INTEGER NOT NULL REFERENCES tabs(id) ON DELETE CASCADE,
    type       TEXT NOT NULL,          -- habit | counter | number | progress | todo | note | timer
    title      TEXT NOT NULL,
    config     TEXT NOT NULL DEFAULT '{}',
    position   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

-- one numeric value per widget per day (habit/counter/number/progress/timer)
CREATE TABLE IF NOT EXISTS logs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    widget_id INTEGER NOT NULL REFERENCES widgets(id) ON DELETE CASCADE,
    day       TEXT NOT NULL,           -- YYYY-MM-DD
    value     REAL NOT NULL DEFAULT 0,
    UNIQUE(widget_id, day)
);
CREATE INDEX IF NOT EXISTS idx_logs_widget ON logs(widget_id, day);

CREATE TABLE IF NOT EXISTS todos (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    widget_id INTEGER NOT NULL REFERENCES widgets(id) ON DELETE CASCADE,
    text      TEXT NOT NULL,
    done      INTEGER NOT NULL DEFAULT 0,
    position  INTEGER NOT NULL DEFAULT 0
);

-- per-day state for a 75 Hard challenge widget: which tasks are checked off
-- (JSON {taskKey: true}) plus an optional progress-photo path (relative to the
-- data dir). One row per (widget, day); a day with all tasks + photo "passes".
CREATE TABLE IF NOT EXISTS hard75 (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    widget_id INTEGER NOT NULL REFERENCES widgets(id) ON DELETE CASCADE,
    day       TEXT NOT NULL,           -- YYYY-MM-DD
    tasks     TEXT NOT NULL DEFAULT '{}',
    photo     TEXT,                    -- relative path under the data dir, or NULL
    UNIQUE(widget_id, day)
);
CREATE INDEX IF NOT EXISTS idx_hard75_widget ON hard75(widget_id, day);

-- kaizen ("light mode"): the daily ritual lives here, one row per day.
CREATE TABLE IF NOT EXISTS kaizen_days (
    day            TEXT PRIMARY KEY,        -- YYYY-MM-DD
    highlight      TEXT NOT NULL DEFAULT '',
    highlight_done INTEGER NOT NULL DEFAULT 0,
    braindump      TEXT NOT NULL DEFAULT '',
    updated_at     TEXT NOT NULL
);

-- tiny 2-minute "show up" habits; completion is forgiving like the rest of lifeboard
CREATE TABLE IF NOT EXISTS kaizen_commitments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    text       TEXT NOT NULL,
    position   INTEGER NOT NULL DEFAULT 0,
    archived   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kaizen_logs (
    commitment_id INTEGER NOT NULL REFERENCES kaizen_commitments(id) ON DELETE CASCADE,
    day           TEXT NOT NULL,
    done          INTEGER NOT NULL DEFAULT 1,
    UNIQUE(commitment_id, day)
);
CREATE INDEX IF NOT EXISTS idx_kaizen_logs ON kaizen_logs(commitment_id, day);

-- unified store for the second brain: notes + journal entries
CREATE TABLE IF NOT EXISTS entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL DEFAULT 'note',   -- note | journal
    title      TEXT NOT NULL DEFAULT '',
    body       TEXT NOT NULL DEFAULT '',
    entry_date TEXT,                            -- journal: YYYY-MM-DD
    slot       TEXT,                            -- journal: am | pm
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS journal_prompts (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    text   TEXT NOT NULL,
    slot   TEXT NOT NULL DEFAULT 'pm',          -- am | pm | any
    active INTEGER NOT NULL DEFAULT 1
);

-- full-text index over the unified entries store
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
    title, body, content='entries', content_rowid='id', tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
    INSERT INTO entries_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
END;
CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, title, body) VALUES ('delete', old.id, old.title, old.body);
END;
CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, title, body) VALUES ('delete', old.id, old.title, old.body);
    INSERT INTO entries_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
END;
"""

DEFAULT_PROMPTS = [
    ("Write your own honest obituary based on what you did today.", "pm"),
    ("What did you avoid today, and what was it really about?", "pm"),
    ("If today repeated for a year, where would it take you?", "pm"),
    ("What mattered today that won't matter in a week? What mattered that will?", "pm"),
    ("What are the three things that would make today a win?", "am"),
    ("Who do you want to be by tonight?", "am"),
    ("What's the one task you're tempted to push to tomorrow? Do it first.", "am"),
]


def _column_names(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate(conn):
    """Idempotent schema upgrades so existing v1 databases survive."""
    # v2: per-prompt weekday scheduling (comma list of 0=Mon..6=Sun; empty = any day)
    if "weekdays" not in _column_names(conn, "journal_prompts"):
        conn.execute("ALTER TABLE journal_prompts ADD COLUMN weekdays TEXT NOT NULL DEFAULT ''")
    _migrate_kaizen_braindumps(conn)


def _migrate_kaizen_braindumps(conn):
    """One-time: move any kaizen_days.braindump text into the entries store as
    dated journal 'dump' entries, so older brain dumps become browsable and
    searchable. Idempotent — clears the column as it goes, so reruns are no-ops."""
    try:
        rows = conn.execute("SELECT day, braindump FROM kaizen_days WHERE braindump <> ''").fetchall()
    except sqlite3.OperationalError:
        return  # kaizen_days not created yet
    for r in rows:
        day, body = r["day"], r["braindump"]
        exists = conn.execute(
            "SELECT 1 FROM entries WHERE kind='journal' AND slot='dump' AND entry_date=?", (day,)).fetchone()
        if not exists:
            ts = datetime.utcnow().isoformat()
            conn.execute(
                "INSERT INTO entries(kind,title,body,entry_date,slot,created_at,updated_at) "
                "VALUES ('journal',?,?,?,'dump',?,?)", (f"Brain dump — {day}", body, day, ts, ts))
        conn.execute("UPDATE kaizen_days SET braindump='' WHERE day=?", (day,))


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    _migrate(conn)
    # seed journal prompts once
    n = conn.execute("SELECT COUNT(*) FROM journal_prompts").fetchone()[0]
    if n == 0:
        conn.executemany(
            "INSERT INTO journal_prompts(text, slot, active) VALUES (?,?,1)",
            DEFAULT_PROMPTS,
        )
    # seed a starter tab if empty
    t = conn.execute("SELECT COUNT(*) FROM tabs").fetchone()[0]
    if t == 0:
        now = datetime.utcnow().isoformat()
        conn.execute("INSERT INTO tabs(name, position, created_at) VALUES (?,?,?)",
                     ("Dashboard-example", 0, now))
    conn.commit()
    conn.close()


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings(key,value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
