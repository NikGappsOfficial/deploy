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
        self.assertNotIn('if', command)
        self.assertEqual(1, sum('main.py --mode production' in step.get('run', '') for step in self.steps))

    def test_inline_python_parses(self):
        for step in self.steps:
            for code in re.findall("python - <<'PY'\n(.*?)\nPY", step.get('run', ''), re.DOTALL):
                compile(code, step['name'], 'exec')

    @unittest.skipUnless(BASH, 'Bash is unavailable')
    def test_shell_steps_parse(self):
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / 'step.sh'
            for step in self.steps:
                if 'run' in step:
                    script.write_text(step['run'], encoding='utf-8', newline='\n')
                    result = subprocess.run([BASH, '-n', str(script)], capture_output=True, text=True)
                    self.assertEqual(0, result.returncode, f"{step['name']}: {result.stderr}")


if __name__ == '__main__':
    unittest.main()
