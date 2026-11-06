#!/usr/bin/env python3
"""Read-only official Jenkins LTS discovery and exact-version changelog."""

import argparse
from html.parser import HTMLParser
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

IMAGE = "docker.io/jenkins/jenkins"
VERSION = re.compile(r"2\.[0-9]+\.[0-9]+")
TAG = re.compile(r"(2\.[0-9]+\.[0-9]+)-jdk21")
DIGEST = re.compile(r"sha256:[a-f0-9]{64}")
NOTES_URL = "https://www.jenkins.io/changelog-stable/"
ACCEPT = ", ".join(("application/vnd.oci.image.index.v1+json",
                    "application/vnd.docker.distribution.manifest.list.v2+json",
                    "application/vnd.oci.image.manifest.v1+json",
                    "application/vnd.docker.distribution.manifest.v2+json"))


def fetch(url, headers=None):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "lazycat-jenkins-adapter/1", **(headers or {})})
            with urllib.request.urlopen(req, timeout=45) as response:
                data = response.read(16 * 1024 * 1024 + 1)
            if len(data) > 16 * 1024 * 1024:
                raise ValueError("Upstream metadata exceeds limit")
            return data
        except urllib.error.HTTPError as error:
            if error.code < 500 and error.code != 429:
                raise
            if attempt == 2:
                raise
        except OSError:
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)


def manifest(reference):
    """Anonymous pull only; never read the developer PAT or Docker config."""
    host, path = reference.split("/", 1)
    if "@" in path:
        repository, tag = path.split("@", 1)
        # OCI permits repository:tag@digest. The tag is NOT part of the API's
        # repository path; the digest is the authoritative manifest selector.
        repository = repository.rsplit(":", 1)[0]
    else:
        repository, tag = path.rsplit(":", 1)
    host = "registry-1.docker.io" if host == "docker.io" else host
    url = f"https://{host}/v2/{repository}/manifests/{tag}"
    headers = {"Accept": ACCEPT}
    try:
        raw = fetch(url, headers)
    except urllib.error.HTTPError as error:
        if error.code != 401:
            raise
        challenge = error.headers.get("WWW-Authenticate", "")
        if not challenge.lower().startswith("bearer "):
            raise ValueError("Registry requires unsupported authentication") from error
        fields = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
        realm = fields.get("realm", "")
        if not realm.startswith("https://"):
            raise ValueError("Registry returned an insecure token endpoint")
        query = urllib.parse.urlencode({"service": fields.get("service", ""), "scope": fields.get("scope", f"repository:{repository}:pull")})
        token = json.loads(fetch(realm + ("&" if "?" in realm else "?") + query))
        headers["Authorization"] = "Bearer " + (token.get("token") or token["access_token"])
        raw = fetch(url, headers)
    import hashlib
    return json.loads(raw), "sha256:" + hashlib.sha256(raw).hexdigest()


def platform_digest(reference):
    document, digest = manifest(reference)
    if "manifests" not in document:
        if "config" not in document or "layers" not in document:
            raise ValueError("Registry did not return an image manifest")
        return digest
    matches = [m["digest"] for m in document["manifests"]
               if m.get("platform", {}).get("os") == "linux"
               and m.get("platform", {}).get("architecture") == "amd64"]
    if len(matches) != 1 or not DIGEST.fullmatch(matches[0]):
        raise ValueError("Expected exactly one linux/amd64 manifest")
    return matches[0]


def latest_candidate():
    # Read-only helper for push/verify CI. Publication discovery is owned by
    # lazycat-action, and prepare.py ALWAYS uses its selected immutable digest.
    token = json.loads(fetch("https://auth.docker.io/token?service=registry.docker.io&scope=repository%3Ajenkins%2Fjenkins%3Apull"))["token"]
    headers = {"Authorization": "Bearer " + token}
    candidates = []
    last = ""
    for _ in range(100):
        query = urllib.parse.urlencode({"n": 1000, "last": last})
        page = json.loads(fetch("https://registry-1.docker.io/v2/jenkins/jenkins/tags/list?" + query, headers))
        tags = page.get("tags") or []
        for tag in tags:
            match = TAG.fullmatch(tag)
            if match:
                candidates.append((tuple(map(int, match[1].split("."))), match[1], tag))
        if len(tags) < 1000:
            break
        if tags[-1] <= last:
            raise ValueError("Non-advancing registry pagination")
        last = tags[-1]
    else:
        raise ValueError("Jenkins tag pagination exceeded limit")
    if not candidates:
        raise ValueError("No stable Jenkins LTS linux/amd64 image found")
    _, version, tag = max(candidates)
    ref = IMAGE + ":" + tag
    return {"version": version, "tag": tag, "ref": ref, "digest": platform_digest(ref)}


class NotesParser(HTMLParser):
    def __init__(self, version):
        super().__init__(convert_charrefs=True)
        self.version = version
        self.active = False
        self.matches = 0
        self.skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("script", "style"):
            self.skip += 1
        if self.skip:
            return
        # Exact official release header, never a version mentioned in prose.
        if tag == "h3" and attrs.get("data-type") == "release-header":
            self.active = attrs.get("data-version") == self.version
            self.matches += int(self.active)
        if self.active and tag in ("div", "ul", "p", "h3", "br"):
            self.parts.append("\n")
        if self.active and tag == "li":
            self.parts.append("\n- ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
        if self.active and not self.skip and tag in ("div", "li", "p", "h3"):
            self.parts.append("\n")

    def handle_data(self, data):
        if self.active and not self.skip:
            # Source HTML puts newlines inside <li>/<span>. Only structural
            # tags should introduce output newlines, otherwise bullets split
            # into a lone '-' followed by several unrelated-looking lines.
            self.parts.append(re.sub(r"\s+", " ", data))


def release_notes(html, version):
    if not VERSION.fullmatch(version):
        raise ValueError("Expected a Jenkins LTS x.y.z version")
    parser = NotesParser(version)
    parser.feed(html)
    lines = [re.sub(r"\s+", " ", line).strip() for line in "".join(parser.parts).splitlines()]
    text = "\n".join(line for line in lines if line)
    if parser.matches != 1 or len(text) < 20 or len(text) > 24000:
        raise ValueError(f"Missing, duplicated or invalid Jenkins {version} release notes")
    return f"Jenkins LTS {version}\n\n{text}\n\nSource: https://www.jenkins.io/changelog/{version}/"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("candidate", "changelog"))
    args = parser.parse_args()
    if args.operation == "candidate":
        print(json.dumps(latest_candidate()))
    else:
        version = os.environ.get("LAZYCAT_SOURCE_VERSION", "")
        if os.environ.get("LAZYCAT_SOURCE_TAG", version + "-jdk21") != version + "-jdk21":
            raise ValueError("Selected Jenkins tag and version disagree")
        print(release_notes(fetch(NOTES_URL).decode(), version))


if __name__ == "__main__":
    main()
