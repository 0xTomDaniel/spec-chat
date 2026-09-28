"""Context fields on every question (jev-suggestions #context, #acceptance-context)."""

import importlib.util
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("jev_context_test", ROOT / "tools" / "jev.py")
jev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jev)


# A spec with h1 title, sections with headings, and a table with columns.
SPEC_WITH_CONTEXT = (
    '<header data-anchor="header"><h1 data-anchor="title">My Spec Title</h1>'
    '<p data-anchor="ctx">Context paragraph.</p></header>'
    '<section data-anchor="sec-a" data-spec-section="rules"><h2>Rules Section</h2>'
    '<p data-anchor="clause-a">First clause text.</p>'
    '<section data-anchor="sec-nested"><h3>Nested Heading</h3>'
    '<p data-anchor="clause-nested">Nested clause text.</p></section>'
    '<div class="table-scroll" data-anchor="tbl-scroll">'
    '<table data-anchor="tbl"><thead><tr><th>Name</th><th>Value</th><th>When</th></tr></thead>'
    '<tbody>'
    '<tr data-anchor="row-1"><td>alpha</td><td>one</td><td>always</td></tr>'
    '<tr data-anchor="row-2"><td>beta</td><td>two</td><td>sometimes</td></tr>'
    '</tbody></table></div>'
    '</section>'
    '<section data-anchor="sec-b"><h2>Other Section</h2>'
    '<p data-anchor="clause-b">Second clause text.</p></section>')

SPEC_NO_TITLE = (
    '<section data-anchor="sec"><h2>Only Section</h2>'
    '<p data-anchor="clause">Some text.</p></section>')


class ExtractContextTest(unittest.TestCase):
    """extract_anchors returns title, heading, and columns per anchor."""

    def test_title_on_every_anchor(self):
        anchors = jev.extract_anchors(SPEC_WITH_CONTEXT)
        for key in anchors:
            self.assertEqual(anchors[key]["title"], "My Spec Title", f"anchor {key}")

    def test_no_title_when_absent(self):
        anchors = jev.extract_anchors(SPEC_NO_TITLE)
        for key in anchors:
            self.assertEqual(anchors[key]["title"], "", f"anchor {key}")

    def test_section_heading_on_section_anchor(self):
        anchors = jev.extract_anchors(SPEC_WITH_CONTEXT)
        self.assertEqual(anchors["sec-a"]["heading"], "Rules Section")
        self.assertEqual(anchors["sec-nested"]["heading"], "Nested Heading")
        self.assertEqual(anchors["sec-b"]["heading"], "Other Section")

    def test_no_heading_on_non_section(self):
        anchors = jev.extract_anchors(SPEC_WITH_CONTEXT)
        self.assertNotIn("heading", anchors["clause-a"])

    def test_table_columns(self):
        anchors = jev.extract_anchors(SPEC_WITH_CONTEXT)
        self.assertEqual(anchors["tbl"]["columns"], ["Name", "Value", "When"])

    def test_no_columns_on_non_table(self):
        anchors = jev.extract_anchors(SPEC_WITH_CONTEXT)
        self.assertNotIn("columns", anchors["clause-a"])


class AnchorContextTest(unittest.TestCase):
    """anchor_context builds the context dict for a given anchor."""

    def setUp(self):
        self.anchors = jev.extract_anchors(SPEC_WITH_CONTEXT)

    def test_surface_always_present(self):
        ctx = jev.anchor_context("clause-a", self.anchors)
        self.assertEqual(ctx["surface"], "My Spec Title")

    def test_section_from_ancestor(self):
        ctx = jev.anchor_context("clause-a", self.anchors)
        self.assertEqual(ctx["section"], "Rules Section")

    def test_nested_section_uses_nearest(self):
        ctx = jev.anchor_context("clause-nested", self.anchors)
        self.assertEqual(ctx["section"], "Nested Heading")

    def test_no_section_when_no_ancestor_section(self):
        anchors = jev.extract_anchors(SPEC_NO_TITLE)
        ctx = jev.anchor_context("clause", anchors)
        # The section "sec" has heading "Only Section", and clause's parent is sec
        self.assertEqual(ctx["section"], "Only Section")

    def test_columns_for_table_row(self):
        ctx = jev.anchor_context("row-1", self.anchors)
        self.assertEqual(ctx["columns"], ["Name", "Value", "When"])

    def test_columns_for_second_row(self):
        ctx = jev.anchor_context("row-2", self.anchors)
        self.assertEqual(ctx["columns"], ["Name", "Value", "When"])

    def test_no_columns_for_non_table_clause(self):
        ctx = jev.anchor_context("clause-a", self.anchors)
        self.assertNotIn("columns", ctx)

    def test_no_surface_when_no_title(self):
        anchors = jev.extract_anchors(SPEC_NO_TITLE)
        ctx = jev.anchor_context("clause", anchors)
        self.assertNotIn("surface", ctx)

    def test_empty_context_for_unknown_anchor(self):
        ctx = jev.anchor_context("nonexistent", self.anchors)
        self.assertEqual(ctx, {})


class CorpusContextTest(unittest.TestCase):
    """build_corpus_questions includes context in state."""

    def test_corpus_question_state_has_context(self):
        baseline = (
            '<header data-anchor="header"><h1 data-anchor="title">Spec</h1></header>'
            '<section data-anchor="sec" data-spec-section="rules"><h2>Rules</h2>'
            '<p data-anchor="clause-x">Old text.</p>'
            '<p data-anchor="clause-y">Candidate text.</p></section>')
        current = (
            '<header data-anchor="header"><h1 data-anchor="title">Spec</h1></header>'
            '<section data-anchor="sec" data-spec-section="rules"><h2>Rules</h2>'
            '<p data-anchor="clause-x">New changed text.</p>'
            '<p data-anchor="clause-y">Candidate text.</p></section>')
        questions = jev.build_corpus_questions(current, baseline)
        self.assertTrue(len(questions) > 0, "expected at least one corpus question")
        for q in questions:
            # Gate step (about) carries context inside first/second (#gate-set-state)
            gate = q["chain"][0]
            self.assertEqual(gate["kind"], "about")
            self.assertEqual(gate["state"]["first"]["surface"], "Spec")
            self.assertEqual(gate["state"]["second"]["surface"], "Spec")
            # Chain steps carry context/target_context at top level
            for step in q["chain"][1:]:
                state = step["state"]
                self.assertIn("context", state, f"step {step['kind']} missing context")
                self.assertEqual(state["context"]["surface"], "Spec")
                self.assertEqual(state["context"]["section"], "Rules")
                self.assertIn("target_context", state, f"step {step['kind']} missing target_context")

    def test_corpus_table_row_has_columns_in_context(self):
        baseline = (
            '<header data-anchor="header"><h1 data-anchor="title">T</h1></header>'
            '<section data-anchor="sec"><h2>S</h2>'
            '<table data-anchor="tbl"><thead><tr><th>A</th><th>B</th></tr></thead>'
            '<tbody><tr data-anchor="r1"><td>old</td><td>val</td></tr>'
            '<tr data-anchor="r2"><td>other</td><td>candidate</td></tr></tbody></table></section>')
        current = (
            '<header data-anchor="header"><h1 data-anchor="title">T</h1></header>'
            '<section data-anchor="sec"><h2>S</h2>'
            '<table data-anchor="tbl"><thead><tr><th>A</th><th>B</th></tr></thead>'
            '<tbody><tr data-anchor="r1"><td>new changed</td><td>val</td></tr>'
            '<tr data-anchor="r2"><td>other</td><td>candidate</td></tr></tbody></table></section>')
        questions = jev.build_corpus_questions(current, baseline)
        self.assertTrue(len(questions) > 0)
        # Gate step (about) carries columns inside first (#gate-set-state)
        gate = questions[0]["chain"][0]
        self.assertEqual(gate["kind"], "about")
        self.assertIn("columns", gate["state"]["first"])
        # Chain step (contradicts) carries columns at top level
        step = questions[0]["chain"][1]
        self.assertEqual(step["state"]["context"]["columns"], ["A", "B"])


class TypeContextTest(unittest.TestCase):
    """build_type_questions includes context in state."""

    def test_type_question_state_has_context(self):
        baseline = (
            '<header data-anchor="header"><h1 data-anchor="title">Title</h1></header>'
            '<section data-anchor="sec" data-spec-section="rules"><h2>Heading</h2>'
            '<p data-anchor="c">Old.</p></section>')
        current = (
            '<header data-anchor="header"><h1 data-anchor="title">Title</h1></header>'
            '<section data-anchor="sec" data-spec-section="rules"><h2>Heading</h2>'
            '<p data-anchor="c">New.</p></section>')
        questions = jev.build_type_questions(current, baseline)
        self.assertTrue(len(questions) > 0)
        state = questions[0]["chain"][0]["state"]
        self.assertIn("context", state)
        self.assertEqual(state["context"]["surface"], "Title")
        self.assertEqual(state["context"]["section"], "Heading")


class CacheKeyContextTest(unittest.TestCase):
    """Context in state changes the cache key (#context-position)."""

    def test_different_context_different_key(self):
        seam = jev.JevSeam()
        state_a = {"after": "text", "context": {"surface": "Spec A", "section": "S"}}
        state_b = {"after": "text", "context": {"surface": "Spec B", "section": "S"}}
        q_a = {"kind": "type", "state": state_a}
        q_b = {"kind": "type", "state": state_b}
        self.assertNotEqual(seam.key(q_a), seam.key(q_b))

    def test_same_context_same_key(self):
        seam = jev.JevSeam()
        state = {"after": "text", "context": {"surface": "X"}}
        q1 = {"kind": "type", "state": dict(state)}
        q2 = {"kind": "type", "state": dict(state)}
        self.assertEqual(seam.key(q1), seam.key(q2))


class LaneContextTest(unittest.TestCase):
    """lane_check includes context from clause dicts."""

    def test_lane_check_passes_context(self):
        a = {"path": "a.spec.html", "anchor": "c1", "before": "old", "after": "new a", "base": "abc",
             "context": {"surface": "Spec A", "section": "S1"}}
        b = {"path": "b.spec.html", "anchor": "c2", "before": "old", "after": "new b", "base": "def",
             "context": {"surface": "Spec B", "section": "S2"}}
        item = jev.lane_check("left", a, "right", b)
        # gate step (index 0): about? with first/second context
        self.assertEqual(item["chain"][0]["kind"], "about")
        # contradicts step (index 1): has context for a and target_context for b
        step1 = item["chain"][1]
        self.assertEqual(step1["state"]["context"]["surface"], "Spec A")
        self.assertEqual(step1["state"]["target_context"]["surface"], "Spec B")
        # back oversteps step (index 3): context for b, target_context for a
        step3 = item["chain"][3]
        self.assertEqual(step3["state"]["context"]["surface"], "Spec B")
        self.assertEqual(step3["state"]["target_context"]["surface"], "Spec A")


if __name__ == "__main__":
    unittest.main()
