#!/usr/bin/env python3
"""irie Web UI API server — iried.py / irie-tickets.py の薄い HTTP ラッパー

ローカルの iried.py / irie-tickets.py を subprocess で呼び出し、ブラウザ向けに
HTTP API として公開する。本文はすべて argv + stdin JSON で渡すので shell injection 安全。

環境変数:
  IRIE_ROOM        — 会議室の SSoT データディレクトリ (既定: <repo>/room)
  IRIE_DAEMON      — iried.py のパス (既定: <repo>/room/bin/iried.py)
  IRIE_TICKETS     — irie-tickets.py のパス (既定: <repo>/room/bin/irie-tickets.py)
  IRIE_PYTHON      — Python 実行ファイル (既定: python3)
  IRIE_HOST        — バインドするアドレス (既定: 127.0.0.1)
                     ループバック以外にバインドするには IRIE_API_TOKEN が必須
  IRIE_PORT        — ポート (既定: 8901)
  IRIE_API_TOKEN   — 設定すると、書き込み系/設定系エンドポイントに
                     X-Irie-Token ヘッダ (または Authorization: Bearer) を要求する
  IRIE_WEB_AUTHOR  — Web UI からの既定発言者名 (既定: config の最初の human、無ければ "web")
  IRIE_CORS_ORIGIN — 指定したオリジンにのみ CORS を許可 (既定: 同一オリジンのみ)
"""
import errno
import hmac
import json
import re
import subprocess
import sys
import os
import uuid
import mimetypes
import datetime
import socketserver
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "room" / "bin"
STATIC_DIR = Path(__file__).resolve().parent

DAEMON = os.environ.get("IRIE_DAEMON") or str(BIN_DIR / "iried.py")
TICKETS = os.environ.get("IRIE_TICKETS") or str(BIN_DIR / "irie-tickets.py")
ROOM = os.environ.get("IRIE_ROOM") or os.environ.get("KAIGI_ROOM") or str(REPO_ROOT / "room")
PYTHON = os.environ.get("IRIE_PYTHON") or "python3"
UPLOADS = Path(ROOM) / "uploads"
META_FILE = UPLOADS / "meta.jsonl"

HOST = os.environ.get("IRIE_HOST", "127.0.0.1")
PORT = int(os.environ.get("IRIE_PORT", "8901"))
API_TOKEN = os.environ.get("IRIE_API_TOKEN", "")
CORS_ORIGIN = os.environ.get("IRIE_CORS_ORIGIN", "")
WEB_AUTHOR_ENV = os.environ.get("IRIE_WEB_AUTHOR", "")
MAX_UPLOAD = 10 * 1024 * 1024  # 10MB
MAX_JSON_BODY = 1 * 1024 * 1024  # 1MB
# インライン表示を許す画像タイプ。それ以外は attachment で返す（アップロードHTML/SVG経由のXSS防止）
INLINE_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


def _subprocess_env():
    return {"IRIE_ROOM": ROOM, "KAIGI_ROOM": ROOM, "PATH": "/usr/bin:/bin"}


def call_tickets(cmd, payload=None):
    stdin_data = json.dumps(payload) if payload else "{}"
    result = subprocess.run(
        [PYTHON, TICKETS, cmd],
        input=stdin_data, capture_output=True, text=True, env=_subprocess_env(), timeout=10,
    )
    if result.returncode != 0:
        return {"error": result.stderr.strip() or f"exit code {result.returncode}"}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": "invalid JSON", "raw": result.stdout[:500]}


def call_daemon(cmd, payload=None):
    stdin_data = json.dumps(payload) if payload else ""
    result = subprocess.run(
        [PYTHON, DAEMON, cmd],
        input=stdin_data, capture_output=True, text=True, env=_subprocess_env(), timeout=10,
    )
    if result.returncode != 0:
        return {"error": result.stderr.strip() or f"exit code {result.returncode}"}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": "invalid JSON from daemon", "raw": result.stdout[:500]}


def load_config():
    config_path = Path(ROOM) / "config.json"
    if config_path.exists():
        try:
            return json.loads(config_path.read_text())
        except json.JSONDecodeError:
            return {"members": []}
    return {"members": []}


def default_author():
    if WEB_AUTHOR_ENV:
        return WEB_AUTHOR_ENV
    for m in load_config().get("members", []):
        if isinstance(m, dict) and m.get("role") == "human" and m.get("name"):
            return m["name"]
    return "web"


def validate_config(body):
    """設定POSTの最小スキーマ検証。問題があればエラーメッセージ、無ければ None。"""
    if not isinstance(body, dict):
        return "config must be an object"
    members = body.get("members")
    if not isinstance(members, list):
        return "config.members must be an array"
    for i, m in enumerate(members):
        if not isinstance(m, dict):
            return f"members[{i}] must be an object"
        name = m.get("name")
        if not isinstance(name, str) or not name.strip():
            return f"members[{i}].name must be a non-empty string"
        if m.get("role") not in ("human", "ai"):
            return f"members[{i}].role must be 'human' or 'ai'"
        color = m.get("display_color")
        if color is not None and not (isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{3,8}", color)):
            return f"members[{i}].display_color must be a hex color (e.g. #4ecca3)"
    return None


def save_upload_meta(entry):
    UPLOADS.mkdir(parents=True, exist_ok=True)
    with open(META_FILE, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_upload_meta():
    if not META_FILE.exists():
        return []
    entries = []
    for line in META_FILE.read_text().splitlines():
        if line.strip():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def is_loopback(host):
    return host in ("127.0.0.1", "::1", "localhost") or host.startswith("127.")


class IrieHandler(BaseHTTPRequestHandler):
    server_version = "irie"

    def log_message(self, fmt, *args):
        pass

    # --- helpers ---
    def _cors(self):
        if CORS_ORIGIN:
            self.send_header("Access-Control-Allow-Origin", CORS_ORIGIN)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Irie-Token")

    def _authorized(self):
        """IRIE_API_TOKEN 未設定なら常に許可。設定時は X-Irie-Token / Bearer を検証。"""
        if not API_TOKEN:
            return True
        tok = self.headers.get("X-Irie-Token", "")
        if not tok:
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                tok = auth[len("Bearer "):]
        return bool(tok) and hmac.compare_digest(tok, API_TOKEN)

    def _json_response(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("X-Content-Type-Options", "nosniff")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _unauthorized(self):
        self._json_response({"error": "unauthorized"}, 401)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            return None
        if length <= 0 or length > MAX_JSON_BODY:
            return None
        try:
            return json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
            return None

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/pull":
            qs = urlparse(self.path).query
            since = 0
            for kv in qs.split("&"):
                if kv.startswith("since="):
                    try:
                        since = int(kv[len("since="):])
                    except ValueError:
                        since = 0
            active_file = Path(ROOM) / "ACTIVE"
            if not active_file.exists():
                self._json_response({"meeting": None, "messages": []})
                return
            meeting = active_file.read_text().strip()
            result = call_daemon("log", {"meeting": meeting})
            if "error" in result or not result.get("ok"):
                self._json_response(result, 500)
                return
            msgs = result.get("messages", [])
            filtered = [m for m in msgs if m.get("seq", 0) > since]
            self._json_response({"meeting": meeting, "messages": filtered})
            return

        if path == "/api/status":
            active_file = Path(ROOM) / "ACTIVE"
            if not active_file.exists():
                self._json_response({"active": False})
                return
            meeting = active_file.read_text().strip()
            result = call_daemon("status", {"meeting": meeting})
            self._json_response(result)
            return

        if path == "/api/tickets":
            self._json_response(call_tickets("list", {}))
            return

        if path.startswith("/api/tickets/") and path not in ("/api/tickets/create", "/api/tickets/update"):
            ticket_id = path.split("/")[-1]
            try:
                tid = int(ticket_id)
                self._json_response(call_tickets("get", {"id": tid}))
            except ValueError:
                self._json_response({"error": "invalid ticket id"}, 400)
            return

        if path == "/api/config":
            # 設定の読み取りも、トークン設定時は要認可（メンバー名の漏えい防止）
            if not self._authorized():
                self._unauthorized()
                return
            self._json_response(load_config())
            return

        if path == "/api/files":
            self._json_response({"files": load_upload_meta()})
            return

        # serve uploaded files
        if path.startswith("/uploads/"):
            rel = path[len("/uploads/"):]
            file_path = (UPLOADS / rel).resolve()
            if file_path.is_file() and UPLOADS.resolve() in file_path.parents:
                mime, _ = mimetypes.guess_type(str(file_path))
                mime = mime or "application/octet-stream"
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("X-Content-Type-Options", "nosniff")
                # 画像以外（HTML/SVG/JS等）はインライン実行させず必ずダウンロード扱いにする
                if mime not in INLINE_IMAGE_TYPES:
                    self.send_header("Content-Disposition", "attachment")
                self._cors()
                self.end_headers()
                self.wfile.write(file_path.read_bytes())
                return
            self.send_response(404)
            self.end_headers()
            return

        if path.startswith("/i18n/") and path.endswith(".json"):
            lang_file = STATIC_DIR / "i18n" / os.path.basename(path)
            if lang_file.exists():
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._cors()
                self.end_headers()
                self.wfile.write(lang_file.read_bytes())
                return
            self.send_response(404)
            self.end_headers()
            return

        if path.startswith("/irie-icon") and path.endswith(".png"):
            icon = (STATIC_DIR / os.path.basename(path)).resolve()
            if icon.is_file() and STATIC_DIR.resolve() in icon.parents:
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(icon.read_bytes())
                return
            self.send_response(404)
            self.end_headers()
            return

        if path == "" or path == "/index.html":
            html = (STATIC_DIR / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html)
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        # すべての書き込み系エンドポイントは認可必須（トークン未設定時は全許可）
        if not self._authorized():
            self._unauthorized()
            return

        if path == "/api/config":
            body = self._read_json()
            if body is None:
                self._json_response({"error": "invalid or too-large JSON body"}, 400)
                return
            err = validate_config(body)
            if err:
                self._json_response({"error": err}, 400)
                return
            config_path = Path(ROOM) / "config.json"
            tmp = config_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n")
            tmp.rename(config_path)
            self._json_response({"ok": True})
            return

        if path == "/api/tickets/create":
            body = self._read_json()
            if body is None:
                self._json_response({"error": "invalid JSON body"}, 400)
                return
            self._json_response(call_tickets("create", body))
            return

        if path == "/api/tickets/update":
            body = self._read_json()
            if body is None:
                self._json_response({"error": "invalid JSON body"}, 400)
                return
            self._json_response(call_tickets("update", body))
            return

        if path == "/api/post":
            body = self._read_json()
            if body is None:
                self._json_response({"error": "invalid JSON body"}, 400)
                return
            author = body.get("author") or default_author()
            text = body.get("text", "")
            if not text:
                self._json_response({"error": "empty text"}, 400)
                return
            active_file = Path(ROOM) / "ACTIVE"
            if not active_file.exists():
                self._json_response({"error": "no active meeting"}, 400)
                return
            meeting = active_file.read_text().strip()
            result = call_daemon("append", {"meeting": meeting, "author": author, "text": text})
            self._json_response(result)
            return

        if path == "/api/upload":
            self._handle_upload()
            return

        if path == "/api/describe":
            body = self._read_json()
            if body is None:
                self._json_response({"error": "invalid JSON body"}, 400)
                return
            file_id = body.get("id", "")
            description = body.get("description", "")
            claimed_by = body.get("claimed_by", "")
            if not file_id or not description:
                self._json_response({"error": "id and description required"}, 400)
                return
            entries = load_upload_meta()
            updated = False
            for e in entries:
                if e.get("id") == file_id:
                    e["description"] = description
                    e["claimed_by"] = claimed_by
                    updated = True
                    break
            if not updated:
                self._json_response({"error": "file not found"}, 404)
                return
            UPLOADS.mkdir(parents=True, exist_ok=True)
            with open(META_FILE, "w") as f:
                for e in entries:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
            stored = next((e["stored"] for e in entries if e["id"] == file_id), "")
            pending = UPLOADS / f"{stored}.pending"
            if pending.exists():
                pending.unlink()
            self._json_response({"ok": True})
            return

        self.send_response(404)
        self.end_headers()

    def _handle_upload(self):
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self._json_response({"error": "multipart/form-data required"}, 400)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            self._json_response({"error": "bad content length"}, 400)
            return
        if length <= 0 or length > MAX_UPLOAD:
            self._json_response({"error": "file too large (max 10MB)"}, 413)
            return
        raw = self.rfile.read(length)
        boundary = content_type.split("boundary=")[1].strip() if "boundary=" in content_type else ""
        if not boundary:
            self._json_response({"error": "no boundary"}, 400)
            return
        parts = raw.split(b"--" + boundary.encode())
        file_data = None
        original_name = ""
        author = ""
        for part in parts:
            if b"Content-Disposition" not in part:
                continue
            header_end = part.find(b"\r\n\r\n")
            if header_end < 0:
                continue
            header = part[:header_end].decode(errors="replace")
            body = part[header_end + 4:]
            if body.endswith(b"\r\n"):
                body = body[:-2]
            if 'name="file"' in header and "filename=" in header:
                fn_start = header.index('filename="') + 10
                fn_end = header.index('"', fn_start)
                original_name = header[fn_start:fn_end]
                file_data = body
            elif 'name="author"' in header:
                author = body.decode(errors="replace").strip()
        if file_data is None or not original_name:
            self._json_response({"error": "no file in upload"}, 400)
            return
        author = author or default_author()
        original_name = os.path.basename(original_name)
        ext = Path(original_name).suffix
        file_id = uuid.uuid4().hex[:8]
        stored_name = f"{file_id}{ext}"
        UPLOADS.mkdir(parents=True, exist_ok=True)
        file_path = UPLOADS / stored_name
        file_path.write_bytes(file_data)
        mime, _ = mimetypes.guess_type(original_name)
        is_image = bool(mime and mime.startswith("image/"))
        entry = {
            "id": file_id,
            "name": original_name,
            "stored": stored_name,
            "mime": mime or "application/octet-stream",
            "is_image": is_image,
            "size": len(file_data),
            "ts": datetime.datetime.now().isoformat(),
            "author": author,
        }
        save_upload_meta(entry)
        if is_image:
            (UPLOADS / f"{stored_name}.pending").touch()
        self._json_response({"ok": True, "file": entry})


class IrieHTTPServer(HTTPServer):
    """HTTPServer that skips the reverse-DNS lookup done in server_bind().

    http.server.HTTPServer.server_bind() calls socket.getfqdn(host), which can
    block for ~30s on hosts with slow or misconfigured reverse DNS (observed on
    CI runners). Worse, it runs between bind() and listen(), so the socket does
    not accept connections until it returns — making startup appear to hang.
    Set server_name from the address directly instead.
    """

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


if __name__ == "__main__":
    if not is_loopback(HOST) and not API_TOKEN:
        sys.exit(
            f"拒否: ループバック以外 ({HOST}) にバインドするには IRIE_API_TOKEN を設定してください。\n"
            f"      ローカル専用で起動するなら IRIE_HOST=127.0.0.1 のままにしてください。"
        )
    try:
        server = IrieHTTPServer((HOST, PORT), IrieHandler)
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            sys.exit(
                f"ポート {PORT} は既に使用中のため、Web サーバーは起動しませんでした。\n"
                f"  - irie Web が既に起動済みなら、そのまま http://{HOST}:{PORT}/ を開いてください。\n"
                f"  - 別プロセスが使用中なら、停止するか別ポートで起動してください: "
                f"IRIE_PORT=8911 python3 web/api-server.py"
            )
        sys.exit(f"Web サーバーを起動できませんでした ({HOST}:{PORT}): {e}")
    auth_state = "token required" if API_TOKEN else "no auth (loopback only)"
    print(f"irie Web UI API server on http://{HOST}:{PORT}  [{auth_state}]")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
