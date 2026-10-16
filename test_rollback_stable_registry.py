import unittest
from unittest.mock import patch

import rollback_stable_registry as rollback


class RollbackTests(unittest.TestCase):
    def test_config_metadata_is_part_of_publication_not_source_code(self):
        for path in ('catalog.json', 'config-templates.json', 'releases/index.json'):
            self.assertTrue(rollback.generated_metadata_path(path))
        for path in ('README.md', 'rollback_stable_registry.py', '.github/workflows/publish.yml'):
            self.assertFalse(rollback.generated_metadata_path(path))

    def test_config_artifact_is_validated_and_revision_must_match(self):
        documents = {
            'releases/index.json': {'latest': {'17': {'stable': {'arm64-v8a': 'old'}}},
                'releases': [{'id': 'old', 'androidVersion': '17', 'channel': 'stable',
                    'architecture': 'arm64-v8a', 'manifest': 'releases/old.json'}]},
            'releases/old.json': {'configVersion': '41', 'packages': {'chrome': {'version': 'old'}}},
            'catalog.json': {'packages': [{'id': 'chrome', 'versions': {'old': {'artifact': {'url': 'package'}}}}]},
            'builder-assets.json': {'assets': {}},
            'config-templates.json': {'templates': {'17': {'version': '41', 'template': {'url': 'config'}}}},
        }
        with patch.object(rollback, 'document', side_effect=lambda revision, path: documents[path]), \
             patch.object(rollback, 'git', return_value='config-templates.json'), \
             patch.object(rollback, 'verify_url') as verify:
            self.assertEqual((1, 2), rollback.validate_release('parent', 'old', '17', 'stable', 'arm64-v8a', 'token'))
            self.assertIn(unittest.mock.call('config', 'token'), verify.call_args_list)
            documents['config-templates.json']['templates']['17']['version'] = '42'
            with self.assertRaisesRegex(ValueError, 'config revision'):
                rollback.validate_release('parent', 'old', '17', 'stable', 'arm64-v8a', 'token')
