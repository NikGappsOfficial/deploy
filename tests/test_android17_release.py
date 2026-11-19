import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
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
        self.assertEqual(
            "${{ inputs.verify_only && 'android17-release-preview' || '" + old['concurrency']['group'] + "' }}",
            self.workflow['concurrency']['group'],
        )
        self.assertEqual(
            "github.repository == 'NikGappsOfficial/deploy' && (github.ref == 'refs/heads/main' || inputs.verify_only)",
            self.workflow['jobs']['release']['if'],
        )
        self.assertIn('corego', self.workflow['on']['workflow_dispatch']['inputs']['packages']['default'].split(','))
        self.assertEqual('main', self.workflow['env']['RELEASE_REF'])
        self.assertNotIn('RELEASE_COMMIT', self.workflow['env'])

    def test_release_engine_uses_latest_branch_and_records_resolved_commit(self):
        checkout = next(s for s in self.steps if s.get('name') == 'Check out latest release engine')
        self.assertEqual('${{ env.RELEASE_REF }}', checkout['with']['ref'])
        record = next(i for i, s in enumerate(self.steps) if s.get('name') == 'Record resolved release engine commit')
        self.assertIn('git -C release rev-parse HEAD', self.steps[record]['run'])
        self.assertIn('RELEASE_COMMIT=', self.steps[record]['run'])
        selection = next(i for i, s in enumerate(self.steps) if s.get('id') == 'selection')
        self.assertLess(record, selection)
        self.assertIn('python -m pip install . ', self.steps[selection - 1]['run'])
        self.assertIn('from nikgapps_release.release import selected_groups', self.steps[selection]['run'])
        for name in ('nikgapps/project', 'builder-pin.json', 'release/.engine'):
            self.assertNotIn(name, json.dumps(self.steps))

    def test_dependent_workflows_resolve_the_same_release_branch(self):
        for name in ('Android17ReleaseChecks', 'SyncGitHubRegistryMirror', 'SyncSourceForgeMetadata',
                     'PublishStableRegistry', 'RollbackStableRegistry'):
            workflow = yaml.load((ROOT / f'.github/workflows/{name}.yml').read_text(), Loader=yaml.BaseLoader)
            steps = [step for job in workflow['jobs'].values() for step in job['steps']]
            resolver = next(s for s in steps if s.get('id') == 'engine')
            code = re.search("python - <<'PY'\n(.*?)\nPY", resolver['run'], re.DOTALL).group(1)
            checkout = next(s for s in steps if s.get('with', {}).get('repository') == 'nikhilmenghani/nikgapps-release')
            self.assertEqual('${{ steps.engine.outputs.ref }}', checkout['with']['ref'])
            with tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / 'output'
                subprocess.run([sys.executable, '-c', code], cwd=ROOT,
                    env={**os.environ, 'GITHUB_OUTPUT': str(output)}, check=True, capture_output=True)
                self.assertEqual('ref=main', output.read_text().strip(), name)

    def test_myenv_bootstrap_precedes_git_and_publication(self):
        self.assertEqual("${{ !inputs.verify_only && secrets.G_TOKEN || '' }}", self.workflow['env']['G_TOKEN'])
        serialized = json.dumps(self.workflow)
        for name in ('SSH_PRIVATE_KEY', 'SF_PWD', 'GITLAB_TOKEN'):
            self.assertNotIn('secrets.' + name, serialized)
        loader = next(i for i, step in enumerate(self.steps) if 'scripts/environment.py' in step.get('run', ''))
        clones = next(i for i, step in enumerate(self.steps) if step.get('name') == 'Resolve Stable and overlay source revisions')
        self.assertLess(loader, clones)
        self.assertEqual('${{ !inputs.verify_only }}', self.steps[loader]['if'])
        self.assertEqual('${{ secrets.GITLAB_SOURCE_READ_TOKEN }}', self.steps[clones]['env']['GITLAB_SOURCE_READ_TOKEN'])
        self.assertIn('export GITLAB_TOKEN="$GITLAB_SOURCE_READ_TOKEN"', self.steps[clones]['run'])
        self.assertEqual('false', next(s['with']['persist-credentials'] for s in self.steps if s.get('name') == 'Check out latest release engine'))
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
        # The shared engine now builds overlays and runs the real-APK gate before its registry stage.
        production = next(s for s in self.steps if s.get('id') == 'production')
        self.assertIn('--verify-core', production['run'])
        self.assertIn('--overlay-source .work/overlays_17_source', production['run'])
        self.assertIn('--overlay-source-commit "$OVERLAY_SOURCE_COMMIT"', production['run'])
        verification = next(s for s in self.steps if s.get('id') == 'verification')
        self.assertEqual('inputs.verify_only', verification['if'])
        self.assertIn('--mode local', verification['run'])
        self.assertIn('--metadata-source preview', verification['run'])
        self.assertIn('--package-source registry', verification['run'])
        self.assertIn('--no-compare', verification['run'])
        self.assertIn('--verify-core', verification['run'])
        self.assertIn('--overlay-source .work/overlays_17_source', verification['run'])
        for name in ('STABLE', 'OVERLAY_SOURCE', 'RELEASE'):
            self.assertIn(f'"${name}_COMMIT"', verification['run'])
        self.assertNotIn('NikGapps.overlay_control', json.dumps(self.steps))

    def test_both_source_commit_overrides_are_optional(self):
        inputs = self.workflow['on']['workflow_dispatch']['inputs']
        self.assertNotIn('overlays_commit', inputs)
        step = next(s for s in self.steps if s.get('name') == 'Resolve Stable and overlay source revisions')
        for key, repository in (('stable_commit', '17_stable'), ('overlay_source_commit', 'overlays_17_source')):
            with self.subTest(source=repository):
                self.assertEqual('false', inputs[key]['required'])
                self.assertNotIn('default', inputs[key])
                env_key = f'REQUESTED_{key.upper()}'
                self.assertEqual('${{ inputs.' + key + ' }}', step['env'][env_key])
                self.assertIn(f'https://gitlab.com/nikgapps/{repository}.git .work/{repository}', step['run'])
                self.assertIn(f'{key.upper()} --commit "$' + env_key + '"', step['run'])
        self.assertNotIn('https://gitlab.com/nikgapps/overlays_17.git', step['run'])

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
        self.assertEqual('${{ always() && !inputs.verify_only }}', artifacts['if'])

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
