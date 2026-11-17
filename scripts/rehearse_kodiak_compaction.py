"""Exercise the Kodiak cutover against one disposable GitLab project."""

import base64
import json
import os
from pathlib import Path
import signal
import tempfile
import urllib.error
import urllib.request

from nikgapps_ops.auth import import_secrets
from nikgapps_ops.kodiak_compaction import current_lfs, parentless, snapshot_receipt
from nikgapps_ops.metadata_gitlab import MetadataGitLab
from nikgapps_ops.source_git import git_bytes


DEPLOY_REF = "refs/heads/migration/kodiak-compaction-rehearsal"
PROJECT_ID = 89082372
PROJECT = "nikgapps/17_kodiak_compaction_test"
ORIGIN = f"https://gitlab.com/{PROJECT}.git"
PROJECT_API = f"/projects/{PROJECT_ID}"
MAIN = "refs/heads/main"
CANDIDATE_REF = "refs/heads/rehearsal-candidate"
PROTECTED_MAIN = f"{PROJECT_API}/protected_branches/main"
OLD = "aa7b89df6c289ad3ded0630885d96dedcca6bd40"
ROOT = "22fb6eeaed14a037599d25ec00e8a665e15116b6"
TREE = "6971122740610038434138ced1fadefb07bdd03a"
SOURCE_RECEIPT = {
    "otaProject": "nikgapps/17_kodiak_261105",
    "otaRevision": "2de2cc51106d8a8f115a2af3090f4967a19e80b0",
    "syncReceiptSha256": "e2bbe8dee4ca9289f41fd88a89993c22081e5ff7653be8d5453647ee6f65c3da",
}


class RehearsalError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise RehearsalError(message)


def git_auth(authorization):
    return {
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "http.https://gitlab.com/.extraheader",
        "GIT_CONFIG_VALUE_1": f"Authorization: {authorization}",
    }


def check_project(api):
    project = api.request(PROJECT_API)
    require(
        (project.get("id"), project.get("path_with_namespace"), project.get("visibility"),
         project.get("default_branch"), project.get("archived")) == (PROJECT_ID, PROJECT, "private", "main", False),
        "GitLab project identity does not match the private rehearsal target.",
    )


def force_allowed(api):
    protection = api.request(PROTECTED_MAIN)
    flag = protection.get("allow_force_push")
    require(protection.get("name") == "main" and isinstance(flag, bool), "Could not confirm main protection.")
    return flag


def check_lfs(authorization, ref, objects):
    expected = {row["sha256"]: row["size"] for row in objects}
    body = {
        "operation": "download",
        "transfers": ["basic"],
        "ref": {"name": ref},
        "objects": [{"oid": oid, "size": size} for oid, size in expected.items()],
    }
    request = urllib.request.Request(
        ORIGIN + "/info/lfs/objects/batch", data=json.dumps(body).encode(),
        headers={"Authorization": authorization, "Accept": "application/vnd.git-lfs+json",
                 "Content-Type": "application/vnd.git-lfs+json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            rows = json.load(response)["objects"]
    except urllib.error.HTTPError as exc:
        raise RehearsalError(f"GitLab LFS batch read returned HTTP {exc.code}.") from None
    actual = {row["oid"]: row["size"] for row in rows if "download" in row.get("actions", {}) and "error" not in row}
    require(len(rows) == len(expected) and actual == expected, "GitLab LFS cannot download every candidate object.")


def replace_main(api, repo, environment):
    require(not force_allowed(api), "Expected force push to be disabled before the rehearsal.")
    try:
        api.request(PROTECTED_MAIN, {"allow_force_push": True}, method="PATCH")
        require(force_allowed(api), "Could not confirm the temporary force-push permission.")
        git_bytes(
            repo, "-c", "core.hooksPath=/dev/null", "push", "-o", "ci.skip",
            f"--force-with-lease={MAIN}:{OLD}", "origin", f"{ROOT}:{MAIN}", environment=environment,
        )
    finally:
        try:
            # Restore even when the server accepted the first PATCH but its response was lost.
            api.request(PROTECTED_MAIN, {"allow_force_push": False}, method="PATCH")
            require(not force_allowed(api), "GitLab still reports force push enabled.")
        except Exception:
            raise RehearsalError("Could not verify that force push is disabled on the private rehearsal project.") from None


def run(api, authorization, repo):
    check_project(api)
    require(not force_allowed(api), "Expected force push disabled on the private rehearsal project.")
    environment = git_auth(authorization)
    git_bytes(repo, "init", "--bare")
    git_bytes(repo, "remote", "add", "origin", ORIGIN)
    git_bytes(repo, "fetch", "--no-tags", "origin", f"{MAIN}:{MAIN}",
              f"{CANDIDATE_REF}:{CANDIDATE_REF}", environment=environment)
    main = git_bytes(repo, "rev-parse", MAIN).decode().strip()
    require(main in {OLD, ROOT}, "Rehearsal main is neither the expected baseline nor the verified root.")
    require(git_bytes(repo, "rev-parse", CANDIDATE_REF).decode().strip() == ROOT, "Staged rehearsal candidate changed.")
    require(parentless(repo, ROOT) and git_bytes(repo, "rev-list", "--count", ROOT).strip() == b"1",
            "The candidate must be one root commit.")
    require(git_bytes(repo, "rev-parse", f"{ROOT}^{{tree}}").decode().strip() == TREE, "Candidate tree changed.")
    require(snapshot_receipt(repo, ROOT) == SOURCE_RECEIPT, "Source sync receipt changed.")
    objects = current_lfs(repo, ROOT)
    require(len(objects) == 57 and sum(row["size"] for row in objects) == 2901670362, "Candidate LFS inventory changed.")
    check_lfs(authorization, CANDIDATE_REF, objects)

    if main == OLD:
        replace_main(api, repo, environment)

    heads = git_bytes(repo, "ls-remote", "--heads", "origin", MAIN, environment=environment).decode().split()
    require(heads == [ROOT, MAIN], "Git transport read-back of main disagrees with the expected commit.")
    commits = api.request(f"{PROJECT_API}/repository/commits?ref_name=main&per_page=2")
    require(len(commits) == 1 and commits[0]["id"] == ROOT and not commits[0]["parent_ids"],
            "GitLab's main branch does not contain just the expected root commit.")
    check_lfs(authorization, MAIN, objects)
    require(not force_allowed(api), "Could not confirm force push remains disabled.")

    mode = "Applied the leased cutover" if main == OLD else "Verified the already applied cutover"
    summary = (f"{mode} in the private rehearsal project.\n\n"
               f"- Starting main `{main}`\n- Final main `{ROOT}` with one commit\n- Tree `{TREE}`\n"
               f"- Verified OTA revision `{SOURCE_RECEIPT['otaRevision']}`\n"
               f"- All {len(objects)} Git LFS objects available through the download batch API\n"
               "- Force push disabled on protected main\n")
    print(summary, flush=True)
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
        stream.write(summary)


def cancel(_signum, _frame):
    raise SystemExit("Rehearsal interrupted.")


def main():
    require(os.environ.get("GITHUB_REPOSITORY") == "NikGappsOfficial/deploy"
            and os.environ.get("GITHUB_REF") == DEPLOY_REF and os.environ.get("GITHUB_EVENT_NAME") == "push",
            "Run this rehearsal from its dedicated deploy workflow branch.")
    signal.signal(signal.SIGTERM, cancel)
    token = import_secrets(["NIKGAPPS_GITLAB_TOKEN"])["NIKGAPPS_GITLAB_TOKEN"]
    authorization = "Basic " + base64.b64encode(f"oauth2:{token}".encode()).decode()
    with tempfile.TemporaryDirectory(prefix="kodiak-compaction-") as directory:
        run(MetadataGitLab(token), authorization, Path(directory))


if __name__ == "__main__":
    try:
        main()
    except RehearsalError as exc:
        raise SystemExit(str(exc)) from None
    except Exception as exc:
        # Transport errors can contain credential-bearing headers or signed URLs.
        raise SystemExit(f"Rehearsal failed ({type(exc).__name__}). Inspect the private project's main and protection.") from None
