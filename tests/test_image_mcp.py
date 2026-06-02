"""MCP画像claim/describe/readフローのテスト"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER_JS = ROOT / "room" / "mcp" / "server.js"


class ImageClaimFlowTest(unittest.TestCase):
    """画像claim→describe→readの一連のフローをshellスクリプトレベルでテスト"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.room = Path(self.tmp.name)
        self.uploads = self.room / "uploads"
        self.uploads.mkdir(parents=True)
        # ダミー画像ファイル
        self.img_id = "test1234"
        (self.uploads / f"{self.img_id}.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        # meta.jsonl
        meta = {"id": self.img_id, "name": "test.png", "stored": f"{self.img_id}.png",
                "mime": "image/png", "is_image": True, "size": 108, "ts": "2026-06-02T09:00:00", "author": "user"}
        (self.uploads / "meta.jsonl").write_text(json.dumps(meta, ensure_ascii=False) + "\n")
        # pendingファイル (Web UIは ${file_id}${ext}.pending で作る)
        (self.uploads / f"{self.img_id}.png.pending").touch()

    def tearDown(self):
        self.tmp.cleanup()

    def _claim(self, file_id, claimer):
        env = os.environ.copy()
        env["IRIE_UPLOADS"] = str(self.uploads)
        return subprocess.run(
            ["bash", str(ROOT / "room" / "bin" / "irie-claim.sh"), file_id, claimer],
            env=env, text=True, capture_output=True, check=False,
        )

    def _finish(self, file_id, claimer, desc):
        env = os.environ.copy()
        env["IRIE_UPLOADS"] = str(self.uploads)
        env["IRIE_DESCRIBE_API"] = "http://127.0.0.1:1/dummy"  # API呼ばない
        return subprocess.run(
            ["bash", str(ROOT / "room" / "bin" / "irie-finish-claim.sh"), file_id, claimer, desc],
            env=env, text=True, capture_output=True, check=False,
        )

    def test_claim_success(self):
        r = self._claim(self.img_id, "agent1")
        self.assertEqual(r.returncode, 0)
        self.assertIn("CLAIMED", r.stdout)

    def test_claim_twice_blocked(self):
        r1 = self._claim(self.img_id, "agent1")
        self.assertIn("CLAIMED", r1.stdout)
        # pendingにclaimer名が書き込まれてる
        pending = self.uploads / f"{self.img_id}.png.pending"
        content = pending.read_text().strip()
        self.assertEqual(content, "agent1")
        # 2人目はTAKEN_BY
        r2 = self._claim(self.img_id, "agent2")
        self.assertIn("TAKEN_BY", r2.stdout)

    def test_no_pending_file(self):
        r = self._claim("nonexistent", "agent1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("NO_PENDING", r.stdout)

    def test_meta_has_image(self):
        meta = json.loads((self.uploads / "meta.jsonl").read_text().strip())
        self.assertTrue(meta["is_image"])
        self.assertEqual(meta["id"], self.img_id)

    def test_meta_no_description_initially(self):
        meta = json.loads((self.uploads / "meta.jsonl").read_text().strip())
        self.assertNotIn("description", meta)


class ImageMetaReadTest(unittest.TestCase):
    """meta.jsonlからの画像情報読み取りテスト"""

    def test_filter_images_only(self):
        tmp = tempfile.TemporaryDirectory()
        uploads = Path(tmp.name) / "uploads"
        uploads.mkdir()
        entries = [
            {"id": "img1", "name": "photo.png", "stored": "img1.png", "mime": "image/png", "is_image": True, "size": 100},
            {"id": "doc1", "name": "readme.md", "stored": "doc1.md", "mime": "text/markdown", "is_image": False, "size": 50},
            {"id": "img2", "name": "shot.jpg", "stored": "img2.jpg", "mime": "image/jpeg", "is_image": True, "size": 200,
             "description": "A screenshot", "claimed_by": "agent1"},
        ]
        with open(uploads / "meta.jsonl", "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        meta = [json.loads(l) for l in (uploads / "meta.jsonl").read_text().strip().split("\n")]
        images = [e for e in meta if e.get("is_image")]
        self.assertEqual(len(images), 2)
        described = [e for e in images if e.get("description")]
        self.assertEqual(len(described), 1)
        self.assertEqual(described[0]["description"], "A screenshot")
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
