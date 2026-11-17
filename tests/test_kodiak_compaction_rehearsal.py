import json
import unittest
from unittest.mock import Mock, patch

from scripts import rehearse_kodiak_compaction as rehearsal


class RehearsalTests(unittest.TestCase):
    def test_project_identity_rejects_other_target(self):
        api = Mock()
        api.request.return_value = {"id": rehearsal.PROJECT_ID, "path_with_namespace": rehearsal.PROJECT,
                                    "visibility": "public", "default_branch": "main", "archived": False}
        with self.assertRaisesRegex(rehearsal.RehearsalError, "private rehearsal target"):
            rehearsal.check_project(api)
        api.request.assert_called_once_with(rehearsal.PROJECT_API)

    def test_failed_push_restores_protection(self):
        enabled = False
        changes = []

        def protection(path, body=None, method=None):
            nonlocal enabled
            self.assertEqual(path, rehearsal.PROTECTED_MAIN)
            if method == "PATCH":
                enabled = body["allow_force_push"]
                changes.append(enabled)
            return {"name": "main", "allow_force_push": enabled}

        api = Mock()
        api.request.side_effect = protection
        with patch.object(rehearsal, "git_bytes", side_effect=RuntimeError("push failed")) as push:
            with self.assertRaisesRegex(RuntimeError, "push failed"):
                rehearsal.replace_main(api, "test-bare-repo", {})
        self.assertEqual(changes, [True, False])
        self.assertIn(f"--force-with-lease={rehearsal.MAIN}:{rehearsal.OLD}", push.call_args.args)
        self.assertIn(f"{rehearsal.ROOT}:{rehearsal.MAIN}", push.call_args.args)
        self.assertIn("ci.skip", push.call_args.args)

    def test_lfs_batch_requires_exact_downloadable_objects(self):
        objects = [{"sha256": "a" * 64, "size": 1}, {"sha256": "b" * 64, "size": 2}]
        rows = [{"oid": item["sha256"], "size": item["size"], "actions": {"download": {}}} for item in objects]
        with patch.object(rehearsal.urllib.request, "urlopen") as open_url:
            response = open_url.return_value.__enter__.return_value
            response.read.return_value = json.dumps({"objects": rows})
            rehearsal.check_lfs("Basic placeholder", rehearsal.MAIN, objects)
            request = open_url.call_args.args[0]
            self.assertEqual(json.loads(request.data)["operation"], "download")
            self.assertEqual(json.loads(request.data)["ref"]["name"], rehearsal.MAIN)
            rows[1]["error"] = {"code": 404}
            response.read.return_value = json.dumps({"objects": rows})
            with self.assertRaisesRegex(rehearsal.RehearsalError, "cannot download every candidate object"):
                rehearsal.check_lfs("Basic placeholder", rehearsal.MAIN, objects)
