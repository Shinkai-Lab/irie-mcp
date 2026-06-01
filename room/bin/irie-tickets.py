#!/usr/bin/env python3
"""irie-tickets.py — irie 会議室システムのチケット管理（BTS）

iried.pyと同じ設計: stdin JSON → stdout JSON、flock排他。
HTTPラッパーからsubprocess.runで呼ばれる想定。

コマンド:
  create  {title, assignee?, created_by}  → チケット作成
  list    {}                               → 全チケット一覧
  update  {id, status?, assignee?, title?} → チケット更新
  get     {id}                             → チケット詳細
"""
import sys
import os
import json
import fcntl
import datetime
from pathlib import Path

DEFAULT_ROOM = str(Path(__file__).resolve().parents[1])
ROOM = os.environ.get("IRIE_ROOM") or os.environ.get("KAIGI_ROOM") or DEFAULT_ROOM
CONFIG_FILE = os.path.join(ROOM, "config.json")
TICKETS_FILE = os.path.join(ROOM, "tickets.jsonl")
LOCK_FILE = os.path.join(ROOM, ".tickets.lock")
VALID_STATUS = {"open", "in_progress", "done", "closed"}
VALID_ROLES = {"human", "ai"}


class ConfigError(ValueError):
    pass


def now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def ensure_room():
    os.makedirs(ROOM, exist_ok=True)


def load_members():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        return []
    except json.JSONDecodeError as e:
        raise ConfigError(f"config.jsonが壊れています: {e}") from e

    members = cfg.get("members", [])
    if not isinstance(members, list):
        raise ConfigError("config.membersは配列である必要があります")

    normalized = []
    for i, member in enumerate(members):
        if not isinstance(member, dict):
            raise ConfigError(f"members[{i}]はobjectである必要があります")
        name = member.get("name")
        role = member.get("role")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"members[{i}].nameは空でない文字列である必要があります")
        if role not in VALID_ROLES:
            raise ConfigError(f"members[{i}].roleはhumanまたはaiである必要があります")
        normalized.append({"name": name.strip(), "role": role})
    return normalized


def allowed_assignees():
    return {m["name"] for m in load_members()}


def validate_assignee(assignee):
    if not assignee:
        return None
    try:
        allowed = allowed_assignees()
    except ConfigError as e:
        return str(e)
    if assignee not in allowed:
        return f"未許可のassignee: {assignee}"
    return None


def read_tickets():
    tickets = []
    if not os.path.exists(TICKETS_FILE):
        return tickets
    with open(TICKETS_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                tickets.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return tickets


def next_id(tickets):
    if not tickets:
        return 1
    return max(t.get("id", 0) for t in tickets) + 1


def write_tickets(tickets):
    ensure_room()
    with open(TICKETS_FILE, "w", encoding="utf-8") as f:
        for t in tickets:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")


def cmd_create(req):
    title = (req.get("title") or "").strip()
    if not title:
        return {"ok": False, "error": "titleは必須です"}
    created_by = req.get("created_by", "system")
    assignee = req.get("assignee")
    assignee_error = validate_assignee(assignee)
    if assignee_error:
        return {"ok": False, "error": assignee_error}

    ensure_room()
    with open(LOCK_FILE, "w") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        tickets = read_tickets()
        ticket = {
            "id": next_id(tickets),
            "title": title,
            "description": req.get("description", ""),
            "status": "open",
            "assignee": assignee,
            "created_by": created_by,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "comments": [],
        }
        tickets.append(ticket)
        write_tickets(tickets)

    return {"ok": True, "ticket": ticket}


def cmd_list(req):
    tickets = read_tickets()
    status_filter = req.get("status")
    assignee_filter = req.get("assignee")
    if status_filter:
        tickets = [t for t in tickets if t.get("status") == status_filter]
    if assignee_filter:
        tickets = [t for t in tickets if t.get("assignee") == assignee_filter]
    return {"ok": True, "tickets": tickets, "count": len(tickets)}


def cmd_get(req):
    tid = req.get("id")
    if tid is None:
        return {"ok": False, "error": "idは必須です"}
    tickets = read_tickets()
    for t in tickets:
        if t.get("id") == int(tid):
            return {"ok": True, "ticket": t}
    return {"ok": False, "error": f"チケット #{tid} が見つかりません"}


def cmd_update(req):
    tid = req.get("id")
    if tid is None:
        return {"ok": False, "error": "idは必須です"}

    ensure_room()
    with open(LOCK_FILE, "w") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        tickets = read_tickets()
        target = None
        for t in tickets:
            if t.get("id") == int(tid):
                target = t
                break
        if not target:
            return {"ok": False, "error": f"チケット #{tid} が見つかりません"}

        changed = []
        if "status" in req and req["status"] != target.get("status"):
            if req["status"] not in VALID_STATUS:
                return {"ok": False, "error": f"不正なstatus: {req['status']}"}
            target["status"] = req["status"]
            changed.append(f"status→{req['status']}")
        if "assignee" in req and req["assignee"] != target.get("assignee"):
            assignee_error = validate_assignee(req["assignee"])
            if assignee_error:
                return {"ok": False, "error": assignee_error}
            target["assignee"] = req["assignee"]
            changed.append(f"assignee→{req['assignee'] or 'なし'}")
        if "title" in req and req["title"] != target.get("title"):
            target["title"] = req["title"]
            changed.append(f"title変更")
        if "description" in req:
            target["description"] = req["description"]
            changed.append("description更新")
        if "comment" in req and req["comment"]:
            if "comments" not in target:
                target["comments"] = []
            target["comments"].append({
                "text": req["comment"],
                "author": req.get("comment_by", "system"),
                "ts": now_iso(),
            })
            changed.append("コメント追加")

        target["updated_at"] = now_iso()
        write_tickets(tickets)

    return {"ok": True, "ticket": target, "changed": changed}


CMDS = {
    "create": cmd_create,
    "list": cmd_list,
    "get": cmd_get,
    "update": cmd_update,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in CMDS:
        print(json.dumps({"ok": False, "error": "unknown command"}))
        return
    raw = sys.stdin.read()
    try:
        req = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        req = {}
    try:
        out = CMDS[sys.argv[1]](req)
    except Exception as e:
        out = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
