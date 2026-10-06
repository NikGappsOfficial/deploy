"""Safely revert the latest catalog publication after validating its predecessor."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def document(revision: str, path: str) -> dict:
    return json.loads(git("show", f"{revision}:{path}"))


def pointer(index: dict, android: str, channel: str, arch: str) -> str | None:
    return index.get("latest", {}).get(android, {}).get(channel, {}).get(arch)


def verify_url(url: str, token: str) -> None:
    if not url.startswith("https://gitlab.com/api/v4/projects/"):
        raise ValueError(f"Unexpected artifact host: {url}")
    request = urllib.request.Request(url, headers={"PRIVATE-TOKEN": token}, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 200:
                raise ValueError(f"Artifact unavailable ({response.status}): {url}")
    except urllib.error.HTTPError as exc:
        raise ValueError(f"Artifact unavailable ({exc.code}): {url}") from exc


def validate_release(parent: str, release_id: str, android: str, channel: str,
                     arch: str, token: str) -> tuple[int, int]:
    index = document(parent, "releases/index.json")
    if pointer(index, android, channel, arch) != release_id:
        raise ValueError("The parent commit does not point to the requested prior release")
    matches = [item for item in index["releases"] if item.get("id") == release_id
               and item.get("androidVersion") == android
               and item.get("channel") == channel
               and item.get("architecture") == arch]
    if len(matches) != 1:
        raise ValueError("Prior release is absent or ambiguous in the parent index")
    manifest = document(parent, matches[0]["manifest"])
    catalog = document(parent, "catalog.json")
    assets = document(parent, "builder-assets.json")["assets"]
    packages = {item["id"]: item for item in catalog["packages"]}
    urls = set()
    for package_id, lock in manifest["packages"].items():
        version = packages.get(package_id, {}).get("versions", {}).get(lock["version"])
        if not version:
            raise ValueError(f"Missing catalog version: {package_id}:{lock['version']}")
        artifact = version.get("artifact")
        if not artifact or not artifact.get("url"):
            raise ValueError(f"Missing artifact URL: {package_id}:{lock['version']}")
        urls.add(artifact["url"])
    if not urls:
        raise ValueError("Prior release has no packages")
    for asset in assets.values():
        urls.add(asset["url"])
    for url in sorted(urls):
        verify_url(url, token)
    return len(manifest["packages"]), len(urls)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--android-version", required=True)
    parser.add_argument("--channel", default="stable")
    parser.add_argument("--arch", default="arm64-v8a")
    parser.add_argument("--prior-release-id", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    token = os.environ.get("GITLAB_TOKEN")
    if not token:
        parser.error("GITLAB_TOKEN is required")
    os.chdir(args.repo)
    if git("status", "--porcelain"):
        raise ValueError("Catalog checkout must be clean")
    git("fetch", "origin", "main")
    if git("rev-parse", "HEAD") != git("rev-parse", "origin/main"):
        raise ValueError("Catalog checkout is no longer at origin/main")
    head = git("rev-parse", "HEAD")
    parent = git("rev-parse", "HEAD^")
    current = document(head, "releases/index.json")
    previous = document(parent, "releases/index.json")
    selected = pointer(current, args.android_version, args.channel, args.arch)
    if not selected or selected == args.prior_release_id:
        raise ValueError("No newer active release to roll back")
    if pointer(previous, args.android_version, args.channel, args.arch) != args.prior_release_id:
        raise ValueError("Only the immediately preceding catalog publication can be rolled back")
    changed = set(git("diff", "--name-only", parent, head).splitlines())
    if not changed or any(not (path in {"catalog.json", "appsets.json", "builder-assets.json"}
                               or path.startswith("releases/")) for path in changed):
        raise ValueError("Latest commit includes changes outside generated catalog metadata")
    count, checked = validate_release(parent, args.prior_release_id,
                                      args.android_version, args.channel, args.arch, token)
    print(f"Current: {selected}; rollback target: {args.prior_release_id}")
    print(f"Validated {count} packages and {checked} package/asset URLs.")
    if not args.apply:
        print("Dry run only. Re-run with --apply to publish the rollback.")
        return 0
    git("fetch", "origin", "main")
    if git("rev-parse", "origin/main") != head:
        raise ValueError("Catalog changed during validation; refusing to push")
    subprocess.run(["git", "revert", "--no-edit", head], check=True)
    subprocess.run(["git", "push", "origin", "HEAD:main"], check=True)
    print(f"Restored catalog metadata for {args.prior_release_id}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
