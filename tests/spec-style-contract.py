import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STYLE = ROOT / "skill" / "shape-spec" / "assets" / "style" / "spec.css"


def channel(value):
    value /= 255
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def luminance(color):
    red, green, blue = (int(color[index : index + 2], 16) for index in (1, 3, 5))
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def contrast(first, second):
    bright, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (bright + 0.05) / (dark + 0.05)


def declared(body):
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", body))


class SpecStyleContractTest(unittest.TestCase):
    """The bundled shared spec stylesheet (spec-page-design #document): every --spec-* value from the design set, in
    light and dark; tests/design-set.py proves the values are the pinned set's."""

    @classmethod
    def setUpClass(cls):
        cls.css = STYLE.read_text()
        region = re.search(r"/\* design-set [0-9a-f]{40} begin\b.*?\*/\n(.*?)/\* design-set end \*/\n", cls.css, re.S)
        cls.region = region.group(1)
        cls.rules = cls.css[region.end():]
        cls.light = declared(re.search(r"^:root \{(.*?)\n\}", cls.region, re.S | re.M).group(1))
        dark = re.search(r"@media \(prefers-color-scheme: dark\) \{\s*:root \{(.*?)\}", cls.region, re.S)
        cls.modes = {"light": cls.light, "dark": {**cls.light, **declared(dark.group(1))}}

    def test_normal_and_muted_text_clear_the_body_contrast_floor_in_both_modes(self):
        for mode, colors in self.modes.items():
            for foreground in ("--spec-ink", "--spec-body", "--spec-muted", "--spec-focus-context"):
                for ground in ("--spec-paper", "--spec-surface", "--spec-soft", "--spec-code"):
                    with self.subTest(mode=mode, foreground=foreground, ground=ground):
                        self.assertGreaterEqual(contrast(colors[foreground], colors[ground]), 4.5)

    def test_semantic_accents_clear_the_text_contrast_floor_in_both_modes(self):
        for mode, colors in self.modes.items():
            for foreground in ("--spec-teal", "--spec-blue", "--spec-amber", "--spec-red"):
                for ground in ("--spec-paper", "--spec-surface", "--spec-soft"):
                    with self.subTest(mode=mode, foreground=foreground, ground=ground):
                        self.assertGreaterEqual(contrast(colors[foreground], colors[ground]), 4.5)

    def test_dark_appearance_renders_a_dark_page_with_light_text(self):
        """spec-page-design #acceptance-dark-document, repair-specs #acceptance-presentation-theme-dark: the stylesheet
        follows the system appearance, and every color the set changes in dark changes here."""
        self.assertRegex(self.rules, r":root \{\s*color-scheme: light dark;")
        dark = self.modes["dark"]
        self.assertLess(luminance(dark["--spec-paper"]), luminance(dark["--spec-ink"]))
        self.assertGreater(luminance(self.light["--spec-paper"]), luminance(self.light["--spec-ink"]))
        colors = [name for name, value in self.light.items() if value.startswith("#")]
        self.assertTrue(all(dark[name] != self.light[name] for name in colors), "every color has its dark value")

    def test_rules_use_only_the_document_values(self):
        """#document: outside the set's region no rule paints a literal color."""
        self.assertNotRegex(self.rules, r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(")
        used = set(re.findall(r"var\((--spec-[\w-]+)", self.rules))
        own = set(declared(re.search(r":root \{(.*?)\}", self.rules, re.S).group(1)))
        self.assertLessEqual(used, set(self.light) | own)

    def test_the_header_is_unboxed_with_the_title_at_the_display_step(self):
        """#document, #acceptance-one-surface: no box or rule around the header; title at the display step."""
        header = re.search(r"article\.spec > header \{(.*?)\}", self.rules, re.S).group(1)
        self.assertIn("background: none;", header)
        self.assertIn("border: 0;", header)
        title = re.search(r"article\.spec > header h1 \{(.*?)\}", self.rules, re.S).group(1)
        self.assertIn("font-size: var(--spec-text-display);", title)
        self.assertRegex(self.rules, r"h2 \{\s*margin: 0;\s*font-size: var\(--spec-text-heading\);")
        self.assertRegex(self.rules, r"body \{[^}]*font-size: 15px;")
        # On a phone the title drops to the heading step (#one-surface-figure-caption).
        self.assertRegex(self.rules, r"@media \(max-width: \d+px\) \{[^@]*article\.spec > header h1 \{\s*font-size: var\(--spec-text-heading\);")

    def test_sections_tables_and_figures_use_hairlines_never_heavy_rules(self):
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", self.rules):
            for width in re.findall(r"border(?:-\w+)?:\s*(\d+(?:\.\d+)?)px solid", body):
                if float(width) > 1:
                    with self.subTest(selector=selector.strip()):
                        # Only the rail's dot and the phase row's divider cut out the paper, never a rule.
                        self.assertIn("var(--spec-paper)", body)
        self.assertRegex(self.rules, r"section \{[^}]*border-top: 1px solid var\(--spec-hairline\);")

    def test_default_language_avoids_decorative_wash(self):
        self.assertNotIn("linear-gradient", self.css)
        self.assertNotIn("backdrop-filter", self.css)

    def test_mobile_tables_scroll_inside_the_page(self):
        self.assertRegex(
            self.css,
            r"table\s*\{\s*display:\s*block;\s*max-width:\s*100%;\s*overflow-x:\s*auto;",
        )


if __name__ == "__main__":
    unittest.main()
