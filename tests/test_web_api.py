import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "web" / "api-server.py"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_until_up(port, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def req(method, port, path, token=None, body=None):
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("X-Irie-Token", token)
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, None


class WebApiAuthTests(unittest.TestCase):
    TOKEN = "test-secret-token"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.room = Path(self.tmp.name)
        (self.room / "config.json").write_text(
            json.dumps({"members": [{"name": "alice", "role": "human", "display_color": "#fff"}]}),
            encoding="utf-8",
        )
        self.port = free_port()
        env = dict(os.environ)
        env.update({
            "IRIE_ROOM": str(self.room),
            "IRIE_HOST": "127.0.0.1",
            "IRIE_PORT": str(self.port),
            "IRIE_API_TOKEN": self.TOKEN,
        })
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVER)], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if not wait_until_up(self.port):
            self.proc.terminate()
            self.fail("server did not start")

    def tearDown(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.tmp.cleanup()

    def test_config_get_requires_token(self):
        code, _ = req("GET", self.port, "/api/config")
        self.assertEqual(code, 401)
        code, data = req("GET", self.port, "/api/config", token=self.TOKEN)
        self.assertEqual(code, 200)
        self.assertEqual(data["members"][0]["name"], "alice")

    def test_config_post_requires_token_and_validates(self):
        code, _ = req("POST", self.port, "/api/config", body={"members": []})
        self.assertEqual(code, 401)

        code, _ = req("POST", self.port, "/api/config", token=self.TOKEN,
                      body={"members": [{"name": "", "role": "human"}]})
        self.assertEqual(code, 400)

        # CSS injection attempt via display_color is rejected
        code, _ = req("POST", self.port, "/api/config", token=self.TOKEN,
                      body={"members": [{"name": "x", "role": "ai", "display_color": "red;background:url(x)"}]})
        self.assertEqual(code, 400)

        code, data = req("POST", self.port, "/api/config", token=self.TOKEN,
                         body={"members": [{"name": "bob", "role": "ai", "display_color": "#4ecca3"}]})
        self.assertEqual(code, 200)
        self.assertTrue(data["ok"])
        saved = json.loads((self.room / "config.json").read_text())
        self.assertEqual(saved["members"][0]["name"], "bob")

    def test_upload_traversal_is_blocked(self):
        code, _ = req("GET", self.port, "/uploads/../../etc/passwd", token=self.TOKEN)
        self.assertEqual(code, 404)


class WebApiPortInUseTest(unittest.TestCase):
    def test_friendly_message_when_port_already_in_use(self):
        room = tempfile.TemporaryDirectory()
        port = free_port()
        env = dict(os.environ)
        env.update({"IRIE_ROOM": room.name, "IRIE_HOST": "127.0.0.1", "IRIE_PORT": str(port)})
        first = subprocess.Popen(
            [sys.executable, str(SERVER)], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            self.assertTrue(wait_until_up(port), "first server did not start")
            # 2つ目は同じポートに bind できず、誘導付きメッセージで綺麗に終わる
            second = subprocess.run(
                [sys.executable, str(SERVER)], env=env,
                capture_output=True, text=True, timeout=10,
            )
            out = second.stdout + second.stderr
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("使用中", out)
            self.assertNotIn("Traceback", out)  # 生スタックトレースを出さない
        finally:
            first.terminate()
            try:
                first.wait(timeout=5)
            except subprocess.TimeoutExpired:
                first.kill()
            room.cleanup()


class WebApiPullWindowTests(unittest.TestCase):
    """#6: /api/pull の初期ロード窓（limit / truncated / earliest_seq）。"""

    def _daemon(self, cmd, payload):
        r = subprocess.run(
            [sys.executable, str(ROOT / "room" / "bin" / "iried.py"), cmd],
            input=json.dumps(payload), capture_output=True, text=True,
            env={"IRIE_ROOM": str(self.room), "PATH": "/usr/bin:/bin"},
        )
        return json.loads(r.stdout)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.room = Path(self.tmp.name)
        (self.room / "config.json").write_text(
            json.dumps({"members": [{"name": "alice", "role": "human"}]}), encoding="utf-8",
        )
        # 会議を開始（seq1=system）し、20件追記 → 全21件
        self._daemon("start", {"topic": "t", "author": "alice"})
        for i in range(20):
            self._daemon("append", {"author": "alice", "text": f"m{i}"})
        self.total = self._daemon("log", {"meeting": (self.room / "ACTIVE").read_text().strip()})["total"]

        self.port = free_port()
        env = dict(os.environ)
        env.update({
            "IRIE_ROOM": str(self.room), "IRIE_HOST": "127.0.0.1",
            "IRIE_PORT": str(self.port), "IRIE_PULL_LIMIT": "5",
        })
        env.pop("IRIE_API_TOKEN", None)
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVER)], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if not wait_until_up(self.port):
            self.proc.terminate()
            self.fail("server did not start")

    def tearDown(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.tmp.cleanup()

    def test_initial_load_applies_default_window(self):
        code, data = req("GET", self.port, "/api/pull?since=0")
        self.assertEqual(code, 200)
        self.assertEqual(len(data["messages"]), 5)       # 既定 limit=5
        self.assertTrue(data["truncated"])
        self.assertEqual(data["earliest_seq"], self.total - 4)
        self.assertEqual(data["messages"][-1]["seq"], self.total)

    def test_explicit_limit_overrides_default(self):
        code, data = req("GET", self.port, "/api/pull?since=0&limit=100")
        self.assertEqual(code, 200)
        self.assertEqual(len(data["messages"]), self.total)
        self.assertFalse(data["truncated"])

    def test_diff_pull_has_no_limit(self):
        # since>0 の差分は制限なし＆truncatedにならない
        code, data = req("GET", self.port, f"/api/pull?since={self.total - 1}")
        self.assertEqual(code, 200)
        self.assertEqual(len(data["messages"]), 1)
        self.assertEqual(data["messages"][0]["seq"], self.total)
        self.assertFalse(data["truncated"])


class WebApiBindGuardTest(unittest.TestCase):
    def test_refuses_non_loopback_without_token(self):
        env = dict(os.environ)
        env.update({"IRIE_HOST": "0.0.0.0", "IRIE_PORT": str(free_port())})
        env.pop("IRIE_API_TOKEN", None)
        proc = subprocess.run(
            [sys.executable, str(SERVER)], env=env,
            capture_output=True, text=True, timeout=10,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("IRIE_API_TOKEN", proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
