import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import rollback_stable_registry as rollback


class RollbackValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old_cwd = Path.cwd()
        self.addCleanup(lambda: __import__("os").chdir(self.old_cwd))
        __import__("os").chdir(self.root)
        subprocess.run(["git", "init", "-q"], check=True)
        subprocess.run(["git", "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)
        (self.root / "releases" / "android-17" / "arm64-v8a" / "stable").mkdir(parents=True)
        self.write("releases/index.json", {
            "latest": {"17": {"stable": {"arm64-v8a": "old"}}},
            "releases": [{"id": "old", "androidVersion": "17", "channel": "stable",
                          "architecture": "arm64-v8a",
                          "manifest": "releases/android-17/arm64-v8a/stable/old.json"}],
        })
        self.write("releases/android-17/arm64-v8a/stable/old.json", {
            "packages": {"test": {"version": "one"}},
        })
        self.write("catalog.json", {"packages": [{"id": "test", "versions": {
            "one": {"artifact": {"url": "https://gitlab.com/api/v4/projects/1/packages/generic/test/one/test.zip"}}
        }}]})
        self.write("builder-assets.json", {"assets": {"busybox": {
            "url": "https://gitlab.com/api/v4/projects/1/packages/generic/assets/one/busybox"
        }}})
        subprocess.run(["git", "add", "."], check=True)
        subprocess.run(["git", "commit", "-qm", "old"], check=True)
        index = json.loads((self.root / "releases/index.json").read_text())
        index["latest"]["17"]["stable"]["arm64-v8a"] = "new"
        self.write("releases/index.json", index)
        subprocess.run(["git", "add", "."], check=True)
        subprocess.run(["git", "commit", "-qm", "new"], check=True)

    def write(self, path, value):
        (self.root / path).write_text(json.dumps(value))

    @patch.object(rollback, "verify_url")
    def test_previous_release_checks_all_urls(self, verify):
        parent = rollback.git("rev-parse", "HEAD^")
        self.assertEqual((1, 2), rollback.validate_release(
            parent, "old", "17", "stable", "arm64-v8a", "token"))
        self.assertEqual(2, verify.call_count)

    @patch.object(rollback, "verify_url")
    def test_wrong_release_is_rejected(self, verify):
        parent = rollback.git("rev-parse", "HEAD^")
        with self.assertRaisesRegex(ValueError, "does not point"):
            rollback.validate_release(parent, "missing", "17", "stable", "arm64-v8a", "token")
        verify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
