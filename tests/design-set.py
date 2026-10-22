"""The pinned design set (spec-page-design #design-set, lane-hosting #index-tokens): one pin for Spec Chat, and every copy
built from exactly the pinned commit by scripts/design-set-sync, never by hand."""

import importlib.machinery
import importlib.util
import os
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_loader = importlib.machinery.SourceFileLoader("design_set_sync", str(ROOT / "scripts/design-set-sync"))
SYNC = importlib.util.module_from_spec(importlib.util.spec_from_loader("design_set_sync", _loader))
_loader.exec_module(SYNC)
RUNTIME = "skill/review-spec/assets/viz/runtime.js"
# A design-set clone holding the pinned commit; the set's own bytes are checked only where one is given.
CLONE = os.environ.get("DESIGN_SET_CLONE", "")


class DesignSetPin(unittest.TestCase):
    def test_one_pin_names_a_repository_and_a_whole_commit(self):
        pin = SYNC.pin()
        self.assertEqual(set(pin), {"repository", "commit"})
        self.assertRegex(pin["commit"], r"^[0-9a-f]{40}$")
        self.assertTrue(pin["repository"].startswith("https://"))

    def test_every_copy_is_built_from_the_pinned_commit(self):
        """A pin moved without a sync leaves a region naming the old commit."""
        for path, regions in SYNC.COPIES.items():
            for name, _ in regions:
                with self.subTest(path=path, region=name or "tokens"):
                    self.assertEqual(SYNC.region((ROOT / path).read_text(), path, name)[2], SYNC.pin()["commit"])

    def test_every_copy_follows_the_set_the_runtime_holds(self):
        """Offline: each copy is exactly what the sync builds from the tokens.css and icons the runtime holds, so the
        document and the chrome never drift apart; the index serves the runtime's own block (review-serve-multi.py)."""
        runtime = (ROOT / RUNTIME).read_text()
        start, end, commit = SYNC.region(runtime, RUNTIME)
        block = runtime[start:end]
        css = block[block.index("*/\n") + 3:-len(SYNC.END)]
        light, dark = SYNC.tokens(css)
        self.assertTrue(light and dark)
        files = {"tokens.css": css, **SYNC.held_icons(runtime, RUNTIME)}
        for path in SYNC.COPIES:
            with self.subTest(path=path):
                self.assertEqual((ROOT / path).read_text(), SYNC.rebuild(path, commit, files), f"{path}: run scripts/design-set-sync")

    def test_the_runtime_holds_the_set_line_icons(self):
        """spec-page-design #icons-rule: every chrome icon is one of the set's line icons, one size and stroke weight; the
        chrome draws the panel toggle, gear, dock, disclosure, and back link from them, never a glyph of its own."""
        runtime = (ROOT / RUNTIME).read_text()
        icons = SYNC.held_icons(runtime, RUNTIME)
        self.assertTrue({"icons/panel.svg", "icons/gear.svg", "icons/threads.svg", "icons/chevron-left.svg",
                         "icons/chevron-right.svg", "icons/chevron-down.svg", "icons/external.svg"} <= set(icons))
        for path, svg in icons.items():
            with self.subTest(icon=path):
                self.assertRegex(svg, r'^<svg [^>]*width="16" height="16" viewBox="0 0 16 16"')
                self.assertIn('stroke-width="1.75"', svg)
                self.assertIn('stroke="currentColor"', svg)
                self.assertIn('aria-hidden="true"', svg)
        for used in ("ICONS.panel", "ICONS.gear", "ICONS.threads", "ICONS['chevron-left']", "ICONS['chevron-right']",
                     "ICONS['chevron-down']", "ICONS.external"):
            self.assertIn(used, runtime)

    def test_the_repo_spec_pages_take_the_bundled_stylesheet(self):
        """docs/specs/.style/spec.css is this repo's copy of the bundled stylesheet (review-spec-scripts.sh holds the runtime's)."""
        self.assertEqual((ROOT / "docs/specs/.style/spec.css").read_bytes(),
                         (ROOT / "skill/shape-spec/assets/style/spec.css").read_bytes())

    @unittest.skipUnless(CLONE, "DESIGN_SET_CLONE names no design-set clone")
    def test_every_copy_matches_the_pinned_files(self):
        for path, text in SYNC.build(Path(CLONE)).items():
            with self.subTest(path=path):
                self.assertEqual((ROOT / path).read_text(), text, f"{path}: run scripts/design-set-sync")


if __name__ == "__main__":
    unittest.main()
