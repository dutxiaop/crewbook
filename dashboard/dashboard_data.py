import difflib
import functools
import json
import os
import re
import sqlite3
import time
from collections import defaultdict
from contextlib import closing
from pathlib import Path

from learning import PROFILE, clean, connect, role_file, role_version


ROSTER = (
    ("delivery_lead", "交付负责人", "交付", "分派任务、跟进依赖和整单收口", "indigo"),
    ("product_owner", "产品负责人", "产品", "定义用户结果、范围与优先级", "amber"),
    ("product_interaction_designer", "产品与交互设计师", "交互", "页面规格、状态和操作路径", "pink"),
    ("solution_architect", "架构师", "架构", "技术契约、领域边界与演进", "blue"),
    ("development_integration", "开发与集成工程师", "开发", "实现、接线和开发自测", "teal"),
    ("technical_diagnosis", "技术诊断工程师", "诊断", "故障复现、归因和恢复", "blue"),
    ("independent_acceptance", "独立验收工程师", "验收", "候选复验、证据回读与判定", "teal"),
    ("build_release", "构建与发布工程师", "发布", "构建、部署、回滚和生效验证", "amber"),
)
ROLE_NAMES = {row[0] for row in ROSTER}
TEMPORARY_PROJECT = ("temporary", "临时")
REPLAY_WORDS = re.compile(r"离线(?:\s*WAA\s*交接|回放|判断|测试)|只(?:按|依据|根据)(?:这份|以下|给定|所给)材料.{0,45}(?:岗位版本|验收状态|判定)|验收回放|exact configured marker", re.I)


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def registered(profile):
    agents = {}
    for name, title, short, responsibility, color in ROSTER:
        try:
            doc, digest = role_file(profile, name)
        except (OSError, ValueError):
            continue
        agents[name] = {
            "id": name, "name": title, "short": short, "responsibility": responsibility,
            "color": color, "version": role_version(name, doc), "hash": digest,
            "body": doc.get("developer_instructions", ""),
            "config": str(profile / "agents" / f"{name}.toml"),
            "methods": str(profile / "agents" / f"{name}.methods.json"),
        }
    return agents


def readonly(path):
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def parent_id(row):
    try:
        return json.loads(row["source"]).get("subagent", {}).get("thread_spawn", {}).get("parent_thread_id")
    except (AttributeError, TypeError, ValueError):
        return None


def thread_row(state, ident):
    return state.execute("SELECT * FROM threads WHERE id=?", (ident,)).fetchone() if ident else None


def lineage(state, row):
    chain = [row]
    for _ in range(5):
        parent = thread_row(state, parent_id(chain[-1]))
        if parent is None or any(item["id"] == parent["id"] for item in chain):
            break
        chain.append(parent)
    return chain


@functools.lru_cache(maxsize=512)
def workspace_path(value):
    if not isinstance(value, str) or not value:
        return None
    if value.lower().startswith("\\\\?\\unc\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    path = Path(value)
    if not path.is_absolute():
        return None
    try:
        return os.path.normcase(str(path.resolve()))
    except (OSError, RuntimeError, ValueError):
        return os.path.normcase(os.path.normpath(value))


def projects(profile, state):
    app = read_json(profile / ".codex-global-state.json", {})
    if not isinstance(app, dict):
        app = {}
    names, roots, aliases = {}, {}, {}
    for ident, project in app.get("local-projects", {}).items():
        if not isinstance(project, dict):
            continue
        names[ident] = clean(project.get("name"), 65) or "未命名项目"
        for value in project.get("rootPaths", []):
            if canonical := workspace_path(value):
                roots.setdefault(canonical, ident)
    try:
        rows = state.execute("""
            SELECT projects.id, projects.name, project_roots.path FROM projects
            LEFT JOIN project_roots ON project_roots.project_id=projects.id
            ORDER BY projects.position, project_roots.position
            """).fetchall()
    except sqlite3.OperationalError:
        rows = []
    for row in rows:
        ident = row["id"]
        names.setdefault(ident, clean(row["name"], 65) or "未命名项目")
        canonical = workspace_path(row["path"])
        if not canonical:
            continue
        existing = roots.setdefault(canonical, ident)
        if existing != ident and names[existing] == names[ident]:
            aliases[ident] = existing
    return names, roots, aliases, app.get("thread-project-assignments", {}), set(app.get("projectless-thread-ids", []))


def task_project(chain, directory):
    names, roots, aliases, assigned, projectless = directory
    for thread in reversed(chain):
        if thread["id"] in projectless:
            return TEMPORARY_PROJECT
        assignment = assigned.get(thread["id"], {})
        ident = assignment.get("projectId") if isinstance(assignment, dict) else None
        ident = ident or thread["project_id"]
        if ident:
            return aliases.get(ident, ident), names.get(ident, "未命名项目")
    inferred = roots.get(workspace_path(chain[-1]["cwd"]))
    if inferred:
        return aliases.get(inferred, inferred), names[inferred]
    return TEMPORARY_PROJECT


def replay(row):
    prompt = " ".join(str(row[key] or "")[:650] for key in ("first_user_message", "title", "preview"))
    return bool(REPLAY_WORDS.search(prompt))


@functools.lru_cache(maxsize=384)
def recorded_version(path, role, cache_key):
    marker = re.compile(rf"\b{re.escape(role)}/([\d.]+)")
    try:
        with path.open("rb") as file:
            start = file.read(1_600_000)
            file.seek(max(0, path.stat().st_size - 520_000))
            end = file.read(520_000)
        for segment in (start, end):
            for line in segment.splitlines():
                if b'developer' not in line or role.encode() not in line:
                    continue
                try:
                    row = json.loads(line)
                    payload = row.get("payload", {})
                    if payload.get("role") == "developer" and (hit := marker.search(str(payload.get("content", "")))):
                        return hit.group(1)
                except (ValueError, AttributeError):
                    continue
    except OSError:
        pass
    return None


def version_at_start(profile, row):
    try:
        path = Path(row["rollout_path"]).resolve()
        if not any(path.is_relative_to((profile / name).resolve()) for name in ("sessions", "archived_sessions")):
            return None
        info = path.stat()
        return recorded_version(path, row["agent_role"], (info.st_size // 8192, info.st_ctime))
    except (OSError, TypeError, ValueError):
        return None


def latest_text(history, ident, turn_id):
    if not turn_id:
        return "", ""
    rows = history.execute("""
        SELECT item_type, item_json FROM thread_items
        WHERE thread_id=? AND turn_id=? AND item_type IN ('agentMessage','commandExecution','mcpToolCall')
        ORDER BY rollout_ordinal DESC LIMIT 24
        """, (ident, turn_id))
    message, operation = "", ""
    for row in rows:
        try:
            item = json.loads(row["item_json"])
        except ValueError:
            continue
        if row["item_type"] == "agentMessage" and not message:
            message = clean(item.get("text"), 220)
        elif row["item_type"] != "agentMessage" and not operation:
            status = item.get("status")
            if status in {"inProgress", "running"}:
                operation = "正在执行命令" if row["item_type"] == "commandExecution" else "正在调用工具"
    return message, operation


def tasks(profile, roles, now):
    if not roles:
        return []
    slots = ",".join("?" for _ in roles)
    with closing(readonly(profile / "state_5.sqlite")) as state, closing(readonly(profile / "thread_history_1.sqlite")) as history:
        directory = projects(profile, state)
        rows = state.execute(f"""
            SELECT * FROM threads WHERE agent_role IN ({slots}) AND archived=0
            ORDER BY updated_at DESC LIMIT 160
            """, tuple(roles)).fetchall()
        results = []
        for row in rows:
            turn = history.execute("""
                SELECT turn_id, status, started_at, completed_at FROM thread_turns
                WHERE thread_id=? ORDER BY rollout_ordinal DESC LIMIT 1
                """, (row["id"],)).fetchone()
            if turn is None:
                continue
            status = {"inProgress": "running", "completed": "completed", "failed": "failed", "interrupted": "interrupted"}.get(turn["status"], "unknown")
            if status == "running" and now - row["updated_at"] > 300:
                status = "stale"
            if status != "running" and len(results) >= 48:
                continue
            chain = lineage(state, row)
            top = chain[-1]
            project_id, project_name = task_project(chain, directory)
            title = clean(top["name"] or top["title"] or row["title"], 115)
            title = title or clean((row["agent_path"] or "").rsplit("/", 1)[-1].replace("_", " "), 100) or "未命名任务"
            progress, operation = latest_text(history, row["id"], turn["turn_id"])
            results.append({
                "id": row["id"], "root": top["id"], "role": row["agent_role"], "title": title,
                "status": status, "started_at": turn["started_at"], "updated_at": row["updated_at"],
                "completed_at": turn["completed_at"], "message": progress, "operation": operation,
                "version": version_at_start(profile, row), "is_replay": any(replay(item) for item in chain),
                "project": project_name, "project_id": project_id,
            })
        return results


def new_lines(before, after):
    before, after = (text.split("\n## 看板学习回执\n", 1)[0].rstrip() for text in (before, after))
    edits = difflib.SequenceMatcher(a=before.splitlines(), b=after.splitlines(), autojunk=False).get_opcodes()
    lines = after.splitlines()
    return [clean(line.lstrip("- "), 220) for op, _, _, start, end in edits if op in {"replace", "insert"}
            for line in lines[start:end] if line.strip() and not line.startswith("##")][:4]


def observe_configs(db, agents, now):
    db.execute("BEGIN IMMEDIATE")
    try:
        for name, agent in agents.items():
            last = db.execute("SELECT hash FROM role_snapshots WHERE role=? ORDER BY id DESC LIMIT 1", (name,)).fetchone()
            if last is None or last["hash"] != agent["hash"]:
                db.execute("INSERT INTO role_snapshots(role,version,at,hash,body,first_seen) VALUES (?,?,?,?,?,?)",
                           (name, agent["version"], now, agent["hash"], agent["body"], int(last is None)))
        db.commit()
    except sqlite3.Error:
        db.rollback()
        raise


def historical(agents):
    result = []
    for name, agent in agents.items():
        doc = read_json(Path(agent["methods"]), {})
        if not isinstance(doc, dict) or not doc:
            continue
        rules = doc.get("rules", [])
        description = [clean(row.get("decision") or row.get("principle"), 135) for row in rules[:4] if isinstance(row, dict)]
        base = doc.get("prior_replay_version") or ("1.3" if doc.get("personal_decision_preferences") else doc.get("version"))
        result.append({"id": f"archive-{name}", "role": name, "origin": "history", "stage": "archived",
                       "title": "从历史案例建立岗位", "note": "已归档的规则来源与回放。", "details": description,
                       "version": base, "evidence": agent["methods"], "at": None, "steps": []})
        if personal := doc.get("personal_decision_preferences"):
            record = personal.get("replay", {})
            result.append({"id": f"archive-personal-{name}", "role": name, "origin": "user", "stage": "verified",
                           "title": "把个人取舍写入验收规则", "note": clean(record.get("result"), 330),
                           "details": [clean(item.get("rule"), 220) for item in personal.get("rules", [])[:5]],
                           "version": personal.get("version"), "evidence": agent["methods"], "at": None, "steps": []})
        if routing := doc.get("team_routing_verification"):
            result.append({"id": f"archive-team-{name}", "role": name, "origin": "user", "stage": "verified",
                           "title": "建立团队派单规则", "note": clean(routing.get("method"), 260), "details": [],
                           "version": routing.get("phase"), "evidence": agent["methods"], "at": None, "steps": []})
    return result


def learning_history(db, agents, now):
    observe_configs(db, agents, now)
    events = defaultdict(list)
    for row in db.execute("SELECT * FROM learning_events ORDER BY at DESC LIMIT 260"):
        events[row["role"], row["learning_id"]].append(dict(row))
    items = []
    applied_hashes = set()
    for (role, ident), steps in events.items():
        last = steps[0]
        applied_hashes.update(step["role_hash"] for step in steps if step["stage"] == "applied")
        by_stage = {step["stage"]: step for step in steps}
        details = []
        if "accepted" in by_stage and "applied" in by_stage:
            old_body, new_body = (db.execute("SELECT body FROM role_snapshots WHERE role=? AND hash=? ORDER BY id DESC LIMIT 1",
                                             (role, by_stage[stage]["role_hash"])).fetchone()
                                  for stage in ("accepted", "applied"))
            if old_body and new_body:
                details = new_lines(old_body["body"], new_body["body"])
        items.append({"id": f"{role}-{ident}", "role": role, "origin": last["origin"], "stage": last["stage"],
                      "title": last["title"], "note": last["note"], "version": last["version"], "at": last["at"],
                      "task_id": last["task_id"], "evidence": last["evidence"], "details": details,
                      "steps": [{k: row[k] for k in ("stage", "at", "note", "version", "evidence", "task_id")}
                                for row in reversed(steps)]})
    snapshots = db.execute("SELECT * FROM role_snapshots WHERE first_seen=0 ORDER BY at DESC LIMIT 35").fetchall()
    for row in snapshots:
        if row["hash"] in applied_hashes:
            continue
        before = db.execute("SELECT version,body FROM role_snapshots WHERE role=? AND id<? ORDER BY id DESC LIMIT 1",
                            (row["role"], row["id"])).fetchone()
        if before is None:
            continue
        version = row["version"] or "未标版本"
        old = before["version"] or "未标版本"
        changes = new_lines(before["body"], row["body"])
        if not changes and old == version:
            continue
        items.append({"id": f"snapshot-{row['id']}", "role": row["role"], "origin": "file", "stage": "observed",
                      "title": "检测到岗位配置变更", "note": f"v{old} → v{version}" if old != version else f"v{version} 内容发生变化",
                      "details": changes, "steps": [], "version": row["version"],
                      "at": row["at"], "evidence": agents.get(row["role"], {}).get("config")})
    recent = sorted(items, key=lambda item: item["at"], reverse=True)
    archive = sorted(historical(agents), key=lambda item: item["stage"] == "verified", reverse=True)
    return recent + archive


def snapshot(profile=PROFILE, db_path=None):
    now = time.time()
    agents = registered(profile)
    message = None
    try:
        activity = tasks(profile, agents, now)
    except sqlite3.Error:
        activity, message = [], "暂时无法读取 Codex 任务状态"
    with closing(connect(db_path) if db_path else connect()) as db:
        learning = learning_history(db, agents, now)
    staff = [{key: value for key, value in agent.items() if key not in {"body", "hash"}} for agent in agents.values()]
    return {"at": now, "staff": staff, "tasks": activity, "learning": learning, "task_error": message}
