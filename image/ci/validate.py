#!/usr/bin/env python3
"""Disposable real old-to-new upgrade tests. Runs on GitHub Runner, not LightOS.

Exercises Jenkins API, encrypted credentials, changed admin password and an
actual Pipeline using Python/venv/Git/Go/Node/npm. Recreates containers around
the same persistent home; never imports or modifies production user data.
"""

import base64
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from xml.sax.saxutils import escape

from contract import CHECKS, ROOT


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


class Jenkins:
    def __init__(self, url, password="peter111"):
        self.url = url
        self.password = password
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, path, data=None, ctype=None):
        headers = {"Authorization": "Basic " + base64.b64encode(("admin:" + self.password).encode()).decode()}
        if ctype:
            headers["Content-Type"] = ctype
        if data is not None:
            crumb = json.loads(self.request("/crumbIssuer/api/json"))
            headers[crumb["crumbRequestField"]] = crumb["crumb"]
        return self.opener.open(urllib.request.Request(self.url + path, data=data, headers=headers), timeout=90).read()

    def script(self, script):
        body = urllib.parse.urlencode({"script": script}).encode()
        return self.request("/scriptText", body, "application/x-www-form-urlencoded").decode().strip()

    def wait(self, version):
        deadline = time.monotonic() + 420
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(self.url + "/login", timeout=5) as response:
                    if response.status == 200:
                        actual = response.headers.get("X-Jenkins")
                        if actual != version:
                            raise AssertionError(f"Expected Jenkins {version}, got {actual}")
                        who = json.loads(self.request("/whoAmI/api/json"))
                        if who.get("authenticated") and who.get("name") == "admin":
                            return
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(3)
        raise RuntimeError("Jenkins startup/authentication timed out")

    def configuration(self):
        try:
            urllib.request.urlopen(self.url + "/api/json", timeout=10)
            raise AssertionError("Anonymous access to Jenkins API unexpectedly allowed")
        except urllib.error.HTTPError as error:
            assert error.code in (401, 403), error.code
        config = json.loads(self.script('''import jenkins.model.Jenkins
import groovy.json.JsonOutput
def j = Jenkins.get()
println JsonOutput.toJson([theme:j.getDescriptor('io.jenkins.plugins.thememanager.ThemeManagerPageDecorator')?.getThemeKey(),executors:j.numExecutors,failedPlugins:j.pluginManager.failedPlugins.collect{it.name}])'''))
        assert config["theme"] == "dark" and config["executors"] == 2 and not config["failedPlugins"], config
        installed = json.loads(self.request("/pluginManager/api/json?tree=plugins[shortName,version,active]"))["plugins"]
        for name in (ROOT / "image/plugins.txt").read_text().splitlines():
            assert any(p["shortName"] == name and p["active"] for p in installed), name
        return installed

    def seed(self, password, credential):
        self.script('''import jenkins.model.Jenkins
import com.cloudbees.plugins.credentials.SystemCredentialsProvider
import com.cloudbees.plugins.credentials.CredentialsScope
import com.cloudbees.plugins.credentials.domains.Domain
import com.cloudbees.plugins.credentials.impl.UsernamePasswordCredentialsImpl
def store = SystemCredentialsProvider.getInstance().getStore()
assert store.addCredentials(Domain.global(), new UsernamePasswordCredentialsImpl(CredentialsScope.GLOBAL, "adapter-upgrade-test", "Disposable upgrade fixture", "test-user", %s))
println "credential-created"
''' % json.dumps(credential))
        self.pipeline()
        self.script('''import hudson.model.User
import hudson.security.HudsonPrivateSecurityRealm
def u=User.getById("admin", false)
u.addProperty(HudsonPrivateSecurityRealm.Details.fromPlainPassword(%s))
u.save()
println "password-updated"''' % json.dumps(password))
        self.password = password

    def assert_initial_rejected(self):
        initial = Jenkins(self.url)
        try:
            who = json.loads(initial.request("/whoAmI/api/json"))
            assert not who.get("authenticated"), "Initial password unexpectedly accepted after change"
        except urllib.error.HTTPError as error:
            assert error.code in (401, 403), error.code

    def assert_data(self, credential, artifact):
        result = json.loads(self.request("/job/adapter-toolchains/1/api/json"))
        assert result["result"] == "SUCCESS", result
        assert self.request("/job/adapter-toolchains/1/artifact/toolchain-report.txt") == artifact
        found = self.script('''import com.cloudbees.plugins.credentials.SystemCredentialsProvider
def c = SystemCredentialsProvider.getInstance().getCredentials().find{it.id == 'adapter-upgrade-test'}
assert c != null && c.username == 'test-user' && c.password.plainText == %s
println 'credential-decrypted' ''' % json.dumps(credential))
        assert found == "credential-decrypted", found

    def pipeline(self):
        pipeline = r'''pipeline {
  agent any
  stages {
    stage('Toolchains') {
      steps {
        sh ''' + "'''" + r'''set -eu
python3.12 -c 'import ssl, sqlite3, bz2, lzma, ctypes; import sys; assert sys.version_info[:2] == (3,12)'
python3.12 -m venv .venv
.venv/bin/python -m pip --version
git init -q test-repo
git -C test-repo -c user.name=Validation -c user.email=validation@localhost commit --allow-empty -qm validation
printf 'package main\nimport "fmt"\nfunc main(){fmt.Println("go-toolchain-ok")}\n' > hello.go
GOTOOLCHAIN=local go run hello.go
mkdir -p node-test
cd node-test
npm init -y >/dev/null
node -e 'require("fs").writeFileSync("test.js", "console.log(6*7)")'
npm pkg set scripts.test="node test.js"
npm test
cd ..
{ python3.12 --version; git --version; go version; node --version; npm --version; java -version 2>&1; } > toolchain-report.txt
''' + "'''" + r'''
        archiveArtifacts artifacts: 'toolchain-report.txt', fingerprint: true
      }
    }
  }
}'''
        xml = '<flow-definition plugin="workflow-job"><description>Disposable adapter verification</description><keepDependencies>false</keepDependencies><properties/><definition class="org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition" plugin="workflow-cps"><script>' + escape(pipeline) + '</script><sandbox>true</sandbox></definition><triggers/><disabled>false</disabled></flow-definition>'
        try:
            info = json.loads(self.request("/job/adapter-toolchains/api/json"))
            previous = info["lastBuild"]["number"] if info.get("lastBuild") else 0
            self.request("/job/adapter-toolchains/config.xml", xml.encode(), "application/xml")
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
            previous = 0
            self.request("/createItem?name=adapter-toolchains", xml.encode(), "application/xml")
        self.request("/job/adapter-toolchains/build", b"", "application/x-www-form-urlencoded")
        deadline = time.monotonic() + 480
        while time.monotonic() < deadline:
            try:
                result = json.loads(self.request("/job/adapter-toolchains/lastBuild/api/json"))
                if result["number"] > previous and not result["building"]:
                    if result["result"] != "SUCCESS":
                        print(self.request("/job/adapter-toolchains/lastBuild/consoleText").decode())
                        raise AssertionError("Toolchain Pipeline failed")
                    return self.request("/job/adapter-toolchains/lastSuccessfulBuild/artifact/toolchain-report.txt")
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    raise
            time.sleep(3)
        raise RuntimeError("Toolchain Pipeline timed out")


def main():
    candidate = sys.argv[1]
    baseline = json.loads((ROOT / "image/ci/baseline.json").read_text())
    versions = json.loads((ROOT / "image/versions.lock.json").read_text())
    version = os.environ["LAZYCAT_SOURCE_VERSION"]
    reports = ROOT / ".lazycat-action/reports"
    reports.mkdir(parents=True, exist_ok=True)
    proof = ROOT / ".lazycat-action/runtime-proof.json"
    proof.unlink(missing_ok=True)
    name = "jenkins-adapter-test-" + secrets.token_hex(6)
    directory = Path(tempfile.mkdtemp(prefix="jenkins-data-"))
    active = False

    def stop():
        nonlocal active
        if active:
            # Graceful termination is necessary to persist queued Pipeline data.
            docker("stop", "--time", "60", name)
            docker("rm", "--volumes", name)
            active = False

    def start(image, home, password="peter111"):
        nonlocal active
        data = directory / home
        for p in (data / "var", data / "cache"):
            p.mkdir(parents=True, exist_ok=True)
        subprocess.run(["sudo", "chown", "-R", "1000:1000", str(data)], check=True)
        docker("run", "--detach", "--name", name, "--cpus", "2", "--memory", "3g",
               "--publish", "127.0.0.1::8080", "--mount", f"type=bind,src={data / 'var'},dst=/lzcapp/var",
               "--mount", f"type=bind,src={data / 'cache'},dst=/lzcapp/cache",
               "--env", "JENKINS_ADMIN_PASSWORD=peter111", image)
        active = True
        port = docker("port", name, "8080/tcp").rsplit(":", 1)[1]
        return Jenkins("http://127.0.0.1:" + port, password)

    try:
        print("Testing fresh installation", flush=True)
        fresh = start(candidate, "fresh")
        fresh.wait(version)
        installed = fresh.configuration()
        tools = fresh.pipeline().decode()
        assert versions["go"] in tools and versions["node"] in tools and versions["npm"] in tools, tools
        for path, value in (("plugins.json", json.dumps(installed, indent=2)), ("toolchains.txt", tools)):
            (reports / path).write_text(value)
        print("Verified installed toolchains:\n" + tools, flush=True)
        print("Verified active plugins: " + json.dumps(installed, sort_keys=True), flush=True)
        stop()

        print("Seeding approved baseline Jenkins " + baseline["source_version"], flush=True)
        old = start(baseline["image"], "upgrade")
        old.wait(baseline["source_version"])
        old.configuration()
        changed = "Aa1_" + secrets.token_hex(6)
        credential = secrets.token_urlsafe(24)
        old.seed(changed, credential)
        artifact = old.request("/job/adapter-toolchains/1/artifact/toolchain-report.txt")
        stop()

        print("Upgrading the same persistent home to Jenkins " + version, flush=True)
        for phase in ("upgrade", "container-recreation"):
            current = start(candidate, "upgrade", changed)
            current.wait(version)
            current.assert_initial_rejected()
            current.configuration()
            current.assert_data(credential, artifact)
            if phase == "upgrade":
                current.pipeline()
            stop()
        print("Testing explicit password reset request", flush=True)
        new_password = "Reset_A1_" + secrets.token_hex(5)
        # User data is owned by UID 1000; write through a disposable helper in
        # the already tested image, rather than chmod'ing Jenkins' secrets.
        docker("run", "--rm", "--entrypoint", "python3", "--mount",
               f"type=bind,src={directory / 'upgrade/var'},dst=/lzcapp/var", candidate,
               "-c", "from pathlib import Path; p=Path('/lzcapp/var/jenkins/secrets/reset-admin-password'); p.write_text(" + repr(new_password + "\n") + "); p.chmod(0o600)")
        current = start(candidate, "upgrade", new_password)
        current.wait(version)
        current.assert_data(credential, artifact)
        current.assert_initial_rejected()
        assert current.script("println new File(System.getenv('JENKINS_HOME'), 'secrets/reset-admin-password').exists()") == "false"
        stop()
        proof.write_text(json.dumps({"checks": CHECKS, "baseline_image": baseline["image"],
                                    "artifact_sha256": hashlib.sha256(artifact).hexdigest()}, indent=2) + "\n")
        print("PASS: fresh install, real upgrade, recreation, password preservation/reset, encrypted credentials, jobs, tools, plugins and theme.")
    except BaseException:
        if active:
            log = docker("logs", "--tail", "200", name)
            (reports / "container-failure.log").write_text(log)
            print(log)
        raise
    finally:
        stop()
        subprocess.run(["sudo", "rm", "-rf", str(directory)], check=True)


if __name__ == "__main__":
    main()
