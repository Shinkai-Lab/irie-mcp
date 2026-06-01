import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROOM_CLI = ROOT / "room" / "bin" / "room"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_port(port, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


class RoomClientSelfContainedTests(unittest.TestCase):
    """`room` は ~/.irie/config.json が無くても同一マシン(local)既定で動く。"""

    def setUp(self):
        self.home = tempfile.TemporaryDirectory()   # 空ホーム = ~/.irie 無し
        self.room = tempfile.TemporaryDirectory()
        (Path(self.room.name) / "config.json").write_text(
            json.dumps({"members": [{"name": "alice", "role": "human"}]}),
            encoding="utf-8",
        )

    def tearDown(self):
        self.home.cleanup()
        self.room.cleanup()

    def run_room(self, *args):
        env = dict(os.environ)
        env["HOME"] = self.home.name           # ~/.irie / ~/.kaigi を不在にする
        env["IRIE_ROOM"] = self.room.name
        env.pop("KAIGI_ROOM", None)
        return subprocess.run(
            [sys.executable, str(ROOM_CLI), *args],
            env=env, text=True, capture_output=True, timeout=15,
        )

    def test_start_and_post_without_config_file(self):
        started = self.run_room("start", "kickoff")
        self.assertEqual(started.returncode, 0, started.stderr)
        self.assertIn("会議を開始", started.stdout)

        posted = self.run_room("post", "--as", "alice", "hello config-less")
        self.assertEqual(posted.returncode, 0, posted.stderr)
        self.assertIn("投稿しました", posted.stdout)

        jsonl = list(Path(self.room.name).glob("*.jsonl"))
        self.assertEqual(len(jsonl), 1)
        texts = [json.loads(l)["text"] for l in jsonl[0].read_text().splitlines() if l.strip()]
        self.assertIn("hello config-less", texts)


class RoomServeTests(unittest.TestCase):
    """`room serve` が web/api-server.py を起動し、二重起動は「既に起動中」と案内する。"""

    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.room = tempfile.TemporaryDirectory()
        (Path(self.room.name) / "config.json").write_text(
            json.dumps({"members": [{"name": "alice", "role": "human"}]}), encoding="utf-8")
        self.port = _free_port()
        self.env = dict(os.environ)
        self.env["HOME"] = self.home.name
        self.env["IRIE_ROOM"] = self.room.name
        self.env["IRIE_PORT"] = str(self.port)
        self.env.pop("KAIGI_ROOM", None)
        self.env.pop("IRIE_API_TOKEN", None)
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOM_CLI), "serve", "--no-open"],
            env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def tearDown(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        if self.proc.stdout:
            self.proc.stdout.close()
        self.home.cleanup()
        self.room.cleanup()

    def test_serve_launches_web_and_reports_when_already_running(self):
        self.assertTrue(_wait_port(self.port), "room serve did not bring the web server up")
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=3) as r:
            self.assertTrue(r.headers.get("Server", "").startswith("irie"))
        # 2回目の serve は新規起動せず「既に起動中」と報告して正常終了
        second = subprocess.run(
            [sys.executable, str(ROOM_CLI), "serve", "--no-open"],
            env=self.env, text=True, capture_output=True, timeout=10)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("既に起動中", second.stdout)


if __name__ == "__main__":
    unittest.main()
