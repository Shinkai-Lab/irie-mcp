#!/usr/bin/env python3
"""iried.py — irie 会議室システムのサーバ側ヘルパ（SSoT管理）

すべての状態は IRIE_ROOM 配下に置く。クライアント(room)から
`python3 iried.py <cmd>` の形で呼ばれ、JSONリクエストをstdinで受け、
JSON応答をstdoutに返す。本文は一切シェル展開させない（injection安全）。

設計根拠: 会議室設計レビューの堅牢化仕様 v1.0
- 追記は .lock のflock保持中にseq採番＋append（行の混在を防ぐ）
- 順序は必ずVPS単一採番のseqで決定（ホスト時刻ズレと無関係）
- カーソルは read(peek)→処理→ack の二相。ackするまで前進しないので
  クラッシュしても取りこぼしゼロ（冪等）
- 壊れたJSONL行はスキップして継続
"""
import sys
import os
import json
import fcntl
import re
import datetime

ROOM = os.environ.get("IRIE_ROOM", os.environ.get("KAIGI_ROOM", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
AI_CHAIN_LIMIT = int(os.environ.get("IRIE_AI_CHAIN_LIMIT", os.environ.get("KAIGI_AI_CHAIN_LIMIT", "30")))


def _load_members():
    """config.jsonから参加者リストを読む。なければデフォルト空。"""
    cfg_path = os.path.join(ROOM, "config.json")
    try:
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
        members = cfg.get("members", [])
        humans = {m["name"] for m in members if m.get("role") == "human"}
        ais = {m["name"] for m in members if m.get("role") == "ai"}
        allowed = humans | ais | {"system"}
        names = [m["name"] for m in members]
        mention_pattern = "|".join(re.escape(n) for n in names) + "|all" if names else "all"
        return allowed, humans, ais, re.compile(rf"@({mention_pattern})")
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return {"system"}, set(), set(), re.compile(r"@(all)")


def _members():
    """毎回config.jsonを読み直す（動的追加に対応）。"""
    return _load_members()



def now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def ensure_dirs():
    os.makedirs(ROOM, exist_ok=True)
    os.makedirs(os.path.join(ROOM, "cursor"), exist_ok=True)
    os.makedirs(os.path.join(ROOM, "meta"), exist_ok=True)


def meeting_path(mid):
    return os.path.join(ROOM, f"{mid}.jsonl")


def meta_path(mid):
    return os.path.join(ROOM, "meta", f"{mid}.json")


def cursor_path(mid, who):
    safe = re.sub(r"[^\w぀-ヿ一-鿿-]", "_", who or "")
    return os.path.join(ROOM, "cursor", f"{mid}__{safe}")


def active_path():
    return os.path.join(ROOM, "ACTIVE")


def get_active():
    try:
        with open(active_path()) as f:
            return f.read().strip() or None
    except FileNotFoundError:
        return None


def valid_mid(mid):
    return bool(mid) and re.fullmatch(r"[0-9A-Za-z_-]+", mid) is not None


def read_messages(mid):
    msgs = []
    p = meeting_path(mid)
    if not os.path.exists(p):
        return msgs
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                msgs.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # 壊れた行はスキップ
    return msgs


def _append(mid, author, text):
    """グローバル .lock 保持中に seq 採番して1行追記。返り値=採番したseq。"""
    lockp = os.path.join(ROOM, ".lock")
    with open(lockp, "w") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        msgs = read_messages(mid)
        seq = (msgs[-1]["seq"] + 1) if msgs else 1
        rec = {"ts": now_iso(), "seq": seq, "author": author, "text": text}
        with open(meeting_path(mid), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return seq


def get_cursor(mid, who):
    try:
        with open(cursor_path(mid, who)) as f:
            return int(f.read().strip() or 0)
    except (FileNotFoundError, ValueError):
        return 0  # 破損や未作成は0扱い＝全再読（冪等なので安全）


# ---- commands ----

def cmd_start(req):
    topic = (req.get("topic") or "").strip() or "(無題)"
    author = req.get("author", "system")
    allowed, _, _, _ = _members()
    if author not in allowed:
        author = "system"
    mid = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    open(meeting_path(mid), "a").close()
    with open(meta_path(mid), "w", encoding="utf-8") as f:
        json.dump(
            {"topic": topic, "started_by": author, "started_at": now_iso()},
            f, ensure_ascii=False,
        )
    with open(active_path(), "w") as f:
        f.write(mid)
    _append(mid, "system", f"会議開始: {topic}")
    return {"ok": True, "meeting": mid, "topic": topic}


def cmd_end(req):
    mid = get_active()
    if not mid:
        return {"ok": False, "error": "アクティブな会議がありません"}
    _append(mid, "system", "会議終了")
    open(active_path(), "w").close()
    return {"ok": True, "meeting": mid}


def _ai_chain_count(mid):
    """直近の人間発言より後ろにあるAI発言の数を返す。
    人間発言が無ければ会議開始からの全AI発言数。systemはノーカウント。"""
    _, humans, ais, _ = _members()
    msgs = read_messages(mid)
    count = 0
    for m in reversed(msgs):
        a = m.get("author")
        if a in humans:
            break
        if a in ais:
            count += 1
    return count


def cmd_append(req):
    author = req.get("author")
    allowed, _, ais, mention_re = _members()
    if author not in allowed:
        return {"ok": False, "error": f"未許可の発言者: {author!r}（{os.path.join(ROOM, 'config.json')} の members に追加してください）"}
    text = req.get("text", "")
    if not isinstance(text, str):
        return {"ok": False, "error": "text は文字列である必要があります"}
    mid = req.get("meeting") or get_active()
    if not mid:
        return {"ok": False, "error": "アクティブな会議がありません"}
    if not valid_mid(mid):
        return {"ok": False, "error": "不正なmeeting id"}

    cut = False
    if author in ais:
        if _ai_chain_count(mid) >= AI_CHAIN_LIMIT:
            # #9: 全角＠を半角に正規化してから除去（メンションカット時に全角メンションも確実に剥がす）
            stripped = mention_re.sub(lambda m: m.group(1), text.replace("＠", "@"))
            if stripped != text:
                text = stripped
            text = "<メンションカット> " + text
            cut = True

    seq = _append(mid, author, text)
    return {"ok": True, "meeting": mid, "seq": seq, "mention_cut": cut}


def cmd_read(req):
    who = req.get("who")
    mid = req.get("meeting") or get_active()
    if not mid:
        return {"ok": False, "error": "アクティブな会議がありません"}
    if not valid_mid(mid):
        return {"ok": False, "error": "不正なmeeting id"}
    cur = get_cursor(mid, who)
    msgs = [m for m in read_messages(mid) if m.get("seq", 0) > cur]
    # 自分の発言は新着としてカウントしない（自己ループ即return防止）
    fresh = [m for m in msgs if m.get("author") != who]
    return {"ok": True, "meeting": mid, "cursor": cur,
            "messages": msgs, "fresh": fresh}


def cmd_ack(req):
    who = req.get("who")
    mid = req.get("meeting") or get_active()
    if not mid:
        return {"ok": False, "error": "アクティブな会議がありません"}
    if not valid_mid(mid):
        return {"ok": False, "error": "不正なmeeting id"}
    try:
        seq = int(req.get("seq", 0))
    except (TypeError, ValueError):
        return {"ok": False, "error": "seqは整数である必要があります"}
    cp = cursor_path(mid, who)
    with open(cp + ".lock", "w") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        newcur = max(get_cursor(mid, who), seq)
        with open(cp, "w") as f:
            f.write(str(newcur))
    return {"ok": True, "cursor": newcur}


def cmd_log(req):
    mid = req.get("meeting") or get_active()
    if not mid:
        return {"ok": False, "error": "会議が指定されていません"}
    if not valid_mid(mid):
        return {"ok": False, "error": "不正なmeeting id"}
    return {"ok": True, "meeting": mid, "messages": read_messages(mid)}


def cmd_status(req):
    mid = get_active()
    if not mid:
        return {"ok": True, "active": None}
    topic = ""
    try:
        with open(meta_path(mid)) as f:
            topic = json.load(f).get("topic", "")
    except Exception:
        pass
    msgs = read_messages(mid)
    return {
        "ok": True, "active": mid, "topic": topic,
        "count": len(msgs), "last_seq": (msgs[-1]["seq"] if msgs else 0),
    }


CMDS = {
    "start": cmd_start, "end": cmd_end, "append": cmd_append,
    "read": cmd_read, "ack": cmd_ack, "log": cmd_log, "status": cmd_status,
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
    ensure_dirs()
    try:
        out = CMDS[sys.argv[1]](req)
    except Exception as e:
        out = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
