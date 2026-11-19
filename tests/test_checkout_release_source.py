import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.checkout_release_source import checkout


def git(repository, *args):
    return subprocess.run(
        ['git', '-C', str(repository), *args], check=True, capture_output=True, text=True,
    ).stdout.strip()


class ReleaseSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.upstream = self.root / 'upstream'
        self.upstream.mkdir()
        self.url = self.upstream.as_uri()
        git(self.upstream, 'init', '--initial-branch=main')
        git(self.upstream, 'config', 'user.name', 'Release source test')
        git(self.upstream, 'config', 'user.email', 'release-test@example.invalid')
        git(self.upstream, 'config', 'commit.gpgsign', 'false')
        git(self.upstream, 'lfs', 'install', '--local', '--skip-repo')
        git(self.upstream, 'lfs', 'track', '*.apk')
        self.old = self.commit(b'older APK payload')
        self.new = self.commit(b'current APK payload')
        git(self.upstream, 'update-ref', 'refs/heads/beta', self.old)

    def commit(self, content):
        (self.upstream / 'payload.apk').write_bytes(content)
        git(self.upstream, 'add', '.')
        git(self.upstream, 'commit', '-m', 'Update payload')
        return git(self.upstream, 'rev-parse', 'HEAD')

    def test_blank_override_fetches_main_and_writes_the_resolved_commit(self):
        # Make the remote's default branch differ from the main branch.
        git(self.upstream, 'checkout', 'beta')
        destination = self.root / 'latest source'
        env_file = self.root / 'github-env'
        script = Path(__file__).resolve().parents[1] / 'scripts/checkout_release_source.py'
        subprocess.run(
            [sys.executable, str(script), self.url, str(destination), 'STABLE_COMMIT', '--commit', ''],
            check=True, capture_output=True, text=True,
            env={**os.environ, 'GITHUB_ENV': str(env_file)},
        )
        self.assertEqual(f'STABLE_COMMIT={self.new}', env_file.read_text(encoding='utf-8').strip())
        self.assertEqual('HEAD', git(destination, 'rev-parse', '--abbrev-ref', 'HEAD'))
        self.assertEqual('1', git(destination, 'rev-list', '--count', 'HEAD'))
        self.assertEqual(b'current APK payload', (destination / 'payload.apk').read_bytes())
        self.assertEqual('', git(destination, 'status', '--porcelain'))

    def test_full_sha_fetches_the_older_revision_with_its_own_lfs_payload(self):
        destination = self.root / 'pinned'
        self.assertEqual(self.old, checkout(self.url, destination, self.old.upper()))
        self.assertEqual(self.old, git(destination, 'rev-parse', 'HEAD'))
        self.assertEqual(b'older APK payload', (destination / 'payload.apk').read_bytes())

    def test_invalid_override_is_rejected_before_checkout(self):
        destination = self.root / 'invalid'
        with self.assertRaisesRegex(ValueError, 'full 40-character commit SHA'):
            checkout(self.url, destination, self.old[:12])
        self.assertFalse(destination.exists())

    def test_moved_main_does_not_change_an_existing_checkout(self):
        destination = self.root / 'first'
        self.assertEqual(self.new, checkout(self.url, destination))
        next_commit = self.commit(b'next APK payload')
        self.assertEqual(next_commit, checkout(self.url, self.root / 'second'))
        with self.assertRaisesRegex(ValueError, 'already exists'):
            checkout(self.url, destination)
        self.assertEqual(self.new, git(destination, 'rev-parse', 'HEAD'))
