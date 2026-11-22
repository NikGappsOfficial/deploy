# Mobilism archive

**Actions → Mobilism archive → Run workflow** runs the uploader from the private
`nikhilmenghani/cron` project. `preflight` (the default) tests the code and checks
Telegram access without publishing. Select `archive` to download and publish
newer watched releases, including variants and dependencies, to Archive.

The watchlist is **cron's `configs/mobilism.json`**. Its credentials are in
**cron's `configs/mobilism.private.json`**. Change those files in the private
project and push `main`; deploy fetches the latest `main` on every run. Neither
credential values nor the Telegram session are copied into public deploy.

The workflow reuses the same `SSH_PRIVATE_KEY` access as the timesheet and currency
jobs. It needs no additional Actions secrets and has only read permission for
deploy. The private cron checkout loads its own Mobilism login, Telegram bot/API
credentials, Archive destination and history-encryption key. Environment variables
remain available as overrides. The timesheet workflow is unchanged.

Linux installs the standalone `requirements-mobilism.txt` and `aapt` to inspect
actual APK package names. APK variants and dependencies from a source post are
grouped into document albums, with per-file captions and shared post hashtags.

The schedule runs hourly at minute 17 UTC when deploy's repository variable
`MOBILISM_ARCHIVE_ENABLED` is `true`. Runs share a concurrency group and never
cancel an upload in progress. Disable that variable to stop scheduled publishing.
GitHub can delay scheduled jobs; the schedule is not a real-time guarantee.

## Persisted history and recovery

Delivery history and the Telegram session are AES-256-GCM encrypted before being
saved to the **private cron repository's `mobilism-state` branch** using its
existing SSH access. Nothing is saved to public deploy, plaintext artifacts or
pip caches. The state branch has no workflows or source code. Its encryption key
stays in the private cron configuration; preserve it when updating credentials.

Seed the existing local history once with `python mobilism_github_state.py seed`
from the cron project before enabling GitHub. This preserves already delivered
releases and refuses to overwrite an existing state branch. Each subsequent run
restores that checkpoint and saves the updated history in its final step, even
if the archive command fails. State pushes use normal fast-forward checks.

Missing history, a wrong key, a concurrent update or an unfinished run lease stops
execution instead of starting empty. If a runner dies before saving, disable
the schedule and review that run and Archive before recovering the checkpoint.
Do not clear the lease or reset history blindly: Telegram may have accepted files.

Run only one scheduler for this archive. The original Windows task uses a
different local database and must stay disabled once GitHub publishing is enabled.
