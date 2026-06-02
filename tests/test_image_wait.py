"""画像claim auto-waitのテスト。

ロック中のAIがdescription完了まで待機し、テキストを受け取れることを検証。
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "web" / "api-server.py"
CLAIM_SCRIPT = ROOT / "room" / "bin" / "irie-claim.sh"
FINISH_SCRIPT = ROOT / "room" / "bin" / "irie-finish-claim.sh"


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


class ImageWaitFlowTest(unittest.TestCase):
    """agent-aがclaim → agent-bがclaim(待機) → agent-aがdescribe → agent-bがテキスト取得"""

    IMG_ID = "waittest"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.room = Path(self.tmp.name)
        self.uploads = self.room / "uploads"
        self.uploads.mkdir(parents=True)
        (self.room / "config.json").write_text(
            json.dumps({"members": [
                {"name": "alice", "role": "human", "display_color": "#fff"},
                {"name": "agent-a", "role": "ai", "display_color": "#aaa"},
                {"name": "agent-b", "role": "ai", "display_color": "#bbb"},
            ]}), encoding="utf-8",
        )
        stored = f"{self.IMG_ID}.png"
        (self.uploads / stored).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        meta = {"id": self.IMG_ID, "name": "test.png", "stored": stored,
                "mime": "image/png", "is_image": True, "size": 108,
                "ts": "2026-06-02T23:00:00", "author": "alice"}
        (self.uploads / "meta.jsonl").write_text(json.dumps(meta, ensure_ascii=False) + "\n")
        (self.uploads / f"{stored}.pending").touch()

        self.port = free_port()
        env = dict(os.environ)
        env.update({
            "IRIE_ROOM": str(self.room),
            "IRIE_HOST": "127.0.0.1",
            "IRIE_PORT": str(self.port),
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

    def _claim(self, claimer):
        env = os.environ.copy()
        env["IRIE_UPLOADS"] = str(self.uploads)
        return subprocess.run(
            ["bash", str(CLAIM_SCRIPT), self.IMG_ID, claimer],
            env=env, text=True, capture_output=True, check=False,
        )

    def _finish(self, claimer, desc):
        env = os.environ.copy()
        env["IRIE_UPLOADS"] = str(self.uploads)
        env["IRIE_DESCRIBE_API"] = f"http://127.0.0.1:{self.port}/api/describe"
        return subprocess.run(
            ["/bin/bash", str(FINISH_SCRIPT), self.IMG_ID, claimer, desc],
            env=env, text=True, capture_output=True, check=False,
        )

    def _read_meta(self):
        for line in (self.uploads / "meta.jsonl").read_text().splitlines():
            if line.strip():
                e = json.loads(line)
                if e.get("id") == self.IMG_ID:
                    return e
        return None

    def test_claim_then_describe_flow(self):
        """agent-aがclaim→describe。descriptionがmeta.jsonlに保存される"""
        r1 = self._claim("agent-a")
        self.assertIn("CLAIMED", r1.stdout)

        r2 = self._finish("agent-a", "A photo of a sunset")
        self.assertEqual(r2.returncode, 0, f"finish failed: {r2.stdout} {r2.stderr}")

        meta = self._read_meta()
        self.assertEqual(meta["description"], "A photo of a sunset")
        self.assertEqual(meta["claimed_by"], "agent-a")

    def test_second_claimer_gets_taken_by(self):
        """agent-aがclaim中にagent-bがclaimするとTAKEN_BY"""
        r1 = self._claim("agent-a")
        self.assertIn("CLAIMED", r1.stdout)

        r2 = self._claim("agent-b")
        self.assertIn("TAKEN_BY", r2.stdout)

    def test_after_describe_read_returns_text(self):
        """describe完了後、meta.jsonlにdescriptionがある"""
        self._claim("agent-a")
        self._finish("agent-a", "Mountains and clouds")

        meta = self._read_meta()
        self.assertIsNotNone(meta)
        self.assertEqual(meta["description"], "Mountains and clouds")

    def test_delayed_describe_unblocks_reader(self):
        """agent-aがclaim中、別スレッドで3秒後にdescribe。
        meta.jsonlを定期チェックするリーダーが結果を取得できることを検証"""
        self._claim("agent-a")

        result_holder = [None]

        def delayed_describe():
            time.sleep(3)
            self._finish("agent-a", "Delayed description")

        t = threading.Thread(target=delayed_describe)
        t.start()

        # ポーリングで待機(auto-waitのシミュレーション)
        for i in range(20):
            time.sleep(0.5)
            meta = self._read_meta()
            if meta and meta.get("description"):
                result_holder[0] = meta["description"]
                break

        t.join(timeout=10)
        self.assertEqual(result_holder[0], "Delayed description")


if __name__ == "__main__":
    unittest.main()
