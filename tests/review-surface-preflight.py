import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "skill" / "review-spec" / "scripts" / "preflight.py"
BUNDLED_RUNTIME = ROOT / "skill" / "review-spec" / "assets" / "viz" / "runtime.js"
BUNDLED_SERVER = ROOT / "skill" / "review-spec" / "assets" / "review-serve.py"

RUNTIME_CAPABILITIES = "// spec-chat-capabilities: finish-review git-focus mobile-review reopen-thread semantic-islands\n"
SERVER_CAPABILITIES = "# spec-chat-capabilities: git-baseline narrow-review-root\n"


class ReviewSurfacePreflightTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        self.spec = self.repo / "docs" / "specs" / "example.spec.html"
        self.runtime = self.repo / "docs" / "specs" / ".viz" / "runtime.js"
        self.server = self.repo / "tools" / "review-serve.py"
        self.runtime.parent.mkdir(parents=True)
        self.server.parent.mkdir(parents=True)
        self.spec.write_text(
            '<figure><script type="application/spec+json" data-render="chart">{}</script>'
            '<div data-render-target="chart"></div></figure>\n'
        )

    def tearDown(self):
        self.temp.cleanup()

    def run_preflight(self, spec=None):
        return subprocess.run(
            (sys.executable, str(PREFLIGHT), str(self.repo), str(spec or self.spec)),
            text=True,
            capture_output=True,
        )

    def test_migrates_assets_that_lack_required_capabilities(self):
        self.runtime.write_text("// legacy runtime\n")
        self.server.write_text("# legacy server\n")

        result = self.run_preflight()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.runtime.read_bytes(), BUNDLED_RUNTIME.read_bytes())
        self.assertEqual(self.server.read_bytes(), BUNDLED_SERVER.read_bytes())
        self.assertIn("runtime=migrated", result.stdout)
        self.assertIn("server=migrated", result.stdout)

    def test_preserves_compatible_custom_assets(self):
        custom_runtime = RUNTIME_CAPABILITIES + "// custom runtime\n"
        custom_server = SERVER_CAPABILITIES + "# custom server\n"
        self.runtime.write_text(custom_runtime)
        self.server.write_text(custom_server)

        result = self.run_preflight()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.runtime.read_text(), custom_runtime)
        self.assertEqual(self.server.read_text(), custom_server)
        self.assertIn("runtime=compatible", result.stdout)
        self.assertIn("server=compatible", result.stdout)

    def test_rejects_a_visual_island_without_its_render_target(self):
        self.runtime.write_text(RUNTIME_CAPABILITIES)
        self.server.write_text(SERVER_CAPABILITIES)
        self.spec.write_text(
            '<figure><script type="application/spec+json" data-render="chart">{}</script></figure>\n'
        )

        result = self.run_preflight()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing data-render-target=\"chart\"", result.stderr)

    def test_rejects_a_spec_outside_the_target_repository(self):
        self.runtime.write_text(RUNTIME_CAPABILITIES)
        self.server.write_text(SERVER_CAPABILITIES)
        outside_temp = tempfile.TemporaryDirectory()
        self.addCleanup(outside_temp.cleanup)
        outside = Path(outside_temp.name) / "outside.spec.html"
        outside.write_text("<p>outside</p>\n")

        result = self.run_preflight(outside)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("inside the target repository", result.stderr)

    def test_refuses_to_migrate_through_an_asset_symlink(self):
        outside_temp = tempfile.TemporaryDirectory()
        self.addCleanup(outside_temp.cleanup)
        outside_runtime = Path(outside_temp.name) / "runtime.js"
        outside_runtime.write_text("// outside legacy runtime\n")
        self.runtime.symlink_to(outside_runtime)
        self.server.write_text(SERVER_CAPABILITIES)

        result = self.run_preflight()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("asset path escapes the target repository", result.stderr)
        self.assertEqual(outside_runtime.read_text(), "// outside legacy runtime\n")


if __name__ == "__main__":
    unittest.main()
