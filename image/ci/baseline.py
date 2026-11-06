"""Load the EXACT approved image through LazyCat's public static blob CDN.

The store backend uses this same CDN layout (lpk_parse_service.go). Its Docker
registry can require authentication on hosted Runners. This short-lived read-only
loopback endpoint supplies the pinned manifest and redirects its allowlisted
blobs to the official HTTPS CDN. Docker verifies the config/layer digests; no
registry credentials, daemon configuration changes or substitute image are used.
"""

from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import subprocess
import threading
from urllib.parse import urlsplit

from upstream import DIGEST, fetch

CDN = "https://registry-static.lazycat.cloud/docker/registry/v2/blobs/sha256/"


def blob_url(digest):
    if not DIGEST.fullmatch(digest):
        raise ValueError("Invalid approved image blob digest")
    value = digest.removeprefix("sha256:")
    return CDN + value[:2] + "/" + value + "/data"


@contextmanager
def static_registry(raw, digest):
    if not DIGEST.fullmatch(digest) or "sha256:" + hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("Approved baseline manifest digest mismatch")
    document = json.loads(raw)
    allowed = {document["config"]["digest"], *(layer["digest"] for layer in document["layers"])}
    if document.get("schemaVersion") != 2 or not all(DIGEST.fullmatch(d) for d in allowed):
        raise ValueError("Invalid approved image descriptors")

    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = urlsplit(self.path).path.rstrip("/")
            if path == "/v2":
                body, kind = b"{}", "application/json"
            elif path == "/v2/baseline/manifests/" + digest:
                body, kind = raw, document.get("mediaType", "application/vnd.docker.distribution.manifest.v2+json")
            elif path.startswith("/v2/baseline/blobs/") and path.rsplit("/", 1)[1] in allowed:
                self.send_response(307)
                self.send_header("Location", blob_url(path.rsplit("/", 1)[1]))
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Docker-Distribution-Api-Version", "registry/2.0")
            self.send_header("Docker-Content-Digest", digest)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield f"127.0.0.1:{server.server_port}/baseline@{digest}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def load_baseline(baseline):
    reference = baseline["image"]
    if not reference.startswith("registry.lazycat.cloud/") or "@" not in reference:
        raise ValueError("Expected the approved LazyCat image pinned by digest")
    digest = reference.rsplit("@", 1)[1]
    raw = fetch(blob_url(digest))
    with static_registry(raw, digest) as reference:
        subprocess.run(["docker", "pull", "--platform", "linux/amd64", reference], check=True)
        info = json.loads(subprocess.check_output(["docker", "image", "inspect", reference], text=True))[0]
    expected_config = json.loads(raw)["config"]["digest"]
    if info["Id"] != expected_config or info["Os"] != "linux" or info["Architecture"] != "amd64":
        raise ValueError("Loaded baseline config/platform differs from the approved image")
    print("Verified exact approved baseline config:", expected_config, flush=True)
    return info["Id"]
