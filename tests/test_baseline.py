import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "image/ci"))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class BaselineTransportTest(unittest.TestCase):
    def metadata(self):
        config = b'{"architecture":"amd64","os":"linux"}'
        config_digest = "sha256:" + hashlib.sha256(config).hexdigest()
        layer_digest = "sha256:" + "b" * 64
        manifest = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
                               "config": {"digest": config_digest, "size": len(config)},
                               "layers": [{"digest": layer_digest, "size": 123}]}).encode()
        digest = "sha256:" + hashlib.sha256(manifest).hexdigest()
        return manifest, digest, config_digest, layer_digest

    def test_loopback_registry_returns_exact_pinned_manifest(self):
        from baseline import static_registry
        raw, digest, _, _ = self.metadata()
        with static_registry(raw, digest) as ref:
            endpoint = "http://" + ref.split("/")[0]
            with urllib.request.urlopen(endpoint + "/v2/", timeout=5) as response:
                self.assertEqual(response.status, 200)
            url = endpoint + "/v2/baseline/manifests/" + digest
            with urllib.request.urlopen(url, timeout=5) as response:
                self.assertEqual(response.read(), raw)
                self.assertEqual(response.headers["Docker-Content-Digest"], digest)
            with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5) as response:
                self.assertEqual(int(response.headers["Content-Length"]), len(raw))
                self.assertEqual(response.read(), b"")

    def test_redirects_only_manifest_blobs_to_official_https_cdn(self):
        from baseline import static_registry
        raw, digest, _, layer = self.metadata()
        opener = urllib.request.build_opener(NoRedirect())
        with static_registry(raw, digest) as ref:
            endpoint = "http://" + ref.split("/")[0] + "/v2/baseline/blobs/"
            with self.assertRaises(urllib.error.HTTPError) as caught:
                opener.open(endpoint + layer, timeout=5)
            self.assertEqual(caught.exception.code, 307)
            self.assertEqual(caught.exception.headers["Location"],
                             "https://registry-static.lazycat.cloud/docker/registry/v2/blobs/sha256/bb/" + "b" * 64 + "/data")
            with self.assertRaises(urllib.error.HTTPError) as caught:
                opener.open(endpoint + "sha256:" + "c" * 64, timeout=5)
            self.assertEqual(caught.exception.code, 404)

    def test_rejects_changed_manifest_before_exposing_registry(self):
        from baseline import static_registry
        raw, digest, _, _ = self.metadata()
        with self.assertRaisesRegex(ValueError, "digest"):
            with static_registry(raw + b" ", digest):
                self.fail("Mismatched baseline should never become pullable")

    def test_returns_only_verified_loaded_config_id(self):
        from baseline import load_baseline
        raw, digest, config, _ = self.metadata()
        expected = {"image": "registry.lazycat.cloud/peter/example:old@" + digest}
        info = [{"Id": config, "Os": "linux", "Architecture": "amd64"}]
        with patch("baseline.fetch", return_value=raw), patch("baseline.subprocess.check_output", return_value=json.dumps(info)), patch("baseline.subprocess.run"):
            self.assertEqual(load_baseline(expected), config)
        info[0]["Id"] = "sha256:" + "d" * 64
        with patch("baseline.fetch", return_value=raw), patch("baseline.subprocess.check_output", return_value=json.dumps(info)), patch("baseline.subprocess.run"):
            with self.assertRaisesRegex(ValueError, "config"):
                load_baseline(expected)
