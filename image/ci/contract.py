"""Shared immutable delivery contract; standard library only."""

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKS = ["fresh-install", "baseline-install", "old-to-new-upgrade", "changed-password",
          "encrypted-credentials", "job-history", "artifacts", "pipeline-toolchains",
          "plugins", "dark-theme", "container-recreation", "password-reset"]


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
            # The executor legitimately changes only these two lines when it
            # replaces a tested staging image with the official copied image.
            import re
            data = re.sub(rb"(?m)^\s*# upstream: .+\n", b"", data)
            data = re.sub(rb"(?m)^(\s+image:) .+$", rb"\1 <delivered-image>", data)
        elif path.name == "package.yml":
            import re
            data = re.sub(rb"(?m)^version: .+$", b"version: <allocated-version>", data)
        digest.update(str(path.relative_to(root)).encode() + b"\0" + data + b"\0")
    return digest.hexdigest()
