from pathlib import Path
import unittest

import yaml


class StableSourcePromotionWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workflow = Path(__file__).resolve().parents[1] / ".github/workflows/NikGappsStableSourcePromotion.yml"
        cls.workflow = yaml.load(workflow.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        cls.job = cls.workflow["jobs"]["stable-source"]

    def test_review_is_default_and_runs_only_from_official_deploy(self):
        self.assertEqual(set(self.workflow["on"]), {"workflow_dispatch"})
        inputs = self.workflow["on"]["workflow_dispatch"]["inputs"]
        self.assertEqual(inputs["action"]["default"], "review")
        self.assertEqual(inputs["action"]["options"], ["review", "apply", "test"])
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        self.assertEqual(self.job["if"], "github.repository == 'NikGappsOfficial/deploy' && github.ref == 'refs/heads/main'")
        checkout = self.job["steps"][0]["with"]
        self.assertEqual(checkout["ref"], "main")
        self.assertEqual(checkout["persist-credentials"], "false")

    def test_review_and_fixture_paths_only_write_the_job_summary(self):
        steps = {step.get("name"): step for step in self.job["steps"]}
        self.assertIn("test_*promotion.py", steps["Run stable source promotion tests"]["run"])
        promotion = steps["Review or apply the exact source tree"]
        self.assertEqual(promotion["if"], "inputs.action != 'test'")
        self.assertEqual(promotion["env"]["NIKOPS_ENVIRONMENT"],
                         "${{ inputs.action == 'apply' && 'production' || 'local' }}")
        self.assertIn('--approve "$APPROVAL_ID"', promotion["run"])
        self.assertIn('"$GITHUB_STEP_SUMMARY"', steps["Show the upgrade report"]["run"])
        self.assertTrue(all("upload-artifact" not in step.get("uses", "") for step in steps.values()))
