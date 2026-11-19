from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from scripts import environment
from scripts.git_askpass import answer
from scripts.check_access import check


class EnvironmentTests(unittest.TestCase):
    def test_mirror_owner_credential_is_mapped_and_masked(self):
        values = environment.release_environment(b'NIKGAPPS_GITLAB_TOKEN=fixture\nSF_PWD=password\nNIKGAPPS_GITHUB_TOKEN=owner-fixture\n')
        self.assertEqual('owner-fixture', values['PACKAGES_GITHUB_TOKEN'])
        with tempfile.TemporaryDirectory() as temporary, redirect_stdout(io.StringIO()) as stdout:
            environment.export(values, Path(temporary) / 'env')
        self.assertIn('::add-mask::owner-fixture', stdout.getvalue())

    def test_maps_existing_myenv_fields_without_importing_flags_or_interpolating(self):
        values = environment.release_environment(b'NIKGAPPS_GITLAB_TOKEN=gitlab-fixture\nSF_PWD="a${HOME}$b"\nUSER_NAME=Nik\nUSER_EMAIL=nik@example.test\nPACKAGE_SOURCE=local\nENVIRONMENT_TYPE=local\nUNRELATED_SECRET=ignore\n')
        self.assertEqual('gitlab-fixture', values['GITLAB_TOKEN'])
        self.assertEqual('a${HOME}$b', values['SF_PWD'])
        self.assertEqual('nik@example.test', values['EMAIL'])
        self.assertEqual({'GITLAB_TOKEN', 'SF_PWD', 'USER_NAME', 'EMAIL', 'SF_USER'}, set(values))

    def test_missing_or_multiline_credentials_fail_before_export(self):
        for document in ({'SF_PWD': 'fixture'}, {'GITLAB_TOKEN': 'fixture', 'SF_PWD': 'bad\nVALUE=injected'}):
            with self.assertRaises(ValueError):
                environment.release_environment(json.dumps(document).encode())

    def test_load_requests_private_raw_data_without_writing_it(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'NIKGAPPS_GITLAB_TOKEN=fixture\nSF_PWD=password\n'
        with patch.object(environment.urllib.request, 'urlopen', return_value=response) as urlopen:
            values = environment.load('bootstrap-fixture')
        self.assertEqual('fixture', values['GITLAB_TOKEN'])
        request = urlopen.call_args.args[0]
        self.assertEqual(environment.URL, request.full_url)
        self.assertEqual('Bearer bootstrap-fixture', request.get_header('Authorization'))

    def test_export_masks_credentials_before_writing_github_env(self):
        values = environment.release_environment(b'NIKGAPPS_GITLAB_TOKEN=fixture%token\nSF_PWD=password\n')
        with tempfile.TemporaryDirectory() as temporary, redirect_stdout(io.StringIO()) as stdout:
            destination = Path(temporary) / 'env'
            environment.export(values, destination)
            self.assertIn('GITLAB_TOKEN=fixture%token\n', destination.read_text())
        self.assertIn('::add-mask::fixture%25token', stdout.getvalue())

    def test_git_credentials_are_restricted_to_the_requested_host(self):
        env = {'G_TOKEN': 'github-fixture', 'GITLAB_TOKEN': 'gitlab-fixture'}
        self.assertEqual('github-fixture', answer("Password for 'https://x-access-token@github.com':", env))
        self.assertEqual('gitlab-fixture', answer("Password for 'https://oauth2@gitlab.com':", env))
        self.assertEqual('oauth2', answer("Username for 'https://gitlab.com':", env))
        for prompt in ("Password for 'https://github.com.attacker.test':", "Password for 'https://other.test':"):
            with self.assertRaises(ValueError):
                answer(prompt, env)

    def test_access_preflight_checks_roles_without_mutations(self):
        calls = []
        def fetch(url, token, **kwargs):
            calls.append(url)
            return {'permissions': {'push': True}} if 'github.com' in url else {
                'permissions': {'project_access': {'access_level': 30}}, 'path_with_namespace': 'fixture'}
        with redirect_stdout(io.StringIO()):
            check({'G_TOKEN': 'github', 'GITLAB_TOKEN': 'gitlab', 'SF_PWD': 'password'}, fetch)
        self.assertEqual(5, len(calls))
        self.assertTrue(all('/repos/' in url or '/projects/' in url for url in calls))
        with self.assertRaisesRegex(ValueError, 'push access'):
            check({'G_TOKEN': 'github', 'GITLAB_TOKEN': 'gitlab', 'SF_PWD': 'password'}, lambda *a, **kw: {})
