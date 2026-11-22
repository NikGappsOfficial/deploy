# Mobilism archive

**Actions → Mobilism archive → Run workflow** runs the uploader from the private
`nikhilmenghani/cron` project. `preflight` (the default) tests the code and checks
Telegram access without publishing. Select `archive` to download and publish
newer watched releases, including variants and dependencies, to Archive.

The watchlist is `configs/mobilism.json` in **cron**, not in deploy. Update that
file and push cron's `main`; deploy fetches its latest `main` on every run.
Install requirements from `requirements-mobilism.txt`, independent of the other
cron jobs. Linux's `aapt` inspects actual APK package names.

The schedule runs hourly at minute 17 UTC when deploy's repository variable
`MOBILISM_ARCHIVE_ENABLED` is `true`. Runs share a concurrency group and never
cancel an upload in progress. Disable that variable to stop scheduled publishing.
GitHub can delay scheduled jobs; the schedule is not a real-time guarantee.

## Secrets and persisted history

Keep these Actions secrets in **NikGappsOfficial/deploy**:

- `MOBILISM_USERNAME`, `MOBILISM_PASSWORD`, `MOBILISM_PREMIUM_PASSWORD`
- `MOBILISM_TELEGRAM_BOT_TOKEN`, `MOBILISM_TELEGRAM_CHAT_ID`
- `MOBILISM_TELEGRAM_API_ID`, `MOBILISM_TELEGRAM_API_HASH`
- `MOBILISM_STATE_KEY`: Base64 encoding of a randomly generated 32-byte key

The workflow reuses deploy's existing `SSH_PRIVATE_KEY` to clone cron. Telegram
uses the same bot as timesheets, with a separate Archive destination and MTProto
session. The timesheet workflow is unchanged.

Delivery history and the Telegram session are encrypted using AES-256-GCM before
storage in deploy's separate **mobilism-state** branch. They are never uploaded
as plaintext artifacts or pip caches. That branch has no workflow or source code.
The encryption key must be backed up securely; changing it alone makes the old
checkpoint unreadable. Existing local delivery history must be seeded before
enabling the schedule, so already archived APKs are not sent again.

An Actions run acquires a persistent lease before using the history. Its final
step saves the new encrypted history even if the archive command fails. Missing
history, a wrong key, a concurrent update or an unfinished lease stops execution
rather than starting empty. If a runner dies before saving, disable the schedule
and review that run and Archive before recovering the checkpoint. Do not clear
the lease or reset history blindly: Telegram may already have accepted files.

Run only one scheduler for this archive. The original Windows task uses a
different local database and must stay disabled once GitHub publishing is enabled.
