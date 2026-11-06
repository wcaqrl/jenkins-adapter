#!/usr/bin/env python3
"""Build and verify the exact selected Jenkins base, then push the tested image.

Executed by lazycat-action's credential-filtered prepare command. The store PAT
is never a Docker build argument, layer, or environment variable in Jenkins.
--verify-only is CI-only: build/test without pushing or submitting anything.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from contract import ROOT, recipe_hash
from upstream import DIGEST, VERSION, manifest, platform_digest


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def selected(env):
    version = env.get("LAZYCAT_SOURCE_VERSION", "")
    ref = env.get("LAZYCAT_SOURCE_REF", "")
    digest = env.get("LAZYCAT_SOURCE_REVISION", "")
    if not VERSION.fullmatch(version) or ref != f"docker.io/jenkins/jenkins:{version}-jdk21" or not DIGEST.fullmatch(digest):
        raise ValueError("Expected the executor's exact official Jenkins LTS reference and platform digest")
    if env.get("LAZYCAT_TARGET_PLATFORM") != "linux/amd64":
        raise ValueError("This custom toolchain currently supports linux/amd64 only")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", env.get("LAZYCAT_APPLICATION_VERSION", "")):
        raise ValueError("Missing allocated application version")
    if not DIGEST.fullmatch(env.get("LAZYCAT_BUILD_FINGERPRINT", "")):
        raise ValueError("Missing source/recipe fingerprint")
    return version, ref, digest


def download(url, path, expected):
    if not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ValueError("Missing pinned toolchain SHA-256")
    # Stream large archives; fetch() is deliberately limited to small metadata.
    import urllib.request
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        partial = path.with_suffix(path.suffix + ".part")
        with urllib.request.urlopen(url, timeout=120) as source, partial.open("wb") as target:
            shutil.copyfileobj(source, target)
        if hashlib.sha256(partial.read_bytes()).hexdigest() != expected:
            partial.unlink()
            raise ValueError("Toolchain checksum mismatch: " + url)
        partial.replace(path)
    print("Verified pinned toolchain:", path.name, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    os.chdir(ROOT)
    version, ref, digest = selected(os.environ)
    output = os.environ.get("LAZYCAT_OUTPUT_IMAGE", "")
    if not re.fullmatch(r"ttl\.sh/lazycat-jenkins-[a-f0-9]{12}:24h", output):
        raise ValueError("Unexpected output image; use the configured immutable fingerprint tag")
    if platform_digest(ref + "@" + digest) != digest:
        raise ValueError("Selected official image digest is inconsistent")
    proof_path = ROOT / ".lazycat-action/upgrade-proof.json"
    proof_path.parent.mkdir(exist_ok=True)
    proof_path.unlink(missing_ok=True)
    recipe = recipe_hash()
    lock = json.loads((ROOT / "image/versions.lock.json").read_text())
    base = ref + "@" + digest
    lock.update(jenkins=version, base_image=base)
    candidate = "lazycat-jenkins:candidate-" + os.environ["LAZYCAT_BUILD_FINGERPRINT"][7:19]
    with tempfile.TemporaryDirectory(prefix="jenkins-build-") as directory:
        context = Path(directory) / "image"
        shutil.copytree(ROOT / "image", context, ignore=shutil.ignore_patterns("downloads", "__pycache__"))
        (context / "versions.lock.json").write_text(json.dumps(lock, indent=2) + "\n")
        downloads = context / "downloads"
        downloads.mkdir()
        download(f"https://go.dev/dl/{lock['go']}.linux-amd64.tar.gz", downloads / "go.tar.gz", lock["go_sha256"])
        download(f"https://nodejs.org/dist/{lock['node']}/node-{lock['node']}-linux-x64.tar.xz", downloads / "node.tar.xz", lock["node_sha256"])
        run("docker", "build", "--platform", "linux/amd64", "--tag", candidate,
            "--build-arg", "JENKINS_BASE_IMAGE=" + base,
            "--build-arg", "PYTHON_BASE_IMAGE=" + lock["python_image"],
            "--build-arg", "GO_SHA256=" + lock["go_sha256"],
            "--build-arg", "NODE_SHA256=" + lock["node_sha256"],
            "--build-arg", "NPM_VERSION=" + lock["npm"],
            "--build-arg", "BUILD_FINGERPRINT=" + os.environ["LAZYCAT_BUILD_FINGERPRINT"], str(context))
    info = json.loads(run("docker", "image", "inspect", candidate, capture_output=True).stdout)[0]
    if info["Architecture"] != "amd64" or info["Os"] != "linux":
        raise ValueError("Built image has the wrong platform")
    if info["Config"]["Labels"]["org.opencontainers.image.base.name"] != base or f"JENKINS_VERSION={version}" not in info["Config"]["Env"]:
        raise ValueError("Built image does not contain the selected Jenkins version/base")
    run("python3", "-B", "image/ci/validate.py", candidate)
    if recipe_hash() != recipe:
        raise ValueError("Recipe changed during image validation")
    if args.verify_only:
        print("PASS: verified image locally on Runner; no push, copy, packaging or review submission.")
        return
    run("docker", "tag", candidate, output)
    run("docker", "push", output)
    pushed = json.loads(run("docker", "image", "inspect", output, capture_output=True).stdout)[0]
    if pushed["Id"] != info["Id"]:
        raise ValueError("Pushed image differs from tested image")
    custom_digest = platform_digest(output)
    document, actual = manifest(output + "@" + custom_digest)
    if actual != custom_digest or document.get("config", {}).get("digest") != info["Id"]:
        raise ValueError("Anonymous staging image differs from the tested local image")
    runtime = json.loads((ROOT / ".lazycat-action/runtime-proof.json").read_text())
    proof = {**runtime, "source_version": version, "source_ref": ref,
             "base_digest": digest, "application_version": os.environ["LAZYCAT_APPLICATION_VERSION"],
             "custom_ref": output, "custom_digest": custom_digest, "image_id": info["Id"],
             "recipe_sha256": recipe, "fingerprint": os.environ["LAZYCAT_BUILD_FINGERPRINT"]}
    proof_path.write_text(json.dumps(proof, indent=2) + "\n")
    print("Verified anonymous staging image:", output + "@" + custom_digest)


if __name__ == "__main__":
    main()
