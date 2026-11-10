import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
GIT_BASH = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git/bin/bash.exe'
BASH = str(GIT_BASH) if os.name == 'nt' and GIT_BASH.is_file() else shutil.which('bash')


class Android17ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.load((ROOT / '.github/workflows/NikGappsAndroid17Release.yml').read_text(), Loader=yaml.BaseLoader)
        self.steps = self.workflow['jobs']['release']['steps']

    def test_release_is_manual_and_shares_registry_lock(self):
        self.assertEqual({'workflow_dispatch'}, set(self.workflow['on']))
        old = yaml.load((ROOT / '.github/workflows/PublishStableRegistry.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(old['concurrency']['group'], self.workflow['concurrency']['group'])
        self.assertIn('corego', self.workflow['on']['workflow_dispatch']['inputs']['packages']['default'].split(','))
        self.assertRegex(self.workflow['env']['RELEASE_COMMIT'], r'^[0-9a-f]{40}$')

    def test_myenv_bootstrap_precedes_git_and_publication(self):
        self.assertEqual('${{ secrets.G_TOKEN }}', self.workflow['env']['G_TOKEN'])
        serialized = json.dumps(self.workflow)
        for name in ('SSH_PRIVATE_KEY', 'SF_PWD', 'GITLAB_TOKEN'):
            self.assertNotIn('secrets.' + name, serialized)
        loader = next(i for i, step in enumerate(self.steps) if 'scripts/environment.py' in step.get('run', ''))
        clones = next(i for i, step in enumerate(self.steps) if step.get('name') == 'Clone tested stable sources and compiled overlays')
        self.assertLess(loader, clones)
        self.assertEqual('false', next(s['with']['persist-credentials'] for s in self.steps if s.get('name') == 'Check out tested release engine'))
        self.assertNotIn('git@', serialized)
        self.assertNotIn('https://$', serialized)

    def test_production_uses_registry_and_pushed_source_commit(self):
        command = next(s for s in self.steps if s.get('id') == 'production')
        self.assertEqual('release', command['working-directory'])
        self.assertIn('--package-source registry', command['run'])
        self.assertIn('--changelog changelogs.yaml', command['run'])
        self.assertIn('--stable-commit "$STABLE_COMMIT"', command['run'])
        self.assertEqual('${{ !inputs.verify_only }}', command['if'])
        self.assertEqual(1, sum('main.py --mode production' in step.get('run', '') for step in self.steps))

    def test_real_apk_gate_precedes_publication_and_full_verification_cannot_publish(self):
        smoke = next(i for i, step in enumerate(self.steps) if 'scripts.verify_core' in step.get('run', ''))
        production = next(i for i, step in enumerate(self.steps) if step.get('id') == 'production')
        self.assertLess(smoke, production)
        self.assertNotIn('if', self.steps[smoke])
        verification = next(s for s in self.steps if s.get('id') == 'verification')
        self.assertEqual('inputs.verify_only', verification['if'])
        self.assertIn('--mode local', verification['run'])
        self.assertIn('--metadata-source preview', verification['run'])
        self.assertIn('--package-source registry', verification['run'])
        self.assertIn('--no-compare', verification['run'])

    def test_compiled_overlay_commit_is_pinned_like_stable_source(self):
        inputs = self.workflow['on']['workflow_dispatch']['inputs']
        self.assertRegex(inputs['overlays_commit']['default'], r'^[0-9a-f]{40}$')
        step = next(s for s in self.steps if s.get('name') == 'Clone tested stable sources and compiled overlays')
        self.assertEqual('${{ inputs.overlays_commit }}', step['env']['TESTED_OVERLAYS_COMMIT'])
        self.assertIn('git -C .work/overlays_17 fetch --depth=1 origin "$TESTED_OVERLAYS_COMMIT"', step['run'])
        self.assertIn('git -C .work/overlays_17 checkout --detach FETCH_HEAD', step['run'])

    def test_inline_python_parses(self):
        sync = yaml.load((ROOT / '.github/workflows/SyncSourceForgeMetadata.yml').read_text(), Loader=yaml.BaseLoader)
        for step in self.steps + sync['jobs']['sync']['steps']:
            for code in re.findall("python - <<'PY'\n(.*?)\nPY", step.get('run', ''), re.DOTALL):
                compile(code, step['name'], 'exec')

    def test_artifacts_keep_receipts_and_metadata_without_duplicate_release_zips(self):
        artifacts = next(step for step in self.steps if step.get('uses', '').startswith('actions/upload-artifact@'))
        paths = artifacts['with']['path'].splitlines()
        self.assertIn('release/dist/**/release-manifest.json', paths)
        self.assertIn('release/.work/production/*/download-metadata/', paths)
        self.assertIn('!release/**/*.zip', paths)
        self.assertNotIn('release/dist/', paths)

    @unittest.skipUnless(BASH, 'Bash is unavailable')
    def test_shell_steps_parse(self):
        sync = yaml.load((ROOT / '.github/workflows/SyncSourceForgeMetadata.yml').read_text(), Loader=yaml.BaseLoader)
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / 'step.sh'
            for step in self.steps + sync['jobs']['sync']['steps']:
                if 'run' in step:
                    script.write_text(step['run'], encoding='utf-8', newline='\n')
                    result = subprocess.run([BASH, '-n', str(script)], capture_output=True, text=True)
                    self.assertEqual(0, result.returncode, f"{step.get('name', 'shell step')}: {result.stderr}")


if __name__ == '__main__':
    unittest.main()
