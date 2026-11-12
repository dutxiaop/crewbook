import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / ".data" / "learning.sqlite"
PROFILE = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".codex"
STAGES = ("proposed", "accepted", "applied", "verified", "rejected")
IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,95}$")
THREAD_ID = re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}$")
SECRET = re.compile(r"(?i)(?:sk-[a-z0-9_-]{12,}|bearer\s+[a-z0-9_.-]{10,}|(?:api[_ -]?key|password|access[_ -]?key|token|secret|凭据|密码)\s*[:=：]\s*[^\s,，。]+)")


def clean(value, maximum=600):
    if not isinstance(value, str):
        return ""
    return SECRET.sub("[已隐藏]", " ".join(value.split()))[:maximum]


def connect(path=DATA):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=8)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS learning_events (
            role TEXT NOT NULL, learning_id TEXT NOT NULL, stage TEXT NOT NULL,
            origin TEXT NOT NULL, at REAL NOT NULL, task_id TEXT, title TEXT NOT NULL,
            note TEXT NOT NULL, version TEXT, evidence TEXT, role_hash TEXT,
            PRIMARY KEY (role, learning_id, stage)
        );
        CREATE TABLE IF NOT EXISTS role_snapshots (
            id INTEGER PRIMARY KEY, role TEXT NOT NULL, version TEXT, at REAL NOT NULL,
            hash TEXT NOT NULL, body TEXT NOT NULL, first_seen INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS snapshots_role ON role_snapshots(role, id DESC);
    """)
    return db


def role_file(profile, role):
    if not IDENTIFIER.fullmatch(role):
        raise ValueError("岗位 ID 无效")
    raw = (profile / "agents" / f"{role}.toml").read_bytes()
    doc = tomllib.loads(raw.decode("utf-8-sig"))
    if doc.get("name") != role:
        raise ValueError("岗位文件名与 name 不一致")
    return doc, hashlib.sha256(raw).hexdigest()


def role_version(role, doc):
    match = re.search(rf"\b{re.escape(role)}/([\d.]+)", doc.get("developer_instructions", ""))
    return match.group(1) if match else None


def reference(value):
    if not isinstance(value, str) or not value or len(value) > 450:
        return None
    if value.startswith("codex://threads/") and THREAD_ID.fullmatch(value.rsplit("/", 1)[-1]):
        return value
    if Path(value).is_absolute() and Path(value).is_file():
        return str(Path(value).resolve())
    return None


def record(db, profile, *, role, learning_id, stage, origin, task_id=None,
           title, note, version=None, evidence=None):
    if not IDENTIFIER.fullmatch(learning_id) or stage not in STAGES or origin not in {"user", "autonomous"}:
        raise ValueError("学习编号、阶段或来源无效")
    title, note = clean(title, 100), clean(note)
    if not title or not note:
        raise ValueError("请提供变更标题与简短说明")
    if task_id and not THREAD_ID.fullmatch(task_id):
        raise ValueError("任务 ID 无效")
    doc, role_hash = role_file(profile, role)
    current = role_version(role, doc)
    if version and not VERSION.fullmatch(version):
        raise ValueError("版本格式无效")
    if version and version != current:
        raise ValueError("提交的版本与当前岗位文件不一致")
    evidence = reference(evidence)
    db.execute("BEGIN IMMEDIATE")
    try:
        rows = {row["stage"]: row for row in db.execute(
            "SELECT * FROM learning_events WHERE role=? AND learning_id=?", (role, learning_id))}
        if stage in rows:
            db.rollback()
            return False
        if rows and any(row["origin"] != origin for row in rows.values()):
            raise ValueError("同一学习编号必须使用一致的来源")
        if "rejected" in rows or (stage == "rejected" and ("applied" in rows or "verified" in rows)):
            raise ValueError("学习事件已经结束")
        if stage == "proposed" and rows:
            raise ValueError("请先记录提议阶段")
        if stage == "accepted" and not rows and origin == "autonomous":
            raise ValueError("自主提议先记录 proposed，获准后再记录 accepted")
        if stage == "applied":
            if "accepted" not in rows or not version or version != current:
                raise ValueError("写入阶段需要已接收反馈，且岗位文件版本与回执一致")
            if rows["accepted"]["role_hash"] == role_hash or rows["accepted"]["version"] == version:
                raise ValueError("岗位文件及版本需要发生实际变更")
        if stage == "verified":
            if "applied" not in rows or not evidence or not version or version != current or version != rows["applied"]["version"]:
                raise ValueError("复验阶段需要已写入的新版本和实际证据引用")
        db.execute("INSERT INTO learning_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (role, learning_id, stage, origin, time.time(), task_id, title, note,
                    version or current, evidence, role_hash))
        db.commit()
        return True
    except (ValueError, sqlite3.Error):
        db.rollback()
        raise


def main():
    parser = argparse.ArgumentParser(description="记录员工的长期反馈或自主提议阶段。")
    parser.add_argument("--role", required=True)
    parser.add_argument("--id", dest="learning_id", required=True)
    parser.add_argument("--stage", choices=STAGES, required=True)
    parser.add_argument("--origin", choices=("user", "autonomous"), required=True)
    parser.add_argument("--task", dest="task_id", default=os.environ.get("CODEX_THREAD_ID"))
    parser.add_argument("--title", required=True)
    parser.add_argument("--note", required=True)
    parser.add_argument("--version")
    parser.add_argument("--evidence")
    args = parser.parse_args()
    try:
        with connect() as db:
            changed = record(db, PROFILE, **vars(args))
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(2, f"{error}\n")
    print(json.dumps({"recorded": changed, "learning_id": args.learning_id, "stage": args.stage}, ensure_ascii=False))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
