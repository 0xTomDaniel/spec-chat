import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "skill" / "review-spec" / "scripts" / "preflight.py"
BUNDLED_RUNTIME = ROOT / "skill" / "review-spec" / "assets" / "viz" / "runtime.js"

SKILL = ROOT / "skill" / "review-spec" / "SKILL.md"

RUNTIME_CAPABILITIES = "// spec-chat-capabilities: changed-root-focus custom-style-focus diff-visibility-control finish-review git-focus manual-resume-status mobile-pre-wrap mobile-review next-tbd reopen-thread semantic-islands shared-style-ownership spec-acceptance tbd-later\n"


class ReviewSurfacePreflightTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        self.spec = self.repo / "docs" / "specs" / "example.spec.html"
        self.runtime = self.repo / "docs" / "specs" / ".viz" / "runtime.js"
        self.runtime.parent.mkdir(parents=True)
        self.spec.write_text(
            '<script defer src="./.viz/runtime.js"></script>'
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

    def test_migrates_a_runtime_that_lacks_required_capabilities(self):
        self.runtime.write_text("// legacy runtime\n")

        result = self.run_preflight()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.runtime.read_bytes(), BUNDLED_RUNTIME.read_bytes())
        self.assertIn("runtime=migrated", result.stdout)

    def git(self, *args):
        return subprocess.run(
            ("git", "-C", str(self.repo), *args),
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()

    def init_git(self, runtime_text):
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.test")
        self.runtime.write_text(runtime_text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        return self.git("rev-parse", "HEAD")

    def test_commits_the_migration_alone_and_names_it(self):
        base = self.init_git("// legacy runtime\n")
        self.spec.write_text(self.spec.read_text() + "<p>feature edit</p>\n")
        self.git("add", str(self.spec))

        result = self.run_preflight()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        head = self.git("rev-parse", "HEAD")
        self.assertNotEqual(head, base)
        self.assertEqual(self.git("rev-parse", "HEAD~1"), base)
        self.assertIn(f"migration={head}", result.stdout)
        changed = self.git("diff", "--name-only", base, head).splitlines()
        self.assertTrue(changed)
        self.assertTrue(
            all(path.startswith("docs/specs/.viz/") for path in changed), changed
        )
        self.assertEqual(
            self.git("diff", "--cached", "--name-only"), "docs/specs/example.spec.html"
        )
        self.assertEqual(self.git("status", "--porcelain", "--", "docs/specs/.viz"), "")

    def test_compatible_runtime_makes_no_commit(self):
        base = self.init_git(RUNTIME_CAPABILITIES)

        result = self.run_preflight()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), base)
        self.assertNotIn("migration=", result.stdout)

    def test_locked_index_fails_and_leaves_the_target_migratable(self):
        base = self.init_git("// legacy runtime\n")
        lock = self.repo / ".git" / "index.lock"
        lock.write_text("")

        result = self.run_preflight()

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertNotIn("runtime=migrated", result.stdout)
        self.assertEqual(self.runtime.read_text(), "// legacy runtime\n")
        self.assertEqual(self.git("rev-parse", "HEAD"), base)

        lock.unlink()
        rerun = self.run_preflight()

        self.assertEqual(rerun.returncode, 0, rerun.stdout + rerun.stderr)
        head = self.git("rev-parse", "HEAD")
        self.assertNotEqual(head, base)
        self.assertIn(f"migration={head}", rerun.stdout)

    def test_ignored_runtime_assets_fail_and_leave_the_target_migratable(self):
        base = self.init_git("// legacy runtime\n")
        (self.repo / ".gitignore").write_text("docs/specs/.viz/\n")

        result = self.run_preflight()

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertNotIn("runtime=migrated", result.stdout)
        self.assertEqual(self.runtime.read_text(), "// legacy runtime\n")
        self.assertEqual(self.git("rev-parse", "HEAD"), base)

    def test_target_nested_in_a_parent_repository_commits_nothing(self):
        self.runtime.write_text("// legacy runtime\n")
        parent = self.repo.parent / (self.repo.name + "-parent")
        parent.mkdir()
        self.addCleanup(shutil.rmtree, parent)
        nested = parent / "target"
        shutil.move(str(self.repo), nested)
        self.repo.mkdir()
        self.repo, self.spec, self.runtime = (
            nested,
            nested / self.spec.relative_to(self.repo),
            nested / self.runtime.relative_to(self.repo),
        )
        subprocess.run(("git", "init", "-q", str(parent)), check=True)

        result = self.run_preflight()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("migration=", result.stdout)
        head = subprocess.run(
            ("git", "-C", str(parent), "rev-parse", "--verify", "-q", "HEAD"),
            capture_output=True,
        )
        self.assertNotEqual(head.returncode, 0)

    def test_migration_commit_leaves_other_work_under_viz_alone(self):
        base = self.init_git("// legacy runtime\n")
        wip = self.runtime.parent / "notes.wip"
        wip.write_text("user work\n")

        result = self.run_preflight()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        changed = self.git("diff", "--name-only", base, "HEAD").splitlines()
        self.assertNotIn("docs/specs/.viz/notes.wip", changed)
        self.assertEqual(
            self.git("status", "--porcelain", "--", str(wip)), "?? docs/specs/.viz/notes.wip"
        )

    def test_skill_ships_the_migration_commit_inside_the_feature_change(self):
        text = " ".join(SKILL.read_text().split())
        for phrase in (
            "preflight commits it alone, touching only the shared runtime assets",
            "hand-off names that commit",
            "ships inside the same change as the feature",
        ):
            self.assertTrue(phrase in text, phrase)
        self.assertFalse("Commit migrated assets locally" in text)

    def test_never_writes_a_review_server_into_the_target(self):
        self.runtime.write_text("// legacy runtime\n")

        result = self.run_preflight()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.repo / "tools").exists())
        self.assertNotIn("server=", result.stdout)

    def test_migrates_a_runtime_that_lacks_changed_root_focus(self):
        self.runtime.write_text(
            "// spec-chat-capabilities: finish-review git-focus manual-resume-status mobile-review reopen-thread semantic-islands\n"
        )

        result = self.run_preflight()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.runtime.read_bytes(), BUNDLED_RUNTIME.read_bytes())
        self.assertIn("runtime=migrated", result.stdout)

    def test_migrates_changed_root_focus_without_custom_style_hardening(self):
        self.runtime.write_text(
            "// spec-chat-capabilities: changed-root-focus finish-review git-focus manual-resume-status mobile-review reopen-thread semantic-islands\n"
        )

        result = self.run_preflight()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.runtime.read_bytes(), BUNDLED_RUNTIME.read_bytes())
        self.assertIn("runtime=migrated", result.stdout)

    def test_migrates_a_runtime_that_lacks_spec_acceptance_and_tbd_later(self):
        self.runtime.write_text(RUNTIME_CAPABILITIES.replace(" spec-acceptance tbd-later", ""))

        result = self.run_preflight()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.runtime.read_bytes(), BUNDLED_RUNTIME.read_bytes())
        self.assertIn("runtime=migrated", result.stdout)

    def test_migrates_a_runtime_that_lacks_next_tbd(self):
        self.runtime.write_text(RUNTIME_CAPABILITIES.replace(" next-tbd", ""))

        result = self.run_preflight()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.runtime.read_bytes(), BUNDLED_RUNTIME.read_bytes())
        self.assertIn("runtime=migrated", result.stdout)

    def test_preserves_compatible_custom_assets(self):
        custom_runtime = RUNTIME_CAPABILITIES + "// custom runtime\n"
        self.runtime.write_text(custom_runtime)

        result = self.run_preflight()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.runtime.read_text(), custom_runtime)
        self.assertIn("runtime=compatible", result.stdout)

    def test_rejects_a_visual_island_without_its_render_target(self):
        self.runtime.write_text(RUNTIME_CAPABILITIES)
        self.spec.write_text(
            '<script defer src="./.viz/runtime.js"></script>'
            '<figure><script type="application/spec+json" data-render="chart">{}</script></figure>\n'
        )

        result = self.run_preflight()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing data-render-target=\"chart\"", result.stderr)

    def test_rejects_a_spec_outside_the_target_repository(self):
        self.runtime.write_text(RUNTIME_CAPABILITIES)
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

        result = self.run_preflight()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("asset path escapes the target repository", result.stderr)
        self.assertEqual(outside_runtime.read_text(), "// outside legacy runtime\n")

    def test_migrates_the_repository_relative_runtime_referenced_by_a_nested_spec(self):
        nested = self.repo / "spec" / "domains" / "nested.spec.html"
        target_runtime = self.repo / "spec" / ".viz" / "runtime.js"
        nested.parent.mkdir(parents=True)
        nested.write_text('<script defer src="../.viz/runtime.js"></script>\n')

        result = self.run_preflight(nested)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(target_runtime.read_bytes(), BUNDLED_RUNTIME.read_bytes())
        self.assertFalse((self.repo / "docs" / "specs" / ".viz" / "vendor").exists())

    def test_rejects_missing_ambiguous_remote_and_noncanonical_runtime_references(self):
        cases = {
            "missing": "<p>No runtime.</p>\n",
            "ambiguous": (
                '<script defer src="./.viz/runtime.js"></script>'
                '<script defer src="../other/runtime.js"></script>\n'
            ),
            "remote": '<script defer src="https://example.test/runtime.js"></script>\n',
            "noncanonical": '<script defer src="./app-runtime.js"></script>\n',
        }
        for name, html in cases.items():
            with self.subTest(name=name):
                self.spec.write_text(html)
                result = self.run_preflight()
                self.assertEqual(result.returncode, 2)
                self.assertIn("exactly one repository-relative runtime.js", result.stderr)


if __name__ == "__main__":
    unittest.main()
