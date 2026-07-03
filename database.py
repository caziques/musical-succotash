import sqlite3
import json
import os
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path

READINGS_TABLE = """
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    data TEXT NOT NULL
)
"""

READINGS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_timestamp ON readings(timestamp DESC)
"""

USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'readonly',
    created_at REAL NOT NULL
)
"""

SETTINGS_TABLE = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

RULES_TABLE = """
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    trigger_field TEXT NOT NULL,
    trigger_op TEXT NOT NULL,
    trigger_value REAL NOT NULL,
    action_type TEXT NOT NULL DEFAULT 'webhook',
    action_url TEXT NOT NULL DEFAULT '',
    action_body TEXT NOT NULL DEFAULT '{}',
    cooldown_seconds INTEGER NOT NULL DEFAULT 300,
    last_fired REAL DEFAULT 0,
    created_at REAL NOT NULL
)
"""

DEFAULT_ADMIN_USERNAME = "admin"


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute(READINGS_TABLE)
        self.conn.execute(READINGS_INDEX)
        self.conn.execute(USERS_TABLE)
        self.conn.execute(SETTINGS_TABLE)
        self.conn.execute(RULES_TABLE)
        self.conn.commit()
        self._ensure_default_admin()

    def _ensure_default_admin(self):
        row = self.conn.execute("SELECT id FROM users WHERE username = ?", (DEFAULT_ADMIN_USERNAME,)).fetchone()
        if not row:
            from werkzeug.security import generate_password_hash
            pwd = secrets.token_urlsafe(10)
            print(f"\n*** Default admin user created: admin / {pwd} ***\n")
            self.conn.execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                (DEFAULT_ADMIN_USERNAME, generate_password_hash(pwd), "admin", time.time()),
            )
            self.conn.commit()

    def ensure_defaults(self):
        defaults = {
            "inverter_type": "Sunsynk",
            "git_repo": "https://github.com/caziques/musical-succotash",
            "weather_api_name": "OpenWeatherMap",
            "weather_key": "e5230e8094823a62715334531f99616a",
            "weather_city": "Johannesburg,ZA",
            "timezone": "Africa/Johannesburg",
            "port": "/dev/ttyUSB0",
            "baudrate": "9600",
            "slave_id": "1",
            "poll_interval": "5",
            "retention_days": "365",
        }
        for k, v in defaults.items():
            if self.get_setting(k) is None:
                self.set_setting(k, v)

    # --- Readings ---

    def insert(self, data: dict) -> None:
        ts = data.pop("timestamp", time.time())
        self.conn.execute(
            "INSERT INTO readings (timestamp, data) VALUES (?, ?)",
            (ts, json.dumps(data)),
        )
        self.conn.commit()

    def query_range(self, start_ts: float, end_ts: float) -> list[dict]:
        cursor = self.conn.execute(
            "SELECT timestamp, data FROM readings WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC",
            (start_ts, end_ts),
        )
        results = []
        for ts, data_json in cursor:
            record = json.loads(data_json)
            record["timestamp"] = ts
            results.append(record)
        return results

    def purge_old(self, retention_days: int) -> int:
        cutoff = time.time() - (retention_days * 86400)
        cursor = self.conn.execute(
            "DELETE FROM readings WHERE timestamp < ?", (cutoff,)
        )
        self.conn.commit()
        self.conn.execute("PRAGMA optimize")
        return cursor.rowcount

    def latest(self) -> dict | None:
        cursor = self.conn.execute(
            "SELECT timestamp, data FROM readings ORDER BY timestamp DESC LIMIT 1"
        )
        row = cursor.fetchone()
        if row:
            record = json.loads(row[1])
            record["timestamp"] = row[0]
            return record
        return None

    # --- Users ---

    def get_user(self, username: str) -> dict | None:
        row = self.conn.execute(
            "SELECT id, username, password_hash, role FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if row:
            return {"id": row[0], "username": row[1], "password_hash": row[2], "role": row[3]}
        return None

    def list_users(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, username, role, created_at FROM users ORDER BY id"
        ).fetchall()
        return [{"id": r[0], "username": r[1], "role": r[2], "created_at": r[3]} for r in rows]

    def create_user(self, username: str, password_hash: str, role: str) -> bool:
        try:
            self.conn.execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                (username, password_hash, role, time.time()),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def delete_user(self, username: str) -> bool:
        if username == DEFAULT_ADMIN_USERNAME:
            return False
        cursor = self.conn.execute("DELETE FROM users WHERE username = ?", (username,))
        self.conn.commit()
        return cursor.rowcount > 0

    def update_user(self, username: str, password_hash: str | None, role: str | None) -> bool:
        if password_hash:
            self.conn.execute(
                "UPDATE users SET password_hash = ? WHERE username = ?",
                (password_hash, username),
            )
        if role and username != DEFAULT_ADMIN_USERNAME:
            self.conn.execute(
                "UPDATE users SET role = ? WHERE username = ?",
                (role, username),
            )
        self.conn.commit()
        return True

    # --- Settings ---

    def get_setting(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
            (key, value, value),
        )
        self.conn.commit()

    def get_all_settings(self) -> dict:
        rows = self.conn.execute("SELECT key, value FROM settings").fetchall()
        return {r[0]: r[1] for r in rows}

    # --- Rules ---

    def list_rules(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, name, enabled, trigger_field, trigger_op, trigger_value, "
            "action_type, action_url, action_body, cooldown_seconds, last_fired, created_at "
            "FROM rules ORDER BY id"
        ).fetchall()
        return [
            {"id": r[0], "name": r[1], "enabled": bool(r[2]), "trigger_field": r[3],
             "trigger_op": r[4], "trigger_value": r[5], "action_type": r[6],
             "action_url": r[7], "action_body": r[8], "cooldown_seconds": r[9],
             "last_fired": r[10], "created_at": r[11]} for r in rows
        ]

    def create_rule(self, rule: dict) -> int:
        c = self.conn.execute(
            "INSERT INTO rules (name, enabled, trigger_field, trigger_op, trigger_value, "
            "action_type, action_url, action_body, cooldown_seconds, created_at) "
            "VALUES (?,1,?,?,?,?,?,?,?,?)",
            (rule["name"], rule["trigger_field"], rule["trigger_op"], rule["trigger_value"],
             rule.get("action_type", "webhook"), rule.get("action_url", ""),
             rule.get("action_body", "{}"), rule.get("cooldown_seconds", 300), time.time()),
        )
        self.conn.commit()
        return c.lastrowid

    def update_rule(self, rule_id: int, rule: dict) -> bool:
        fields = []
        vals = []
        for k in ["name", "enabled", "trigger_field", "trigger_op", "trigger_value",
                   "action_type", "action_url", "action_body", "cooldown_seconds"]:
            if k in rule:
                fields.append(f"{k}=?")
                vals.append(rule[k] if k != "enabled" else (1 if rule[k] else 0))
        if not fields:
            return False
        vals.append(rule_id)
        self.conn.execute(f"UPDATE rules SET {', '.join(fields)} WHERE id=?", vals)
        self.conn.commit()
        return True

    def delete_rule(self, rule_id: int) -> bool:
        c = self.conn.execute("DELETE FROM rules WHERE id=?", (rule_id,))
        self.conn.commit()
        return c.rowcount > 0

    def get_rules_to_evaluate(self) -> list[dict]:
        """Return enabled rules."""
        rows = self.conn.execute(
            "SELECT id, name, enabled, trigger_field, trigger_op, trigger_value, "
            "action_type, action_url, action_body, cooldown_seconds, last_fired, created_at "
            "FROM rules WHERE enabled=1 ORDER BY id"
        ).fetchall()
        return [
            {"id": r[0], "name": r[1], "enabled": bool(r[2]), "trigger_field": r[3],
             "trigger_op": r[4], "trigger_value": r[5], "action_type": r[6],
             "action_url": r[7], "action_body": r[8], "cooldown_seconds": r[9],
             "last_fired": r[10], "created_at": r[11]} for r in rows
        ]

    def set_rule_last_fired(self, rule_id: int, ts: float) -> None:
        self.conn.execute("UPDATE rules SET last_fired=? WHERE id=?", (ts, rule_id))
        self.conn.commit()

    def close(self):
        self.conn.close()
