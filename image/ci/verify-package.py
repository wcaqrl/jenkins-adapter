#!/usr/bin/env python3
"""Refuse packaging unless the exact delivered custom image passed all gates."""

import json
from pathlib import Path
import re

from contract import CHECKS, ROOT, recipe_hash
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
    if not re.search(r"^\s+image: registry\.lazycat\.cloud/\S+\s*$", manifest, re.MULTILINE):
        raise ValueError("LazyCat official image copy has not completed")
    package = (root / "package.yml").read_text()
    if re.search(r"^version:\s*(\S+)\s*$", package, re.MULTILINE)[1] != proof["application_version"]:
        raise ValueError("Application version differs from the verified release")
    if not re.search(r"^package: peter\.lazycat\.app\.jenkins\s*$", package, re.MULTILINE):
        raise ValueError("Unexpected application package")


if __name__ == "__main__":
    verify()
    print("PASS: final official image is the verified custom Jenkins image; recipe and package match.")
