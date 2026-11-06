#!/usr/bin/env python3
"""Refuse packaging unless the exact delivered custom image passed all gates."""

import json
import re

from contract import CHECKS, ROOT, load_config, recipe_hash
from upstream import DIGEST


def verify(root=ROOT):
    proof = json.loads((root / ".lazycat-action/upgrade-proof.json").read_text())
    baseline = json.loads((root / "image/ci/baseline.json").read_text())
    manifest = (root / "lzc-manifest.yml").read_text()
    if proof.get("checks") != CHECKS or proof.get("baseline_image") != baseline["image"]:
        raise ValueError("Missing complete fresh-install and old-to-new upgrade evidence")
    if not all(DIGEST.fullmatch(proof.get(k, "")) for k in ("base_digest", "custom_digest", "image_id", "fingerprint")):
        raise ValueError("Invalid tested image digest/fingerprint")
    if proof["recipe_sha256"] != recipe_hash(root):
        raise ValueError("Image, tests or deployment recipe changed after verification")
    expected = proof["custom_ref"] + "@" + proof["custom_digest"]
    if re.findall(r"^\s*# upstream: (\S+)\s*$", manifest, re.MULTILINE) != [expected]:
        raise ValueError("Delivered custom image differs from the tested image")
    runtime = load_config(root / "lzc-manifest.yml")["services"]["jenkins"]["image"]
    if not isinstance(runtime, str) or not runtime.startswith("registry.lazycat.cloud/"):
        raise ValueError("LazyCat official image copy has not completed")
    package = load_config(root / "package.yml")
    if package.get("version") != proof["application_version"]:
        raise ValueError("Application version differs from the verified release")
    if package.get("package") != "peter.lazycat.app.jenkins":
        raise ValueError("Unexpected application package")


if __name__ == "__main__":
    verify()
    print("PASS: final official image is the verified custom Jenkins image; recipe and package match.")
