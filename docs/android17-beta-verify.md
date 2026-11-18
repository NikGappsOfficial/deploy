# Verify an Android 17 beta seeded from stable

Run **Verify Android 17 stable-seeded beta** in the official Deploy
repository. The workflow reads the exact stable, Kodiak, current beta,
overlay source and dated firmware revisions. It checks out the supplied
operations commit, resolves release `main` once and reports both tool
commits. The overlay project can point to an isolated GitLab checkout with
the reviewed Pixel Launcher sources.

Manual dispatch takes the fields below. An isolated branch rehearsal uses the
same fields from `.github/validation/android17-beta-source-verify.json` on
`migration/android17-beta-source-verify`.

| Field | Input |
| --- | --- |
| `operations_revision` | Exact commit supporting beta `--seed stable` and the modern firmware inventory |
| `stable_revision` | Reviewed `nikgapps/17_stable` main |
| `kodiak_revision` | Reviewed compacted `nikgapps/17_kodiak` main |
| `beta_revision` | Current `nikgapps/17_beta` main used as candidate parent |
| `overlay_project`, `overlay_source_revision` | Project path and commit containing both Pixel Launcher source targets |
| `firmware_project`, `firmware_revision` | Dated source project and commit in Kodiak’s sync receipt |

Use the checkout-only deploy secret `MIGRATION_CHECKOUT_TOKEN` or existing
`G_TOKEN` with access to both tool repositories. The GitLab input step prefers
`GITLAB_SOURCE_READ_TOKEN` or `NIKGAPPS_GITLAB_READ_TOKEN`. It can use the
existing Deploy credential export and then the legacy `GITLAB_TOKEN` when
needed. Git credentials and payloads remain in the runner. The workflow
publishes its verification summary and retains the logs only on the runner.

The builder installs the [official command-line tools](https://developer.android.com/studio/#command-tools)
by pinned archive SHA-256. It uses the Android SDK `android-37.0` platform,
Build Tools 37.0.0 and JDK 17, verifies the pinned apktool, compiles the overlays and compares
the Pixel Launcher APK hashes with the beta payload. It runs beta preparation
twice to verify reuse, reads the candidate back through disposable Git refs
and rehearses restoring the original beta parent. Verify-only does not advance
beta, stable, the registry or GitLab storage.

## Gate a subsequent beta publication

Pause the legacy `PromoteBetaToStable.yml` schedule before publishing a reviewed
beta candidate and wait for every queued, waiting, pending or in-progress
promotion run to finish. A completed run can be confirmed via the Deploy
Actions API at
`GET /repos/NikGappsOfficial/deploy/actions/workflows/PromoteBetaToStable.yml/runs`.
Check the legacy beta writer and GitLab storage reset lanes at the same time.
Re-read the exact live beta, stable and Kodiak refs immediately before a push.
Require the candidate to be a direct child of the reviewed beta head.

For durable automation, place publishing beta and reading that beta for stable
promotion in the same Deploy source-writer concurrency group. Keep the lock on
the source jobs so downstream catalog publishing retains its separate registry
lock. Older runs that started before the shared group was deployed must still
finish before the first write.

## Local workflow checks

```sh
python -m unittest discover -s tests -p test_android17_beta_verify.py -v
actionlint .github/workflows/NikGappsAndroid17BetaVerify.yml
```
