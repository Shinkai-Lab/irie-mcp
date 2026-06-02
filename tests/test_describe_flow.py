"""describe 通し（irie-finish-claim.sh → /api/describe）の回帰テスト。

既存の test_image_mcp は IRIE_DESCRIBE_API をダミーに向けており、finish-claim の
curl + python(JSON生成) パスを実サーバに対して通していなかった。そのため

  - macOS の bash 3.2 で Python dict リテラル {..} がブレース展開で壊れて describe が常に失敗
  - finish-claim の pending 削除が拡張子無しファイル名で空振りして pending が残る

という不具合を検出できなかった。ここでは実 API サーバを立て、特殊文字（波括弧・
引用符・改行・$・&）を含む description を finish-claim 経由で保存し、
meta.jsonl に正しく入ること & pending が消えることを検証する。
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "web" / "api-server.py"
FINISH_CLAIM = ROOT / "room" / "bin" / "irie-finish-claim.sh"


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


class DescribeFlowTest(unittest.TestCase):
    IMG_ID = "test1234"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.room = Path(self.tmp.name)
        self.uploads = self.room / "uploads"
        self.uploads.mkdir(parents=True)
        (self.room / "config.json").write_text(
            json.dumps({"members": [{"name": "alice", "role": "human", "display_color": "#fff"}]}),
            encoding="utf-8",
        )
        stored = f"{self.IMG_ID}.png"
        (self.uploads / stored).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        meta = {"id": self.IMG_ID, "name": "test.png", "stored": stored,
                "mime": "image/png", "is_image": True, "size": 108,
                "ts": "2026-06-02T09:00:00", "author": "user"}
        (self.uploads / "meta.jsonl").write_text(json.dumps(meta, ensure_ascii=False) + "\n")
        # Web UI は ${file_id}${ext}.pending を作る
        self.pending = self.uploads / f"{stored}.pending"
        self.pending.write_text("mio")  # claim 済み状態

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

    def _finish(self, desc):
        env = os.environ.copy()
        env["IRIE_UPLOADS"] = str(self.uploads)
        env["IRIE_DESCRIBE_API"] = f"http://127.0.0.1:{self.port}/api/describe"
        return subprocess.run(
            ["bash", str(FINISH_CLAIM), self.IMG_ID, "mio", desc],
            env=env, text=True, capture_output=True, check=False,
        )

    def _saved_description(self):
        for line in (self.uploads / "meta.jsonl").read_text().splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            if e.get("id") == self.IMG_ID:
                return e.get("description")
        return None

    def test_describe_roundtrips_special_chars(self):
        # 波括弧は bash 3.2 のブレース展開トラップ、他もシェル/JSON のエスケープ確認用
        desc = 'ナチュラルモダン {a,b} "quote" \'apos\' の改行\n2行目 & 特殊文字 $HOME'
        r = self._finish(desc)
        self.assertEqual(r.returncode, 0, f"finish-claim failed: {r.stdout!r} {r.stderr!r}")
        self.assertIn("DONE", r.stdout)
        self.assertEqual(self._saved_description(), desc)

    def test_pending_is_removed_after_describe(self):
        # finish-claim は ${file_id}${ext}.pending（拡張子つき）を消せること
        self.assertTrue(self.pending.exists())
        r = self._finish("a description")
        self.assertEqual(r.returncode, 0, f"finish-claim failed: {r.stdout!r} {r.stderr!r}")
        self.assertFalse(self.pending.exists(), "pending file should be removed after describe")


if __name__ == "__main__":
    unittest.main()
