import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLAIM_SCRIPT = ROOT / "room" / "bin" / "irie-claim.sh"


class ClaimScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.uploads = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def claim(self, file_id, claimer):
        env = os.environ.copy()
        env["IRIE_UPLOADS"] = str(self.uploads)
        return subprocess.run(
            ["bash", str(CLAIM_SCRIPT), file_id, claimer],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_claim_uses_configured_uploads_dir(self):
        pending = self.uploads / "image.png.pending"
        pending.write_text("", encoding="utf-8")

        first = self.claim("image.png", "agent-a")
        second = self.claim("image.png", "agent-b")

        self.assertEqual(first.returncode, 0)
        self.assertEqual(first.stdout.strip(), "CLAIMED")
        self.assertEqual(pending.read_text(encoding="utf-8").strip(), "agent-a")
        self.assertEqual(second.returncode, 0)
        self.assertEqual(second.stdout.strip(), "TAKEN_BY agent-a")

    def test_claim_returns_no_pending_when_file_is_missing(self):
        result = self.claim("missing.png", "agent-a")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.strip(), "NO_PENDING")


if __name__ == "__main__":
    unittest.main()
