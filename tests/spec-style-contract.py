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


class SpecStyleContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = STYLE.read_text()
        cls.colors = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", cls.css))

    def test_normal_and_muted_text_clear_the_body_contrast_floor(self):
        for foreground in ("spec-ink", "spec-body", "spec-muted", "spec-focus-context"):
            with self.subTest(foreground=foreground):
                self.assertGreaterEqual(
                    contrast(self.colors[foreground], self.colors["spec-paper"]), 4.5
                )
                self.assertGreaterEqual(
                    contrast(self.colors[foreground], self.colors["spec-surface"]), 4.5
                )

    def test_semantic_accents_clear_the_text_contrast_floor(self):
        for foreground in ("spec-teal", "spec-blue", "spec-amber", "spec-red"):
            with self.subTest(foreground=foreground):
                self.assertGreaterEqual(
                    contrast(self.colors[foreground], self.colors["spec-surface"]), 4.5
                )

    def test_default_language_avoids_decorative_wash(self):
        self.assertNotIn("linear-gradient", self.css)
        self.assertNotIn("backdrop-filter", self.css)


if __name__ == "__main__":
    unittest.main()
