import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "room" / "bin" / "iried.py"


def load_iried(room, chain_limit=None):
    os.environ["IRIE_ROOM"] = str(room)
    os.environ.pop("KAIGI_ROOM", None)
    if chain_limit is None:
        os.environ.pop("IRIE_AI_CHAIN_LIMIT", None)
    else:
        os.environ["IRIE_AI_CHAIN_LIMIT"] = str(chain_limit)
    os.environ.pop("KAIGI_AI_CHAIN_LIMIT", None)
    spec = importlib.util.spec_from_file_location("iried_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class IriedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.room = Path(self.tmp.name)
        (self.room / "config.json").write_text(
            json.dumps({"members": [
                {"name": "alice", "role": "human"},
                {"name": "agent-a", "role": "ai"},
                {"name": "agent-b", "role": "ai"},
            ]}, ensure_ascii=False),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()
        for k in ("IRIE_ROOM", "IRIE_AI_CHAIN_LIMIT"):
            os.environ.pop(k, None)

    def test_start_append_read_ack_status(self):
        m = load_iried(self.room)
        m.ensure_dirs()
        start = m.cmd_start({"topic": "hello", "author": "alice"})
        self.assertTrue(start["ok"])
        mid = start["meeting"]

        ap = m.cmd_append({"author": "alice", "text": "hi @agent-a"})
        self.assertTrue(ap["ok"])

        rd = m.cmd_read({"who": "agent-a"})
        self.assertTrue(rd["ok"])
        # fresh はagent-a以外の発言（system開始 + alice）を含む
        self.assertIn("alice", [x["author"] for x in rd["fresh"]])

        last = rd["messages"][-1]["seq"]
        ackr = m.cmd_ack({"who": "agent-a", "seq": last})
        self.assertEqual(ackr["cursor"], last)

        # ack後は新着なし（冪等カーソル）
        self.assertEqual(m.cmd_read({"who": "agent-a"})["fresh"], [])

        st = m.cmd_status({})
        self.assertEqual(st["active"], mid)
        self.assertGreaterEqual(st["count"], 2)

    def test_append_rejects_unknown_author(self):
        m = load_iried(self.room)
        m.ensure_dirs()
        m.cmd_start({"topic": "t", "author": "alice"})
        r = m.cmd_append({"author": "mallory", "text": "x"})
        self.assertFalse(r["ok"])
        self.assertIn("未許可", r["error"])

    def test_mention_cut_after_chain_limit(self):
        m = load_iried(self.room, chain_limit=2)
        m.ensure_dirs()
        start = m.cmd_start({"topic": "t", "author": "alice"})
        mid = start["meeting"]
        # 人間発言を挟まずAIが連続 → 制限到達でメンションカット
        m.cmd_append({"author": "agent-a", "text": "1"})
        m.cmd_append({"author": "agent-b", "text": "2"})
        cut = m.cmd_append({"author": "agent-a", "text": "@alice ping"})
        self.assertTrue(cut["ok"])
        self.assertTrue(cut["mention_cut"])
        last_text = m.read_messages(mid)[-1]["text"]
        self.assertIn("<メンションカット>", last_text)
        self.assertNotIn("@alice", last_text)

    def test_broken_jsonl_lines_are_skipped(self):
        m = load_iried(self.room)
        m.ensure_dirs()
        mid = m.cmd_start({"topic": "t", "author": "alice"})["meeting"]
        with open(m.meeting_path(mid), "a", encoding="utf-8") as f:
            f.write("this is not json\n")
        m.cmd_append({"author": "alice", "text": "after broken"})
        msgs = m.read_messages(mid)
        self.assertIn("after broken", [x.get("text") for x in msgs])
        self.assertTrue(all("seq" in x for x in msgs))

    def test_append_requires_active_meeting(self):
        m = load_iried(self.room)
        m.ensure_dirs()
        r = m.cmd_append({"author": "alice", "text": "no meeting"})
        self.assertFalse(r["ok"])

    # ---- #6: 初期ロード制限（cmd_log limit） ----
    def test_log_without_limit_returns_all(self):
        m = load_iried(self.room)
        m.ensure_dirs()
        mid = m.cmd_start({"topic": "t", "author": "alice"})["meeting"]
        for i in range(10):
            m.cmd_append({"author": "alice", "text": f"m{i}"})
        r = m.cmd_log({"meeting": mid})
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["messages"]), r["total"])
        self.assertFalse(r["truncated"])

    def test_log_limit_returns_tail_window(self):
        m = load_iried(self.room)
        m.ensure_dirs()
        mid = m.cmd_start({"topic": "t", "author": "alice"})["meeting"]  # seq 1 = system
        for i in range(20):
            m.cmd_append({"author": "alice", "text": f"m{i}"})
        total = m.cmd_log({"meeting": mid})["total"]
        r = m.cmd_log({"meeting": mid, "limit": 5})
        self.assertTrue(r["truncated"])
        self.assertEqual(r["total"], total)
        self.assertEqual(len(r["messages"]), 5)
        # 末尾N件＝seq が連続した最後の5件
        seqs = [x["seq"] for x in r["messages"]]
        self.assertEqual(seqs, [total - 4, total - 3, total - 2, total - 1, total])

    def test_log_limit_larger_than_total_not_truncated(self):
        m = load_iried(self.room)
        m.ensure_dirs()
        mid = m.cmd_start({"topic": "t", "author": "alice"})["meeting"]
        m.cmd_append({"author": "alice", "text": "only one"})
        r = m.cmd_log({"meeting": mid, "limit": 100})
        self.assertFalse(r["truncated"])
        self.assertEqual(len(r["messages"]), r["total"])


if __name__ == "__main__":
    unittest.main()
