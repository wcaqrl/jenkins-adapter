"""Exercise the real @v1 YAML editors without building or copying an image.

Set LAZYCAT_EXECUTOR_DIR to the lazycat-action checkout. CI checks out @v1 and
always runs this regression; local lightweight test runs can omit the checkout.
"""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import test_adapter as adapter


@unittest.skipUnless(os.environ.get("LAZYCAT_EXECUTOR_DIR"), "Set LAZYCAT_EXECUTOR_DIR to exercise actual executor YAML editors")
class ExecutorIntegrationTest(unittest.TestCase):
    def test_tested_image_remains_packageable_after_real_executor_updates(self):
        fixture = adapter.DeliveryGuardTest("test_tested_image_allowed")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        # Published package.yml advances automatically. Always select a new
        # fixture version so future checks still exercise the real YAML encoder.
        major, minor, patch = map(int, fixture.proof["application_version"].split("."))
        target_version = f"{major}.{minor}.{patch + 1}"
        fixture.proof["application_version"] = target_version
        fixture.save()
        executor = Path(os.environ["LAZYCAT_EXECUTOR_DIR"]).resolve()
        with tempfile.TemporaryDirectory(prefix="jenkins-executor-test-") as directory:
            work = Path(directory)
            # Use the executor's actual editor implementations, not a mocked
            # rewrite or a copy maintained in this adapter. Only compile the
            # small YAML packages so the local computer does not build the CLI.
            for name in ("internal/manifestedit/images.go", "internal/manifesttemplate/template.go",
                         "internal/yamledit/version.go", "internal/appversion/semver.go"):
                target = work / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(executor / name, target)
            (work / "go.mod").write_text("module github.com/wcaqrl/lazycat-action\n\ngo 1.24.0\n\nrequire go.yaml.in/yaml/v3 v3.0.4\n")
            (work / "main.go").write_text('''package main
import (
  "os"
  "path/filepath"
  "github.com/wcaqrl/lazycat-action/internal/manifestedit"
  "github.com/wcaqrl/lazycat-action/internal/yamledit"
)
func main() {
  root := os.Args[1]
  _, err := manifestedit.Apply(filepath.Join(root, "lzc-manifest.yml"), []manifestedit.Update{{
    Target: manifestedit.Target{Kind:manifestedit.TargetService, ID:"jenkins", Service:"jenkins"},
    SourceRef:os.Args[2], RuntimeRef:"registry.lazycat.cloud/peter/jenkins-integration:verified",
  }})
  if err != nil { panic(err) }
  change, err := yamledit.SetPackageVersion(filepath.Join(root, "package.yml"), os.Args[3])
  if err != nil { panic(err) }
  if !change.Changed { panic("regression must exercise actual package version rewriting") }
}
''')
            env = dict(os.environ, GOTOOLCHAIN="local", GOMAXPROCS="1", GOGC="50", GOMEMLIMIT="128MiB")
            subprocess.run(["go", "run", "-mod=mod", ".", str(fixture.root), fixture.pin, target_version], cwd=work, env=env, check=True)
        adapter.guard.verify(fixture.root)


if __name__ == "__main__":
    unittest.main()
