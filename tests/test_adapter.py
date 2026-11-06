import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "image/ci"))
from contract import CHECKS, recipe_hash
from prepare import selected
from upstream import manifest as read_manifest, release_notes

spec = importlib.util.spec_from_file_location("verify_package", ROOT / "image/ci/verify-package.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class ChangelogTest(unittest.TestCase):
    def test_exact_release_only(self):
        html = '''<script>ignore</script><h3 data-type="release-header" data-version="2.580.2">2.580.2</h3>
        <div>October 28, 2026</div><div>Security</div><ul><li>Fix security vulnerability &amp; escape content.</li></ul>
        <h3 data-type="release-header" data-version="2.580.1">2.580.1</h3><li>OLD NOTES MUST NOT LEAK</li>'''
        text = release_notes(html, "2.580.2")
        self.assertIn("Fix security vulnerability & escape content.", text)
        self.assertNotIn("OLD NOTES", text)
        self.assertNotIn("2.580.1", text)
        self.assertNotIn("\n-\n", text)

    def test_missing_or_duplicate_release_fails(self):
        header = '<h3 data-type="release-header" data-version="2.580.2">2.580.2</h3><li>A genuine release note with sufficient length</li>'
        for html, version in ((header, "2.581.1"), (header * 2, "2.580.2"), (header, "2.589")):
            with self.subTest(version=version), self.assertRaises(ValueError):
                release_notes(html, version)


class SelectionTest(unittest.TestCase):
    def test_registry_pin_does_not_treat_tag_as_repository_path(self):
        digest = "sha256:" + "a" * 64
        with patch("upstream.fetch", return_value=b'{"schemaVersion":2}') as fetch:
            read_manifest("docker.io/jenkins/jenkins:2.580.2-jdk21@" + digest)
            self.assertEqual(fetch.call_args.args[0], "https://registry-1.docker.io/v2/jenkins/jenkins/manifests/" + digest)
            read_manifest("ttl.sh/lazycat-jenkins-abcdef123456:24h@" + digest)
            self.assertEqual(fetch.call_args.args[0], "https://ttl.sh/v2/lazycat-jenkins-abcdef123456/manifests/" + digest)

    def test_recipe_filters_lts_and_extracts_jdk_variant(self):
        import re
        rule = yaml.safe_load((ROOT / "lazycat-action.yml").read_text())["source"]["select"]
        for tag in ("2.580.2-jdk21", "2.580.10-jdk21", "2.600.1-jdk21"):
            self.assertIsNotNone(re.fullmatch(rule["tag_regex"], tag))
        for tag in ("2.589-jdk21", "lts-jdk21", "latest", "2.580.2-jdk25", "2.580.2-jdk21-preview"):
            self.assertIsNone(re.fullmatch(rule["tag_regex"], tag))
        versions = [re.fullmatch(rule["version_regex"], t)["version"] for t in ("2.580.2-jdk21", "2.580.10-jdk21")]
        self.assertEqual(sorted(versions, key=lambda v: tuple(map(int, v.split("."))))[-1], "2.580.10")

    def test_prepare_rejects_unpinned_or_unrelated_images(self):
        env = dict(LAZYCAT_SOURCE_VERSION="2.580.2", LAZYCAT_SOURCE_REF="docker.io/jenkins/jenkins:2.580.2-jdk21",
                   LAZYCAT_SOURCE_REVISION="sha256:" + "a" * 64, LAZYCAT_TARGET_PLATFORM="linux/amd64",
                   LAZYCAT_APPLICATION_VERSION="2.580.2", LAZYCAT_BUILD_FINGERPRINT="sha256:" + "b" * 64)
        self.assertEqual(selected(env)[0], "2.580.2")
        for key, value in (("LAZYCAT_SOURCE_REF", "docker.io/jenkins/jenkins:lts-jdk21"),
                           ("LAZYCAT_SOURCE_REF", "example.com/jenkins:2.580.2-jdk21"),
                           ("LAZYCAT_SOURCE_REVISION", "latest"), ("LAZYCAT_TARGET_PLATFORM", "linux/arm64"),
                           ("LAZYCAT_BUILD_FINGERPRINT", ""), ("LAZYCAT_SOURCE_VERSION", "2.589")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                selected({**env, key: value})


class PasswordTest(unittest.TestCase):
    """Execute the REAL entrypoint's Python initialization in disposable homes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        (self.home / "secrets").mkdir()
        entry = (ROOT / "image/entrypoint.sh").read_text()
        self.script = entry.split("python3 - <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        self.password = self.home / "secrets/admin-password"
        self.reset = self.home / "secrets/reset-admin-password"

    def run_init(self, exists=False, **extra):
        env = {"PATH": os.environ["PATH"], "JENKINS_HOME": str(self.home),
               "JENKINS_ADMIN_EXISTS": str(exists).lower(), "JENKINS_ADMIN_RESET_FILE": str(self.reset), **extra}
        return subprocess.run([sys.executable, "-B", "-c", self.script], env=env, capture_output=True)

    def test_new_install_and_existing_account_are_distinct(self):
        self.assertEqual(self.run_init(JENKINS_ADMIN_PASSWORD="peter111").returncode, 0)
        self.assertEqual(self.password.read_text().strip(), "peter111")
        self.password.write_text("last-reset-value\n")
        self.assertEqual(self.run_init(exists=True, JENKINS_ADMIN_PASSWORD="peter111").returncode, 0)
        self.assertEqual(self.password.read_text().strip(), "last-reset-value")
        self.assertEqual(self.password.stat().st_mode & 0o777, 0o600)

    def test_random_fallback_and_explicit_reset(self):
        self.assertEqual(self.run_init().returncode, 0)
        value = self.password.read_text().strip()
        self.assertEqual(len(value), 16)
        for chars in ("abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "0123456789", "_"):
            self.assertTrue(any(c in chars for c in value))
        self.reset.write_text("Reset_A1_password\n")
        self.assertEqual(self.run_init(exists=True, JENKINS_ADMIN_PASSWORD="peter111").returncode, 0)
        self.assertEqual(self.password.read_text().strip(), "Reset_A1_password")
        self.assertFalse(self.reset.exists())

    def test_invalid_reset_and_conflicting_sources_fail(self):
        self.reset.write_text("first\nsecond\n")
        self.assertNotEqual(self.run_init(exists=True).returncode, 0)
        self.reset.unlink()
        self.assertNotEqual(self.run_init(JENKINS_ADMIN_PASSWORD="peter111", JENKINS_ADMIN_PASSWORD_FILE="/missing").returncode, 0)


class DeliveryGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        shutil.copytree(ROOT / "image", self.root / "image", ignore=shutil.ignore_patterns("__pycache__"))
        for name in ("lzc-build.yml", "lzc-manifest.yml", "package.yml", "lzc-icon.png"):
            shutil.copy2(ROOT / name, self.root / name)
        self.manifest = self.root / "lzc-manifest.yml"
        import re
        # A real prior publication already contains an upstream pin. Replace,
        # do not prepend another one, so tests also pass after state commits.
        text = re.sub(r"(?m)^\s*# upstream: .+\n", "", self.manifest.read_text())
        self.pin = "ttl.sh/lazycat-jenkins-abcdef123456:24h@sha256:" + "c" * 64
        self.manifest.write_text(text.replace("    image:", "    # upstream: " + self.pin + "\n    image:"))
        version = yaml.safe_load((self.root / "package.yml").read_text())["version"]
        baseline = json.loads((self.root / "image/ci/baseline.json").read_text())
        self.proof = {"checks": CHECKS, "baseline_image": baseline["image"], "base_digest": "sha256:" + "a" * 64,
                      "custom_ref": self.pin.split("@")[0], "custom_digest": "sha256:" + "c" * 64,
                      "image_id": "sha256:" + "d" * 64, "fingerprint": "sha256:" + "e" * 64,
                      "application_version": version, "recipe_sha256": recipe_hash(self.root)}
        (self.root / ".lazycat-action").mkdir()
        self.save()

    def save(self):
        (self.root / ".lazycat-action/upgrade-proof.json").write_text(json.dumps(self.proof))

    def test_tested_image_allowed(self):
        guard.verify(self.root)

    def test_changed_image_or_incomplete_checks_blocked(self):
        self.manifest.write_text(self.manifest.read_text().replace(self.pin, self.pin[:-1] + "b"))
        with self.assertRaisesRegex(ValueError, "differs"):
            guard.verify(self.root)
        self.manifest.write_text(self.manifest.read_text().replace(self.pin[:-1] + "b", self.pin))
        self.proof["checks"] = CHECKS[:-1]
        self.save()
        with self.assertRaisesRegex(ValueError, "evidence"):
            guard.verify(self.root)

    def test_recipe_or_persistence_changed_after_test_blocked(self):
        self.manifest.write_text(self.manifest.read_text().replace("run_as: \"1000:1000\"", "run_as: \"0:0\""))
        with self.assertRaisesRegex(ValueError, "recipe changed"):
            guard.verify(self.root)

    def test_official_copy_and_allocated_version_required(self):
        self.manifest.write_text(self.manifest.read_text().replace("image: registry.lazycat.cloud/", "image: ttl.sh/"))
        with self.assertRaisesRegex(ValueError, "copy"):
            guard.verify(self.root)

    def test_version_change_and_image_recipe_change_blocked(self):
        self.proof["application_version"] = "999.0.0"
        self.save()
        with self.assertRaisesRegex(ValueError, "Application version"):
            guard.verify(self.root)
        self.proof["application_version"] = yaml.safe_load((self.root / "package.yml").read_text())["version"]
        self.save()
        dockerfile = self.root / "image/Dockerfile"
        dockerfile.write_text(dockerfile.read_text() + "\nRUN echo untested-change\n")
        with self.assertRaisesRegex(ValueError, "recipe changed"):
            guard.verify(self.root)


class MetadataTest(unittest.TestCase):
    def test_preserve_original_runtime_and_store_contract(self):
        manifest = yaml.load((ROOT / "lzc-manifest.yml").read_text(), Loader=yaml.BaseLoader)
        package = yaml.safe_load((ROOT / "package.yml").read_text())
        service = manifest["services"]["jenkins"]
        self.assertEqual(package["package"], "peter.lazycat.app.jenkins")
        self.assertTrue(package["admin_only"])
        self.assertEqual(package["unsupported_platforms"], ["android", "ios"])
        self.assertEqual(service["run_as"], "1000:1000")
        self.assertEqual(service["environment"]["JENKINS_URL"], "https://{{ .S.AppDomain }}/")
        self.assertIn("JENKINS_HOME=/lzcapp/var/jenkins", (ROOT / "image/Dockerfile").read_text())
        normal = yaml.safe_load((ROOT / "image/jenkins.yaml").read_text())
        bootstrap = yaml.safe_load((ROOT / "image/jenkins-bootstrap.yaml").read_text())
        self.assertNotIn("securityRealm", normal["jenkins"])
        self.assertIn("securityRealm", bootstrap["jenkins"])
        self.assertEqual(normal["appearance"]["themeManager"]["theme"], "dark")
        icon = (ROOT / "lzc-icon.png").read_bytes()
        self.assertEqual(icon[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", icon[16:24]), (512, 512))

    def test_workflow_reuses_v1_and_has_safe_manual_defaults(self):
        workflow = yaml.load((ROOT / ".github/workflows/update.yml").read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(workflow["jobs"]["update"]["uses"], "wcaqrl/lazycat-action/.github/workflows/lazycat.yml@v1")
        self.assertEqual(workflow["on"]["workflow_dispatch"]["inputs"]["dry-run"]["default"], "true")
        self.assertEqual(workflow["jobs"]["update"]["with"]["toolchains"], "docker")

    def test_inject_login_failure_success_and_changed_password(self):
        manifest = yaml.load((ROOT / "lzc-manifest.yml").read_text(), Loader=yaml.BaseLoader)
        injects = manifest["application"]["injects"]
        payload = {"request": injects[0]["do"], "response": injects[1]["do"]}
        javascript = r'''
const assert = require('node:assert/strict');
const scripts = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const capture = new Function('ctx', scripts.request);
const commit = new Function('ctx', scripts.response);
const flow = new Map(), persist = new Map();
const ctx = {flow, persist, request: {path:'/j_spring_security_check', method:'POST'},
  body:{getForm:()=>({j_username:['admin'],j_password:['wrong']})}, status:302,
  headers:{get:()=>'/loginError'}};
capture(ctx); assert.equal(persist.size,0); commit(ctx); assert.equal(persist.size,0);
ctx.headers.get=()=>'/'; commit(ctx); assert.equal(persist.get('jenkins.admin.password'),'wrong');
flow.clear(); ctx.request.path='/me/security/configSubmit';
ctx.body.getForm=()=>({'user.password':['new-secret'], 'user.password2':['different']});
capture(ctx); assert.equal(flow.size,0);
ctx.body.getForm=()=>({'user.password':['new-secret'], 'user.password2':['new-secret']});
capture(ctx); ctx.status=403; commit(ctx); assert.equal(persist.get('jenkins.admin.password'),'wrong');
ctx.status=302; commit(ctx); assert.equal(persist.get('jenkins.admin.password'),'new-secret');
'''
        subprocess.run(["node", "-e", javascript], input=json.dumps(payload), text=True, check=True)


if __name__ == "__main__":
    unittest.main()
