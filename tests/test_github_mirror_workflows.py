from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import yaml

from test_android17_release import BASH

ROOT = Path(__file__).resolve().parents[1]


class MirrorWorkflowTests(unittest.TestCase):
    def workflows(self):
        for name in ('SyncGitHubRegistryMirror', 'PublishStableRegistry', 'SyncSourceForgeMetadata', 'RollbackStableRegistry'):
            yield name, yaml.load((ROOT / f'.github/workflows/{name}.yml').read_text(), Loader=yaml.BaseLoader)

    def test_every_metadata_writer_mirrors_under_same_publication_lock(self):
        for name, document in self.workflows():
            self.assertEqual('stable-registry-publication', document['concurrency']['group'], name)
            steps = next(iter(document['jobs'].values()))['steps']
            loader = next(i for i, step in enumerate(steps) if 'scripts/environment.py' in step.get('run', ''))
            mirrors = [i for i, step in enumerate(steps) if 'scripts.github_mirror' in step.get('run', '')]
            self.assertTrue(mirrors, name)
            self.assertTrue(all(i > loader for i in mirrors), name)
            if name == 'RollbackStableRegistry':
                self.assertEqual('inputs.apply', steps[mirrors[0]]['if'])

    def test_inline_python_and_bash_parse(self):
        for name, document in self.workflows():
            for step in next(iter(document['jobs'].values()))['steps']:
                command = step.get('run', '')
                for code in re.findall("python - <<'PY'\n(.*?)\nPY", command, re.DOTALL):
                    compile(code, name, 'exec')
                if command and BASH:
                    with tempfile.TemporaryDirectory() as temporary:
                        path = Path(temporary) / 'step.sh'
                        path.write_text(command, newline='\n')
                        result = subprocess.run([BASH, '-n', str(path)], capture_output=True, text=True)
                        self.assertEqual(0, result.returncode, name + ': ' + result.stderr)

    def test_backfill_keeps_only_receipt_as_workflow_artifact(self):
        document = dict(self.workflows())['SyncGitHubRegistryMirror']
        steps = document['jobs']['mirror']['steps']
        artifact = next(s for s in steps if s.get('uses', '').startswith('actions/upload-artifact@'))
        self.assertEqual('release/.work/github-mirror/mirror-receipt.json', artifact['with']['path'])


if __name__ == '__main__':
    unittest.main()
