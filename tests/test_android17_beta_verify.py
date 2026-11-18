from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from scripts import verify_android17_beta as beta


ROOT = Path(__file__).resolve().parents[1]


class VerifyBetaWorkflowTests(unittest.TestCase):
    def test_verify_runs_only_on_approved_deploy_branch_or_manual_dispatch(self):
        document = yaml.load(
            (ROOT / ".github/workflows/NikGappsAndroid17BetaVerify.yml").read_text(encoding="utf-8"),
            Loader=yaml.BaseLoader,
        )
        self.assertEqual(set(document["on"]), {"push", "workflow_dispatch"})
        self.assertEqual(document["on"]["push"]["branches"], ["migration/android17-beta-source-verify"])
        self.assertEqual(document["permissions"], {"contents": "read"})
        job = document["jobs"]["verify"]
        self.assertEqual(job["if"], "github.repository == 'NikGappsOfficial/deploy'")
        self.assertTrue(all("upload-artifact" not in step.get("uses", "") for step in job["steps"]))
        release = [step["with"] for step in job["steps"] if step.get("with", {}).get("path") == "release"]
        self.assertEqual(len(release), 1)
        self.assertEqual(release[0]["ref"], "main")
        self.assertEqual(release[0]["persist-credentials"], "false")
        self.assertEqual(set(document["on"]["workflow_dispatch"]["inputs"]),
                         beta.REVISIONS | {"firmware_project", "overlay_project"})
        sdk = next(step["run"] for step in job["steps"] if step.get("id") == "sdk")
        self.assertIn("commandlinetools-linux-15859902_latest.zip", sdk)
        self.assertIn("sha256sum --check", sdk)
        self.assertLess(sdk.index("sha256sum --check"), sdk.index("unzip -q"))
        self.assertIn("--install 'platforms;android-37.0' 'build-tools;37.0.0'", sdk)
        self.assertIn("ANDROID_HOME=%s", sdk)
        self.assertIn("ANDROID_SDK_ROOT=%s", sdk)

    def test_exact_revisions_and_safe_project_paths(self):
        pins = {key: "a" * 40 for key in beta.REVISIONS}
        pins.update(overlay_project="fixture/overlays_17_source", firmware_project="nikgapps/17_kodiak_261105")
        self.assertEqual(beta.validate_pins(pins), pins)
        for name, value in (
                ("operations_revision", "main"), ("overlay_project", "https://example.invalid/project"),
                ("overlay_project", "fixture/../project"), ("firmware_project", "nikgapps/17_kodiak")):
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                beta.validate_pins({**pins, name: value})

    def test_overlay_report_binds_both_pixel_apks(self):
        with tempfile.TemporaryDirectory() as directory:
            compiled = Path(directory)
            pixel = compiled / "PixelLauncherOverlay"
            pixel.mkdir()
            files = []
            for filename in beta.PIXEL_APKS:
                path = pixel / filename
                path.write_bytes(filename.encode())
                files.append({"path": path.relative_to(compiled).as_posix(),
                              "sha256": beta.file_hash(path), "size": path.stat().st_size})
            self.assertEqual(set(beta.verify_pixel_overlay_files(compiled, {"files": files})), set(beta.PIXEL_APKS))
            (pixel / beta.PIXEL_APKS[1]).write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, beta.PIXEL_APKS[1]):
                beta.verify_pixel_overlay_files(compiled, {"files": files})

    def test_moved_live_base_cannot_be_hydrated(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
                beta, "git", side_effect=["", "", "https://gitlab.com/nikgapps/17_beta.git", "b" * 40]
        ) as git:
            with self.assertRaisesRegex(ValueError, "main moved"):
                beta.fetch_repository(Path(directory) / "beta", "nikgapps/17_beta", "a" * 40,
                                      env={}, verify_tip=True)
            self.assertFalse(any(call.args[1] == "fetch" for call in git.call_args_list))

    def test_git_lab_authentication_uses_separate_exact_scopes(self):
        token = {"GITLAB_READ_TOKEN": "fixture"}
        remote = "https://gitlab.com/fixture/private.git"
        origin = beta.gitlab_environment(token, remote)
        lfs = beta.gitlab_environment(token, remote + "/info/lfs")
        self.assertEqual(origin["GIT_CONFIG_KEY_1"], "http." + remote + ".extraheader")
        self.assertEqual(lfs["GIT_CONFIG_KEY_1"], "http." + remote + "/info/lfs.extraheader")
        self.assertEqual(lfs["GIT_CONFIG_COUNT"], "2")
        self.assertEqual(lfs["GIT_CONFIG_KEY_0"], "credential.helper")

    def test_runner_reports_actionable_errors_without_auth_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            log = workspace / "candidate.log"
            log.write_text(
                f"Beta preparation failed: Missing payload at {workspace}/firmware\n"
                "ValueError: token is confidential\n"
                "ValueError: https://credential.example.invalid/\n"
            )
            self.assertEqual(beta.safe_tool_errors(log, workspace),
                             ["Beta preparation failed: Missing payload at <runner-workspace>/firmware"])

    def test_disposable_repository_readback_and_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            checkout = folder / "candidate"
            checkout.mkdir()
            beta.git(checkout, "init", "--initial-branch=beta")
            commit = ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.com",
                      "-c", "commit.gpgSign=false", "commit", "-m")
            marker = checkout / "marker"
            marker.write_text("base", encoding="utf-8")
            beta.git(checkout, "add", "marker")
            beta.git(checkout, *commit, "Fixture base")
            base = beta.git(checkout, "rev-parse", "HEAD")
            marker.write_text("new", encoding="utf-8")
            beta.git(checkout, "add", "marker")
            beta.git(checkout, *commit, "Fixture candidate")
            revision = beta.git(checkout, "rev-parse", "HEAD")
            tree = beta.local_rollback(folder, checkout.resolve(), revision, base)
            self.assertEqual(tree, beta.git(checkout, "rev-parse", revision + "^{tree}"))
            self.assertEqual(beta.git(folder / "readback.git", "rev-parse", "HEAD"), base)


if __name__ == "__main__":
    unittest.main()
