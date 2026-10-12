"""Spec #proof-top-k: build_corpus_questions returns at most 5 cross-spec candidates per changed clause
and none for a clause of its own spec, non-goal or not."""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skill", "review-spec", "assets"))
import jev


def _clause(anchor, text):
    """HTML paragraph with data-anchor."""
    return f'<p data-anchor="{anchor}">{text}</p>'


def _spec(clauses, non_goal_clauses=None):
    """Wrap clauses in a minimal spec with separate non-goal section."""
    body = '<section data-anchor="clauses">\n' + "\n".join(clauses) + "\n</section>\n"
    if non_goal_clauses:
        body += '<section data-anchor="non-goals">\n' + "\n".join(non_goal_clauses) + "\n</section>\n"
    return f"<html><body>{body}</body></html>"


class TestTopK(unittest.TestCase):

    def test_at_most_5_cross_spec_and_no_own_spec_candidate(self):
        """With a changed clause, 20 own-spec clauses and own non-goals, and 20 cross-spec candidates,
        build_corpus_questions returns questions for at most 5 cross-spec candidates and none for a
        clause of its own spec, non-goal or not."""
        changed = _clause("changed", "The zephyr routing module handles authentication tokens")
        same_clauses = [_clause(f"same-{i}", f"Candidate {i} uses the zephyr routing module for tokens")
                        for i in range(20)]
        non_goal_clauses = [
            _clause("non-goal-a", "Non-goal alpha: zephyr routing never handles authentication tokens"),
            _clause("non-goal-b", "Non-goal beta: unrelated concept about gardening"),
        ]
        baseline = _spec([_clause("changed", "Old text before change")] + same_clauses, non_goal_clauses)
        current = _spec([changed] + same_clauses, non_goal_clauses)
        cross_clauses = [_clause(f"cross-{i}", f"Cross candidate {i} with routing and tokens") for i in range(20)]
        other = _spec(cross_clauses)

        questions = jev.build_corpus_questions(current, baseline, "spec.html", "base", "head",
                                               [{"path": "other.spec.html", "source": other}])
        targets = [q["target"] for q in questions if q.get("id") == "changed"]

        self.assertEqual(len(targets), 5)
        self.assertEqual(jev.TOPK_CROSS, 5)
        self.assertTrue(all(t.startswith("other.spec.html#cross-") for t in targets), targets)
        # No own-spec candidate at all, and none without other specs served.
        self.assertEqual(jev.build_corpus_questions(current, baseline, "spec.html", "base", "head"), [])

    def test_rarity_weighted_scoring(self):
        """Rare shared words rank higher than common shared words."""
        # "zephyr" appears in only 1 clause (rare), "routing" in many (common).
        texts = [
            "zephyr authentication token",
            "routing module processing data",
            "routing module configuration data",
            "routing module validation data",
            "routing module deployment data",
        ]
        freqs = jev._doc_freqs(texts)
        source_words = jev._anchor_words("zephyr routing token")
        # Candidate sharing "zephyr" (rare) should score higher than one sharing only "routing" (common).
        zephyr_score = jev._rarity_score(source_words, jev._anchor_words("zephyr authentication token"), freqs)
        routing_score = jev._rarity_score(source_words, jev._anchor_words("routing module processing data"), freqs)
        self.assertGreater(zephyr_score, routing_score,
                           "Rare word match should score higher than common word match")


if __name__ == "__main__":
    unittest.main()
