"""Leaf filter: exclude non-claims from corpus/cross-lane pairing (spec #leaf-exclusions, #proof-leaf)."""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skill", "review-spec", "assets"))
import jev

# Minimal spec with one real claim and one of each excluded category.
_SPEC = """\
<html><body>
<section data-anchor="root">
  <h2 data-anchor="heading-a">Heading anchor</h2>
  <figure data-anchor="fig-a"><figcaption data-anchor="figcap-a">Caption inside figure</figcaption></figure>
  <p data-anchor="inside-figure-child">Inside figure child text</p>
  <pre data-anchor="example-pre">Example pre block</pre>
  <code data-anchor="code-a">Code block</code>
  <p data-anchor="mock-preview">Mock preview text</p>
  <p data-anchor="my-example-item">Example id match</p>
  <caption data-anchor="cap-a">Table caption</caption>
  <section data-anchor="audit-section"><p data-anchor="audit-child">Audit child text</p></section>
  <section data-anchor="history-section"><p data-anchor="history-child">History child text</p></section>
  <p data-anchor="real-claim">The system shall do X when Y happens.</p>
</section>
</body></html>
"""

# Spec with a figure wrapping an anchor (figure ancestor, not figure tag).
_SPEC_FIGURE_ANCESTOR = """\
<html><body>
<section data-anchor="root">
  <figure><p data-anchor="inside-fig">Text inside figure element</p></figure>
  <p data-anchor="outside-fig">Text outside figure element</p>
</section>
</body></html>
"""


class TestLeafExclusions(unittest.TestCase):
    """Each exclusion category is flagged and excluded from corpus leaves."""

    def test_heading_excluded(self):
        anchors = jev.extract_anchors(_SPEC)
        self.assertTrue(anchors["heading-a"]["excluded"])

    def test_figure_tag_excluded(self):
        anchors = jev.extract_anchors(_SPEC)
        self.assertTrue(anchors["fig-a"]["excluded"])

    def test_figcaption_tag_excluded(self):
        anchors = jev.extract_anchors(_SPEC)
        self.assertTrue(anchors["figcap-a"]["excluded"])

    def test_figure_ancestor_excluded(self):
        anchors = jev.extract_anchors(_SPEC_FIGURE_ANCESTOR)
        self.assertTrue(anchors["inside-fig"]["excluded"])
        self.assertFalse(anchors["outside-fig"]["excluded"])

    def test_pre_excluded(self):
        anchors = jev.extract_anchors(_SPEC)
        self.assertTrue(anchors["example-pre"]["excluded"])

    def test_code_excluded(self):
        anchors = jev.extract_anchors(_SPEC)
        self.assertTrue(anchors["code-a"]["excluded"])

    def test_example_id_excluded(self):
        anchors = jev.extract_anchors(_SPEC)
        self.assertTrue(anchors["my-example-item"]["excluded"])

    def test_mock_preview_id_excluded(self):
        anchors = jev.extract_anchors(_SPEC)
        self.assertTrue(anchors["mock-preview"]["excluded"])

    def test_caption_excluded(self):
        anchors = jev.extract_anchors(_SPEC)
        self.assertTrue(anchors["cap-a"]["excluded"])

    def test_audit_child_excluded(self):
        anchors = jev.extract_anchors(_SPEC)
        self.assertTrue(anchors["audit-child"]["excluded"])

    def test_history_child_excluded(self):
        anchors = jev.extract_anchors(_SPEC)
        self.assertTrue(anchors["history-child"]["excluded"])

    def test_audit_section_self_excluded(self):
        anchors = jev.extract_anchors(_SPEC)
        self.assertTrue(anchors["audit-section"]["excluded"])

    def test_real_claim_not_excluded(self):
        anchors = jev.extract_anchors(_SPEC)
        self.assertFalse(anchors["real-claim"]["excluded"])

    def test_corpus_leaf_anchors_excludes_all(self):
        """_corpus_leaf_anchors returns only the real claim."""
        anchors = jev.extract_anchors(_SPEC)
        leaves = jev._corpus_leaf_anchors(anchors)
        self.assertIn("real-claim", leaves)
        excluded = {"heading-a", "fig-a", "figcap-a", "example-pre", "code-a",
                     "mock-preview", "my-example-item", "cap-a", "audit-child", "history-child"}
        for name in excluded:
            self.assertNotIn(name, leaves, f"{name} should be excluded from leaves")


class TestProofLeaf(unittest.TestCase):
    """Spec #proof-leaf: build_corpus_questions returns no question for excluded anchors."""

    def test_no_questions_for_excluded(self):
        baseline = "<html><body><section data-anchor='root'><p data-anchor='real-claim'>Old claim</p></section></body></html>"
        questions = jev.build_corpus_questions(_SPEC, baseline)
        involved = set()
        for q in questions:
            if "chain" in q:
                for step in q["chain"]:
                    involved.add(step.get("id"))
                    for s in step.get("sources", []):
                        if "#" in s:
                            involved.add(s.split("#", 1)[1])
                        else:
                            involved.add(s)
            else:
                involved.add(q.get("id"))
                for s in q.get("sources", []):
                    if "#" in s:
                        involved.add(s.split("#", 1)[1])
                    else:
                        involved.add(s)
        excluded_ids = {"heading-a", "fig-a", "figcap-a", "example-pre", "code-a",
                        "mock-preview", "my-example-item", "cap-a", "audit-child", "history-child"}
        for name in excluded_ids:
            self.assertNotIn(name, involved, f"{name} should not appear in any question")


class TestChangedLeafClauses(unittest.TestCase):
    """changed_leaf_clauses excludes non-claims."""

    def test_excluded_anchors_not_in_changed(self):
        baseline = """\
<html><body><section data-anchor="root">
  <h2 data-anchor="heading-a">Old heading</h2>
  <p data-anchor="real-claim">Old claim</p>
</section></body></html>"""
        changed = jev.changed_leaf_clauses(_SPEC, baseline)
        anchors = [c[0] for c in changed]
        self.assertIn("real-claim", anchors)
        self.assertNotIn("heading-a", anchors)


if __name__ == "__main__":
    unittest.main()
