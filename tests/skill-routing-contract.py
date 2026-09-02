import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / "skill" / "review-spec" / "SKILL.md"
SHAPE = ROOT / "skill" / "shape-spec" / "SKILL.md"


def frontmatter_description(text):
    frontmatter = text.split("---", 2)[1]
    return next(
        line.split(":", 1)[1].strip().strip('"')
        for line in frontmatter.splitlines()
        if line.startswith("description:")
    )


class SkillRoutingContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.review = REVIEW.read_text()
        cls.shape = SHAPE.read_text()

    def test_discovery_routes_material_existing_spec_edits_to_shape(self):
        review_description = frontmatter_description(self.review)
        shape_description = frontmatter_description(self.shape)

        self.assertIn("atomic corrections", review_description)
        self.assertIn("spec-chat-shape", review_description)
        self.assertIn("materially restructuring an existing spec", shape_description)
        self.assertIn("materially changes behavior or information architecture", shape_description)

    def test_review_gate_runs_before_material_file_edits(self):
        gate = self.review.index("## Material edit gate")
        self.assertGreater(gate, self.review.index("Classify before editing"))
        self.assertIn("required co-skill before the first file edit", self.review[gate:])
        self.assertIn("leave the batch durable and stop before editing", self.review[gate:])

    def test_shape_gate_has_current_quality_references_and_no_grandfathering(self):
        authoring = self.shape[self.shape.index("## Author the spec") :]
        self.assertIn("references/information-shape.md", authoring)
        self.assertIn("references/visual-quality.md", authoring)
        self.assertIn("An existing spec is not grandfathered", authoring)
        self.assertIn("desktop and mobile widths in normal and Git-focus modes", authoring)


if __name__ == "__main__":
    unittest.main()
