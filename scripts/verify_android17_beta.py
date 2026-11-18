"""Prepare a pinned Android 17 beta candidate and rehearse rollback locally."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


REVISIONS = {
    "operations_revision", "stable_revision", "kodiak_revision", "beta_revision",
    "overlay_source_revision", "firmware_revision",
}
PIXEL_APKS = ("PixelLauncherOverlay.apk", "PixelLauncherColorsOverlay.apk")
SHA = re.compile(r"[0-9a-f]{40}")
PROJECT = re.compile(r"(?:[A-Za-z0-9_-][A-Za-z0-9_.-]*/)+[A-Za-z0-9_-][A-Za-z0-9_.-]*")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, document):
    path.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def validate_pins(document):
    keys = REVISIONS | {"overlay_project", "firmware_project"}
    require(isinstance(document, dict) and set(document) == keys, "Expected exact beta source inputs")
    for key in REVISIONS:
        require(isinstance(document[key], str) and SHA.fullmatch(document[key]), f"{key} must be a full Git commit ID")
    for key in ("overlay_project", "firmware_project"):
        require(isinstance(document[key], str) and PROJECT.fullmatch(document[key]), f"{key} must be a GitLab project path")
    require(re.fullmatch(r"17_kodiak_\d{6}", document["firmware_project"].rsplit("/", 1)[-1]),
            "Firmware must point to a dated Kodiak checkout")
    return document


def save_pins(args):
    if args.event_name == "push":
        document = read_json(args.config)
    else:
        document = read_json(args.event)["inputs"]
    pins = validate_pins(document)
    write_json(args.workspace / "pins.json", pins)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"operations_revision={pins['operations_revision']}\n")


def git(repository, *arguments, env=None):
    return subprocess.check_output(
        ["git", "-C", str(repository), *arguments], env=env, stderr=subprocess.STDOUT,
    ).decode("utf-8", errors="replace").strip()


def gitlab_environment(environment, endpoint):
    scope = dict(environment, GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="credential.helper", GIT_CONFIG_VALUE_0="")
    token = environment.get("GITLAB_READ_TOKEN")
    if token:
        authorization = base64.b64encode(f"oauth2:{token}".encode()).decode()
        scope.update({
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_1": f"http.{endpoint}.extraheader",
            "GIT_CONFIG_VALUE_1": f"Authorization: Basic {authorization}",
        })
    return scope


def fetch_repository(root, project, revision, *, env, bare=False, keep_history=False, verify_tip=False):
    root.mkdir()
    init = ["init", "--initial-branch=main"]
    git(root, *init, *(["--bare"] if bare else []), env=env)
    url = f"https://gitlab.com/{project}.git"
    git_env = gitlab_environment(env, url)
    git(root, "remote", "add", "origin", url, env=env)
    require(git(root, "remote", "get-url", "origin", env=env) == url, "Unexpected GitLab origin")
    if verify_tip:
        tip = git(root, "ls-remote", "--exit-code", "origin", "refs/heads/main", env=git_env).split()[0]
        require(tip == revision, f"{project} main moved before verification")
    fetch = ["fetch", "--no-tags"]
    if not keep_history:
        fetch.append("--depth=1")
    git(root, *fetch, "origin", revision, env=git_env)
    require(git(root, "rev-parse", "FETCH_HEAD", env=env) == revision, f"Fetched the wrong commit for {project}")
    if bare:
        git(root, "update-ref", "refs/heads/main", revision, env=env)
    else:
        git(root, "checkout", "--detach", revision, env=env)
    # The Git header would also match requests for signed object URLs.
    # Use the precise LFS batch endpoint and only its auth configuration.
    git(root, "lfs", "fetch", "origin", revision, env=gitlab_environment(env, url + "/info/lfs"))
    if not bare:
        git(root, "lfs", "checkout", env=env)


def fetch_inputs(args):
    pins = validate_pins(read_json(args.workspace / "pins.json"))
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_LFS_SKIP_SMUDGE="1")
    bootstrap = env.pop("G_TOKEN", None)
    legacy = env.pop("LEGACY_GITLAB_TOKEN", None)
    if not env.get("GITLAB_READ_TOKEN") and bootstrap:
        from nikgapps_ops.auth import import_secrets
        from nikgapps_ops.common import PipelineError

        try:
            env["GITLAB_READ_TOKEN"] = import_secrets(["NIKGAPPS_GITLAB_TOKEN"], token=bootstrap)["NIKGAPPS_GITLAB_TOKEN"]
        except PipelineError:
            require(legacy, "Unable to obtain GitLab read access from deploy credentials")
    if not env.get("GITLAB_READ_TOKEN") and legacy:
        env["GITLAB_READ_TOKEN"] = legacy
    for name in ("stable", "kodiak", "beta"):
        fetch_repository(args.workspace / name, f"nikgapps/17_{name}", pins[f"{name}_revision"],
                         env=env, keep_history=name == "beta", verify_tip=True)
        print(f"Verified GitLab {name} revision")
    sync = read_json(args.workspace / "kodiak" / ".nikgapps-oem-sync.json")
    require(sync.get("status") == "complete" and sync.get("sourceProject") == pins["firmware_project"] and
            sync.get("sourceCommit") == pins["firmware_revision"], "Dated firmware differs from the verified Kodiak source receipt")
    fetch_repository(args.workspace / "overlay-source", pins["overlay_project"],
                     pins["overlay_source_revision"], env=env)
    fetch_repository(args.workspace / "firmware.git", pins["firmware_project"],
                     pins["firmware_revision"], env=env, bare=True)
    print("Verified GitLab overlay-source and dated firmware revisions")


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_tool_errors(log, workspace):
    with log.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        stream.seek(max(0, stream.tell() - 8192))
        tail = stream.read().decode("utf-8", errors="replace")
    messages = []
    for line in tail.splitlines():
        if (not re.match(r"^(?:Beta preparation failed|[A-Za-z]+Error|[A-Za-z]+Exception): ", line)
                or re.search(r"authorization|token|password|secret|credential|https?://", line, re.IGNORECASE)):
            continue
        line = line.replace(str(workspace), "<runner-workspace>").replace("%", "%25")
        messages.append("".join(char for char in line if char.isprintable())[:600])
    return messages[-3:]


def run_tool(workspace, phase, command):
    log_path = workspace / (phase + ".log")
    with log_path.open("wb") as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        for message in safe_tool_errors(log_path, workspace):
            print(f"::error::{message}")
    require(result.returncode == 0, f"{phase} failed in the runner-local work directory")


def verify_pixel_overlay_files(compiled, report):
    expected = {row["path"]: row for row in report["files"]}
    paths = {file.relative_to(compiled).as_posix() for file in compiled.rglob("*.apk")}
    require(len(expected) == len(report["files"]) and paths == set(expected),
            "Compiled overlays differ from the compiler inventory")
    for name in paths:
        path = compiled / name
        require(path.stat().st_size == expected[name]["size"] and file_hash(path) == expected[name]["sha256"],
                f"Compiler did not verify {path.name}")
    for filename in PIXEL_APKS:
        require(f"PixelLauncherOverlay/{filename}" in expected, f"Compiler did not produce {filename}")
    return {filename: expected[f"PixelLauncherOverlay/{filename}"]["sha256"] for filename in PIXEL_APKS}


def compile_overlays(workspace, revision, aapt2):
    from nikgapps_release.overlay_toolchain import prepare

    apktool = prepare(workspace / "tools")
    output = workspace / "compiled-overlays"
    run_tool(workspace, "overlay-compile", [
        "nikgapps-release-overlays", "--source", str(workspace / "overlay-source"),
        "--source-commit", revision, "--apktool-jar", str(apktool),
        "--aapt2", str(aapt2), "--work", str(output),
    ])
    report = read_json(output / "report.json")
    require(report["sourceCommit"] == revision and report.get("apktool"), "Compiler provenance differs from the reviewed inputs")
    compiled = output / "compiled"
    pixel = verify_pixel_overlay_files(compiled, report)
    git(compiled, "init", "--initial-branch=compiled")
    git(compiled, "add", "--all")
    git(compiled, "-c", "user.name=NikGapps Verify", "-c", "user.email=verify@nikgapps.com",
        "-c", "commit.gpgSign=false", "commit", "-m", "Record runner-local compiled overlay inputs")
    return compiled, git(compiled, "rev-parse", "HEAD"), report, pixel


def local_rollback(workspace, candidate, revision, base):
    """Verify the candidate in a disposable Git repository and restore its original ref."""
    destination = workspace / "readback.git"
    destination.mkdir()
    git(destination, "init", "--bare", "--initial-branch=main")
    git(destination, "fetch", "--no-tags", str(candidate), revision)
    git(destination, "update-ref", "refs/heads/main", base)
    git(destination, "update-ref", "refs/heads/main", revision, base)
    tree = git(destination, "rev-parse", "refs/heads/main^{tree}")
    require(tree == git(candidate, "rev-parse", revision + "^{tree}"), "Local beta readback tree differs from the candidate")
    git(destination, "update-ref", "refs/heads/main", base, revision)
    require(git(destination, "rev-parse", "refs/heads/main") == base, "Local beta rollback did not restore the review base")
    return tree


def verify_beta(args):
    workspace = args.workspace.resolve()
    pins = validate_pins(read_json(workspace / "pins.json"))
    require(SHA.fullmatch(args.release_revision), "Invalid resolved release commit")
    aapt2 = args.sdk_build_tools / "aapt2"
    apksigner = args.sdk_build_tools / "lib" / "apksigner.jar"
    compiled, compiled_revision, overlay_report, pixel = compile_overlays(workspace, pins["overlay_source_revision"], aapt2)
    definitions = workspace / "release-memberships.json"
    run_tool(workspace, "release-definitions", [
        "nikgapps-release-definitions", "--android", "17", "--output", str(definitions),
    ])
    output = workspace / "candidate"
    command = [
        sys.executable, "-m", "nikgapps_ops.beta_refresh", "--seed", "stable",
        "--stable", str(workspace / "stable"), "--stable-revision", pins["stable_revision"],
        "--oem", str(workspace / "kodiak"), "--oem-revision", pins["kodiak_revision"],
        "--beta", str(workspace / "beta"), "--beta-revision", pins["beta_revision"],
        "--overlays", str(compiled), "--overlays-revision", compiled_revision,
        "--firmware", str(workspace / "firmware.git"), "--firmware-revision", pins["firmware_revision"],
        "--definitions", str(definitions), "--output", str(output),
        "--aapt2", str(aapt2), "--apksigner", str(apksigner), "--apply",
    ]
    run_tool(workspace, "beta-stage", command)
    receipt = read_json(output / "receipt.json")
    report = read_json(output / "refresh-report.json")
    applied = read_json(output / "apply" / "result.json")
    expected = {
        "stable": pins["stable_revision"], "oem": pins["kodiak_revision"], "beta": pins["beta_revision"],
        "overlays": compiled_revision, "firmware": pins["firmware_revision"],
    }
    require(receipt["provenance"]["revisions"] == expected and receipt["provenance"]["seed"] == "stable"
            and receipt["status"] == "prepared",
            "Beta receipt differs from the reviewed source inputs")
    require(receipt["reportSha256"] == file_hash(output / "refresh-report.json"), "Beta report checksum differs from its receipt")
    require(applied["destinationRevision"] == pins["beta_revision"] and applied["status"] == "complete" and
            applied["source"]["candidateInputId"] == receipt["inputId"], "Beta application does not match the prepared candidate")
    for filename, checksum in pixel.items():
        actual = output / "tree" / "PixelSpecifics" / "PixelLauncher" / "___overlay" / filename
        require(actual.is_file() and file_hash(actual) == checksum, f"Beta output differs from the compiled {filename}")
    run_tool(workspace, "beta-replay", command)
    require(read_json(output / "apply" / "result.json") == applied, "Unchanged beta replay created a different candidate")
    candidate = output / "apply" / "candidate.git"
    if applied["changed"]:
        parents = git(candidate, "show", "-s", "--format=%P", applied["revision"])
        require(parents == pins["beta_revision"], "Candidate does not advance the pinned beta commit")
    else:
        require(applied["revision"] == pins["beta_revision"], "Unchanged beta stage cannot change its parent revision")
        candidate = workspace / "beta"
    tree = local_rollback(workspace, candidate, applied["revision"], pins["beta_revision"])
    require(not applied["changed"] or tree == applied["tree"], "Expected Git tree does not match the apply receipt")
    verification = {
        "schemaVersion": 1, "status": "complete", "inputs": pins, "releaseRevision": args.release_revision,
        "apktool": overlay_report["apktool"], "overlaySourceTreeSha256": overlay_report["sourceTreeSha256"],
        "overlayCount": len(overlay_report["files"]), "compiledOverlayRevision": compiled_revision,
        "compiledPixelSha256": pixel, "candidateRevision": applied["revision"], "candidateTree": tree,
        "candidateFileCount": receipt["fileCount"], "preparedTreeSha256": receipt["treeSha256"],
        "reportSha256": receipt["reportSha256"], "againstStable": report["againstStable"],
        "againstBeta": report["againstBeta"], "localRollbackRestored": pins["beta_revision"],
        "downgradesFromBeta": report["downgradesFromBeta"],
    }
    write_json(workspace / "verification-receipt.json", verification)
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
        summary.write("## Stable-seeded beta verification\n\n")
        for key in sorted(REVISIONS - {"operations_revision"}):
            summary.write(f"- {key}: `{pins[key]}`\n")
        summary.write(f"- Freshly compiled overlays: {verification['overlayCount']}\n")
        for filename, checksum in pixel.items():
            summary.write(f"- {filename}: `{checksum}`\n")
        summary.write(f"- Read-back beta candidate: `{applied['revision']}` and Git tree `{tree}`\n")
        summary.write(f"- Payloads: {receipt['fileCount']} and tree SHA-256 `{receipt['treeSha256']}`\n")
        summary.write(f"- Report SHA-256: `{receipt['reportSha256']}`\n")
        for key in ("againstStable", "againstBeta"):
            stats = report[key]
            summary.write(f"- {key}: {len(stats['added'])} added, {len(stats['updated'])} updated, "
                          f"{len(stats['removed'])} removed\n")
        summary.write(f"- APK downgrade decisions relative to current beta: {len(report['downgradesFromBeta'])}\n")
        summary.write(f"- Unchanged replay and local rollback to `{pins['beta_revision']}` passed.\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="command", required=True)
    for name in ("pins", "fetch", "verify"):
        mode = modes.add_parser(name)
        mode.add_argument("--workspace", type=Path, required=True)
        if name == "pins":
            mode.add_argument("--event", type=Path, required=True)
            mode.add_argument("--event-name", choices=("push", "workflow_dispatch"), required=True)
            mode.add_argument("--config", type=Path, required=True)
        if name == "verify":
            mode.add_argument("--release-revision", required=True)
            mode.add_argument("--sdk-build-tools", type=Path, required=True)
    args = parser.parse_args()
    try:
        {"pins": save_pins, "fetch": fetch_inputs, "verify": verify_beta}[args.command](args)
    except subprocess.CalledProcessError as error:
        # Git diagnostics can carry private network details. Emit the tool and status only.
        parser.exit(1, f"::error::Git failed with status {error.returncode} during {args.command}.\n")
    except (KeyError, OSError, ValueError) as error:
        parser.exit(1, f"::error::{error}\n")


if __name__ == "__main__":
    main()
