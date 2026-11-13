"""The pinned design set (spec-page-design #design-set, review-index #index-tokens): one pin for Spec Chat, and every copy
built from exactly the pinned commit by scripts/design-set-sync, never by hand."""

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("review_serve_design_set", ROOT / "skill/review-spec/assets/review-serve.py")
_serve = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_serve)
# scripts/design-set-sync, through the review service's one load of it
SYNC = _serve._SYNC
RUNTIME = "skill/review-spec/assets/viz/runtime.js"
# A design-set clone holding the pinned commit; the set's own bytes are checked only where one is given.
CLONE = os.environ.get("DESIGN_SET_CLONE", "")


def commit_set(clone: Path, files: dict[str, str], links: dict[str, str] | None = None, modules: tuple[str, ...] = ()) -> str:
    """A throwaway set repository at ``clone``: each file (path: text, its UTF-8 bytes, newlines as given), link (path:
    target), and submodule path in one commit; that commit."""
    for path, text in files.items():
        (clone / path).parent.mkdir(parents=True, exist_ok=True)
        (clone / path).write_bytes(text.encode())
    for path, target in (links or {}).items():
        os.symlink(target, clone / path)
    git = ["git", "-C", str(clone), "-c", "user.name=T", "-c", "user.email=t@example.com", "-c", "core.autocrlf=false"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    for path in modules:
        subprocess.run([*git, "update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},{path}"], check=True)
    subprocess.run([*git, "commit", "-qm", "set"], check=True)
    return subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


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
        """Offline: each copy is exactly what the sync builds from the tokens.css, icons, marks, and Find field the runtime
        holds, so the document and the chrome never drift apart; the index serves the runtime's own blocks
        (review-serve-multi.py)."""
        runtime = (ROOT / RUNTIME).read_text()
        start, end, commit = SYNC.region(runtime, RUNTIME)
        block = runtime[start:end]
        css = block[block.index("*/\n") + 3:-len(SYNC.END)]
        light, dark = SYNC.tokens(css)
        self.assertTrue(light and dark)
        files = {"tokens.css": css}
        for name in SYNC.MAPS:
            files.update(SYNC.held(runtime, RUNTIME, name))
        for path in SYNC.COPIES:
            with self.subTest(path=path):
                self.assertEqual((ROOT / path).read_text(), SYNC.rebuild(path, commit, files), f"{path}: run scripts/design-set-sync")

    def test_the_runtime_holds_the_set_line_icons(self):
        """spec-page-design #icons-rule: every chrome icon is one of the set's line icons, one size and stroke weight; the
        chrome draws the panel toggle, gear, dock, back link, and the focused thread's back from them (threads #focus),
        never a glyph of its own."""
        runtime = (ROOT / RUNTIME).read_text()
        icons = SYNC.held(runtime, RUNTIME, SYNC.ICONS)
        self.assertTrue({"icons/panel.svg", "icons/gear.svg", "icons/threads.svg", "icons/chevron-left.svg",
                         "icons/chevron-right.svg", "icons/chevron-down.svg", "icons/external.svg"} <= set(icons))
        for path, svg in icons.items():
            with self.subTest(icon=path):
                self.assertRegex(svg, r'^<svg [^>]*width="16" height="16" viewBox="0 0 16 16"')
                self.assertIn('stroke-width="1.75"', svg)
                self.assertIn('stroke="currentColor"', svg)
                self.assertIn('aria-hidden="true"', svg)
        for used in ("ICONS.panel", "ICONS.gear", "ICONS.threads", "ICONS['chevron-left']", "ICONS.external"):
            self.assertIn(used, runtime)

    def test_the_runtime_holds_the_set_status_marks(self):
        """review-index #index-marks: the set's five status marks, each one size and stroke weight, drawn only in the set's
        colors; the review index reads them from the runtime (review-serve-multi.py)."""
        marks = SYNC.held((ROOT / RUNTIME).read_text(), RUNTIME, SYNC.MARKS)
        self.assertEqual(set(marks), {"marks/%s.svg" % name for name in ("attention", "running", "pass", "neutral", "fail")})
        for path, svg in marks.items():
            with self.subTest(mark=path):
                self.assertRegex(svg, r'^<svg [^>]*width="16" height="16" viewBox="0 0 16 16"')
                self.assertIn('stroke-width="1.75"', svg)
                self.assertIn('aria-hidden="true"', svg)
                self.assertEqual(set(re.findall(r'(?:stroke|fill)="(var\([^"]*\))"', svg)) - {
                    "var(--ui-%s, currentColor)" % token for token in ("attention", "action", "pass", "muted", "fail")
                } - {"var(--ui-page, Canvas)"}, set())

    def test_the_runtime_holds_the_set_find_field_and_the_index_none_of_its_own(self):
        """review-index #acceptance-index-find-shared, #find-seam: the runtime holds the set's Find field, its markup, its
        style, and its filter script, from the pinned commit, and the review index builds them in from there
        (review-serve-multi.py serves them verbatim); review-serve.py holds no Find markup, style, or script of its own,
        no field and no selector over what it marks, only the marks."""
        runtime = (ROOT / RUNTIME).read_text()
        self.assertEqual(SYNC.region(runtime, RUNTIME, SYNC.FIND)[2], SYNC.pin()["commit"])
        find = SYNC.held(runtime, RUNTIME, SYNC.FIND)
        self.assertEqual(set(find), {"find/find.html", "find/find.css", "find/find.js"})
        self.assertIn('<p class="ui-find"><label for="ui-find">Find</label>', find["find/find.html"])
        serve = (ROOT / "skill/review-spec/assets/review-serve.py").read_text()
        self.assertNotIn("ui-find", serve)
        self.assertNotIn("[data-find", serve)
        self.assertEqual(_serve._design_set(runtime)[3], {Path(path).name: text for path, text in find.items()})

    def test_a_map_takes_only_its_directory_own_files(self):
        """A map region holds only the regular files of its set directory with its suffix: a subtree, link, or
        submodule under it is no file of the set, so its git show (a tree listing, a link target) never reaches a map."""
        with tempfile.TemporaryDirectory() as tmp:
            clone = Path(tmp)
            files = {"tokens.css": ":root {}\n", "icons/panel.svg": "<svg/>\n", "icons/notes.txt": "no icon\n",
                     "icons/sub/deep.svg": "<svg/>\n", "marks/pass.svg": "<svg/>\n", "find/find.js": "find();\n",
                     "find/sub/extra.js": "extra();\n"}
            commit = commit_set(clone, files, links={"find/link.js": "find.js"}, modules=("find/module",))
            self.assertEqual(SYNC.set_files(clone, commit), {path: files[path] for path in (
                "tokens.css", "icons/panel.svg", "marks/pass.svg", "find/find.js")})

    def test_a_set_with_crs_and_non_ascii_text_syncs_stably_in_any_locale(self):
        """git() reads the set as UTF-8 text, git's text mode newlines, so a CRLF or a lone CR in tokens.css or a find/
        file reaches no copy, and its non-ASCII text reaches the runtime as the set writes it, also in a locale that is not
        UTF-8: a sync writes the set's text with LF only, design-set-sync --check is then clean, and a second sync
        rebuilds the same text and writes nothing."""
        runtime = (ROOT / RUNTIME).read_text()
        start, end, _ = SYNC.region(runtime, RUNTIME)
        block = runtime[start:end]
        files = {"tokens.css": block[block.index("*/\n") + 3:-len(SYNC.END)] + "\n/* caf\u00e9 \u2026 \u2713 */\n"}
        for name in SYNC.MAPS:
            files.update(SYNC.held(runtime, RUNTIME, name))
        files["find/find.js"] += "// caf\u00e9 \u2026 \u2713\n"

        def crs(text):
            """The text with its first newline a lone CR and every other a CRLF."""
            return text.replace("\n", "\r", 1).replace("\n", "\r\n")

        raw = {**files, "tokens.css": crs(files["tokens.css"]), "find/find.js": crs(files["find/find.js"])}
        self.assertIn("\r\n", raw["find/find.js"])
        with tempfile.TemporaryDirectory() as tmp:
            clone, root = Path(tmp) / "set", Path(tmp) / "root"
            commit = commit_set(clone, raw)
            for path in ("scripts/design-set-sync", *SYNC.COPIES):
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / path, root / path)
            (root / "design-set.toml").write_text(f'repository = "https://example.invalid/set.git"\ncommit = "{commit}"\n')

            ascii_locale = {**os.environ, "LC_ALL": "C", "PYTHONUTF8": "0"}

            def sync(*args):
                return subprocess.run([sys.executable, str(root / "scripts/design-set-sync"), *args, str(clone)],
                                      capture_output=True, text=True, env=ascii_locale)

            first = sync()
            self.assertEqual(first.returncode, 0, first.stderr)
            synced = {path: (root / path).read_bytes() for path in SYNC.COPIES}
            check = sync("--check")
            self.assertEqual((check.returncode, check.stderr), (0, ""))
            second = sync()
            self.assertEqual((second.returncode, second.stdout), (0, ""))
            self.assertEqual({path: (root / path).read_bytes() for path in SYNC.COPIES}, synced)
        for path, data in synced.items():
            with self.subTest(path=path):
                self.assertNotIn(b"\r", data)
        built = synced[RUNTIME].decode()
        self.assertEqual(SYNC.held(built, RUNTIME, SYNC.FIND)["find/find.js"], files["find/find.js"])
        start, end, _ = SYNC.region(built, RUNTIME)
        self.assertEqual(built[start:end], SYNC.runtime_block(commit, files))

    def test_each_map_holds_its_files_as_its_row_says(self):
        """MAPS names how each map holds its files, not the file's name: an icon or mark on one line, a Find field file
        verbatim, an SVG among them too."""
        svg = '<svg width="16">\n  <path d="M0 0"/>\n</svg>\n'
        files = {"icons/panel.svg": svg, "marks/pass.svg": svg, "find/find.svg": svg, "find/find.js": "find();\n"}
        commit = SYNC.pin()["commit"]
        held = {name: SYNC.held(SYNC.map_block(name)(commit, files), RUNTIME, name) for name in SYNC.MAPS}
        self.assertEqual(held, {SYNC.ICONS: {"icons/panel.svg": '<svg width="16"><path d="M0 0"/></svg>'},
                                SYNC.MARKS: {"marks/pass.svg": '<svg width="16"><path d="M0 0"/></svg>'},
                                SYNC.FIND: {"find/find.js": "find();\n", "find/find.svg": svg}})

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
