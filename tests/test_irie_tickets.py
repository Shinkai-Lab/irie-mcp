import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "room" / "bin" / "irie-tickets.py"


def load_module(room):
    os.environ["IRIE_ROOM"] = str(room)
    os.environ.pop("KAIGI_ROOM", None)
    spec = importlib.util.spec_from_file_location("irie_tickets_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TicketConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.room = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("IRIE_ROOM", None)
        os.environ.pop("KAIGI_ROOM", None)

    def write_config(self, members):
        (self.room / "config.json").write_text(
            json.dumps({"members": members}, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_create_accepts_assignee_from_config(self):
        self.write_config([
            {"name": "alice", "role": "human", "display_color": "#e94560"},
            {"name": "agent-a", "role": "ai", "display_color": "#4ecca3"},
        ])
        tickets = load_module(self.room)

        result = tickets.cmd_create({
            "title": "Add tests",
            "assignee": "agent-a",
            "created_by": "alice",
        })

        self.assertTrue(result["ok"])
        self.assertEqual(result["ticket"]["assignee"], "agent-a")
        listed = tickets.cmd_list({})
        self.assertEqual(listed["count"], 1)

    def test_create_rejects_unknown_assignee(self):
        self.write_config([
            {"name": "agent-a", "role": "ai", "display_color": "#4ecca3"},
        ])
        tickets = load_module(self.room)

        result = tickets.cmd_create({
            "title": "Bad assignee",
            "assignee": "agent-b",
            "created_by": "alice",
        })

        self.assertFalse(result["ok"])
        self.assertIn("未許可のassignee", result["error"])

    def test_update_validates_status_and_assignee(self):
        self.write_config([
            {"name": "agent-a", "role": "ai", "display_color": "#4ecca3"},
        ])
        tickets = load_module(self.room)
        created = tickets.cmd_create({"title": "Refactor", "created_by": "alice"})
        ticket_id = created["ticket"]["id"]

        bad_status = tickets.cmd_update({"id": ticket_id, "status": "reviewing"})
        bad_assignee = tickets.cmd_update({"id": ticket_id, "assignee": "agent-b"})
        good_assignee = tickets.cmd_update({"id": ticket_id, "assignee": "agent-a"})

        self.assertFalse(bad_status["ok"])
        self.assertIn("不正なstatus", bad_status["error"])
        self.assertFalse(bad_assignee["ok"])
        self.assertIn("未許可のassignee", bad_assignee["error"])
        self.assertTrue(good_assignee["ok"])
        self.assertEqual(good_assignee["ticket"]["assignee"], "agent-a")

    def test_invalid_config_returns_error_for_assignee_validation(self):
        (self.room / "config.json").write_text("{bad json", encoding="utf-8")
        tickets = load_module(self.room)

        result = tickets.cmd_create({
            "title": "Needs config",
            "assignee": "agent-a",
            "created_by": "alice",
        })

        self.assertFalse(result["ok"])
        self.assertIn("config.json", result["error"])


if __name__ == "__main__":
    unittest.main()
