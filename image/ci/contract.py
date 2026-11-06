"""Shared immutable delivery contract with semantic YAML comparison."""

import hashlib
import json
from pathlib import Path
import re

import yaml

ROOT = Path(__file__).resolve().parents[2]
CHECKS = ["fresh-install", "baseline-install", "old-to-new-upgrade", "changed-password",
          "encrypted-credentials", "job-history", "artifacts", "pipeline-toolchains",
          "plugins", "dark-theme", "container-recreation", "password-reset"]


class ConfigLoader(yaml.SafeLoader):
    # Match Go's YAML boolean handling. YAML 1.1's on/off/yes/no conversion
    # would incorrectly turn the injects' literal `on` key into a boolean.
    yaml_implicit_resolvers = {
        key: [(tag, pattern) for tag, pattern in rules if tag != "tag:yaml.org,2002:bool"]
        for key, rules in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }


ConfigLoader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))


def load_config(path):
    document = yaml.load(path.read_text(), Loader=ConfigLoader)
    if not isinstance(document, dict):
        raise ValueError("Expected a YAML configuration mapping: " + str(path))
    return document


def recipe_hash(root=ROOT):
    """Hash all build/test inputs, excluding transient downloads and bytecode."""
    digest = hashlib.sha256()
    paths = [root / "lzc-manifest.yml", root / "lzc-build.yml", root / "lzc-icon.png", root / "package.yml"]
    paths += [p for p in (root / "image").rglob("*") if p.is_file()
              and "downloads" not in p.parts and "__pycache__" not in p.parts
              and p.suffix != ".pyc"]
    for path in sorted(paths):
        data = path.read_bytes()
        if path.name == "lzc-manifest.yml":
            # @v1 rewrites the entire YAML document while replacing image and
            # upstream comment. Compare parsed content, not emitted whitespace
            # or scalar style. Script strings and scalar types remain exact.
            document = load_config(path)
            document["services"]["jenkins"]["image"] = "<delivered-image>"
            data = json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        elif path.name == "package.yml":
            document = load_config(path)
            document["version"] = "<allocated-version>"
            data = json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        digest.update(str(path.relative_to(root)).encode() + b"\0" + data + b"\0")
    return digest.hexdigest()
