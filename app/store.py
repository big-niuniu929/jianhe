# -*- coding: utf-8 -*-
"""OfferFit · 本地存储（SQLite / WAL）

第一版只服务「单机单人」：一个 profile、若干练习场次、若干投递记录。
表设计故意留松：会话存 JSON，等数据结构稳定后再拆表。
"""
import json
import sqlite3
import time
import uuid
from pathlib import Path


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init()

    def _init(self):
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS profile (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company TEXT NOT NULL,
                role TEXT NOT NULL,
                stage TEXT NOT NULL DEFAULT '已投',
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS variants (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                jd TEXT DEFAULT '',
                profile TEXT NOT NULL,
                is_active INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    # ---------------- profile ----------------
    def get_profile(self):
        row = self.conn.execute("SELECT data FROM profile ORDER BY updated_at DESC LIMIT 1").fetchone()
        return json.loads(row["data"]) if row else None

    def save_profile(self, profile):
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        pid = profile.get("id") or ("u_" + uuid.uuid4().hex[:10])
        profile["id"] = pid
        profile["updatedAt"] = now
        data = json.dumps(profile, ensure_ascii=False)
        self.conn.execute(
            "INSERT INTO profile(id, data, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
            (pid, data, now),
        )
        self.conn.commit()
        return profile

    # ---------------- sessions ----------------
    def create_session(self, data):
        sid = "pr_" + uuid.uuid4().hex[:10]
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        data["id"] = sid
        data["createdAt"] = now
        data["status"] = "running"
        self.conn.execute(
            "INSERT INTO sessions(id, data, created_at, updated_at) VALUES(?,?,?,?)",
            (sid, json.dumps(data, ensure_ascii=False), now, now),
        )
        self.conn.commit()
        return sid

    def get_session(self, sid):
        row = self.conn.execute("SELECT data FROM sessions WHERE id=?", (sid,)).fetchone()
        return json.loads(row["data"]) if row else None

    def save_session(self, sid, data):
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute(
            "UPDATE sessions SET data=?, updated_at=? WHERE id=?",
            (json.dumps(data, ensure_ascii=False), now, sid),
        )
        self.conn.commit()

    def list_sessions(self, limit=10):
        rows = self.conn.execute("SELECT data FROM sessions ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = json.loads(r["data"])
            out.append({
                "id": d.get("id"),
                "role": d.get("role"),
                "mode": d.get("mode"),
                "persona": d.get("persona"),
                "status": d.get("status"),
                "createdAt": d.get("createdAt"),
                "total": (d.get("report") or {}).get("total"),
                "answered": sum(1 for q in d.get("questions", []) if q.get("answer")),
                "questions": len(d.get("questions", [])),
            })
        return out

    def latest_report(self):
        rows = self.conn.execute("SELECT data FROM sessions ORDER BY created_at DESC LIMIT 20").fetchall()
        for r in rows:
            d = json.loads(r["data"])
            if d.get("report"):
                return {"session": {k: d.get(k) for k in ("id", "role", "mode", "persona", "createdAt")},
                        "report": d["report"]}
        return None

    # ---------------- variants（一岗一版） ----------------
    def ensure_base_variant(self, profile):
        row = self.conn.execute("SELECT COUNT(*) AS n FROM variants").fetchone()
        if row["n"] == 0:
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            self.conn.execute(
                "INSERT INTO variants(id, name, jd, profile, is_active, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                ("base", "通用底版", "", json.dumps(profile, ensure_ascii=False), 1, now, now),
            )
            self.conn.commit()

    def list_variants(self):
        rows = self.conn.execute(
            "SELECT id, name, jd, profile, is_active, created_at, updated_at FROM variants ORDER BY is_active DESC, created_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_variant(self, vid):
        row = self.conn.execute("SELECT * FROM variants WHERE id=?", (vid,)).fetchone()
        return dict(row) if row else None

    def create_variant(self, name, jd, profile):
        vid = "v_" + uuid.uuid4().hex[:10]
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute(
            "INSERT INTO variants(id, name, jd, profile, is_active, created_at, updated_at) VALUES(?,?,?,?,0,?,?)",
            (vid, name, jd or "", json.dumps(profile, ensure_ascii=False), now, now),
        )
        self.conn.commit()
        return vid

    def save_variant_profile(self, vid, profile):
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute(
            "UPDATE variants SET profile=?, updated_at=? WHERE id=?",
            (json.dumps(profile, ensure_ascii=False), now, vid),
        )
        self.conn.commit()

    def set_active_variant(self, vid):
        self.conn.execute("UPDATE variants SET is_active=0")
        self.conn.execute("UPDATE variants SET is_active=1, updated_at=? WHERE id=?", (time.strftime("%Y-%m-%d %H:%M:%S"), vid))
        self.conn.commit()

    def active_variant_id(self):
        row = self.conn.execute("SELECT id FROM variants WHERE is_active=1 LIMIT 1").fetchone()
        return row["id"] if row else None

    def delete_variant(self, vid):
        if vid == "base":
            return False
        self.conn.execute("DELETE FROM variants WHERE id=?", (vid,))
        self.conn.commit()
        return True

    # ---------------- reset ----------------
    def reset_all(self):
        for table in ("sessions", "applications", "variants", "profile"):
            try:
                self.conn.execute("DELETE FROM " + table)
            except Exception:
                pass
        self.conn.commit()

    # ---------------- applications ----------------
    def list_apps(self):
        rows = self.conn.execute(
            "SELECT id, company, role, stage, note, created_at FROM applications ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def update_app(self, app_id, stage=None, note=None):
        fields, vals = [], []
        if stage is not None:
            fields.append("stage=?"); vals.append(stage)
        if note is not None:
            fields.append("note=?"); vals.append(note)
        if not fields:
            return False
        vals.append(app_id)
        self.conn.execute("UPDATE applications SET " + ", ".join(fields) + " WHERE id=?", vals)
        self.conn.commit()
        return True

    def delete_app(self, app_id):
        self.conn.execute("DELETE FROM applications WHERE id=?", (app_id,))
        self.conn.commit()
        return True

    def add_app(self, company, role, stage="已投", note=""):
        now = time.strftime("%Y-%m-%d")
        self.conn.execute(
            "INSERT INTO applications(company, role, stage, note, created_at) VALUES(?,?,?,?,?)",
            (company, role, stage, note, now),
        )
        self.conn.commit()
        return self.list_apps()
