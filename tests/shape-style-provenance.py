import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "skill" / "shape-spec" / "scripts" / "validate-style.py"
FALLBACK = ROOT / "skill" / "shape-spec" / "assets" / "style" / "spec.css"


class ShapeStyleProvenanceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        self.spec = self.repo / "docs" / "specs" / "domains" / "example.spec.html"
        self.style = self.repo / "docs" / "specs" / ".style" / "spec.css"
        self.spec.parent.mkdir(parents=True)
        self.style.parent.mkdir(parents=True)
        self.git("init", "-q")
        self.git("config", "user.email", "spec-chat@example.test")
        self.git("config", "user.name", "Spec Chat Test")

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(
            ("git", "-C", str(self.repo), *args),
            check=True,
            text=True,
            capture_output=True,
        )

    def write_spec(self, inline=False, href="../.style/spec.css", extra=""):
        local = "<style>body { background: navy; }</style>" if inline else ""
        link = f'<link rel="stylesheet" href="{href}">' if href else ""
        self.spec.write_text(f"<!doctype html><html><head>{link}{extra}{local}</head><body></body></html>\n")

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        return self.git("rev-parse", "HEAD").stdout.strip()

    def empty_base(self):
        self.git("commit", "--allow-empty", "-qm", "base")
        return self.git("rev-parse", "HEAD").stdout.strip()

    def validate(self, base):
        return subprocess.run(
            (sys.executable, str(VALIDATOR), str(self.repo), str(self.spec), base),
            text=True,
            capture_output=True,
        )

    def test_accepts_unchanged_established_shared_style(self):
        self.write_spec()
        self.style.write_text("body { color: #111; }\n")
        base = self.commit()

        result = self.validate(base)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("style=established", result.stdout)

    def test_accepts_new_byte_identical_fallback(self):
        base = self.empty_base()
        self.write_spec()
        shutil.copy2(FALLBACK, self.style)

        result = self.validate(base)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("style=fallback", result.stdout)

    def test_rejects_spec_local_style_block(self):
        self.write_spec()
        self.style.write_text("body { color: #111; }\n")
        base = self.commit()
        self.write_spec(inline=True)

        result = self.validate(base)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("may not contain page-level <style> blocks", result.stderr)

    def test_rejects_new_custom_shared_style(self):
        base = self.empty_base()
        self.write_spec()
        self.style.write_text("body { background: linear-gradient(navy, blue); }\n")

        result = self.validate(base)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must match the bundled fallback", result.stderr)

    def test_rejects_changed_established_style(self):
        self.write_spec()
        self.style.write_text("body { color: #111; }\n")
        base = self.commit()
        self.style.write_text("body { color: #14213d; background: #eef3f8; }\n")

        result = self.validate(base)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("differs from the exact review base", result.stderr)

    def test_rejects_missing_shared_style_link(self):
        base = self.empty_base()
        self.write_spec(href="")

        result = self.validate(base)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exactly one repository-relative .style/spec.css", result.stderr)

    def test_rejects_an_additional_custom_stylesheet(self):
        base = self.empty_base()
        self.write_spec(extra='<link rel="stylesheet" href="../custom.css">')
        shutil.copy2(FALLBACK, self.style)

        result = self.validate(base)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("only one shared stylesheet", result.stderr)


if __name__ == "__main__":
    unittest.main()
