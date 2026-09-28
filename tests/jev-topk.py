"""Spec #proof-top-k: build_corpus_questions returns at most 5 same-spec and 5 cross-spec
candidates per changed clause, always including non-goals regardless of rank."""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import jev


def _clause(anchor, text, non_goal=False):
    """HTML paragraph with data-anchor."""
    parent = "non-goals" if non_goal else "clauses"
    return f'<p data-anchor="{anchor}">{text}</p>'


def _spec(clauses, non_goal_clauses=None):
    """Wrap clauses in a minimal spec with separate non-goal section."""
    body = '<section data-anchor="clauses">\n' + "\n".join(clauses) + "\n</section>\n"
    if non_goal_clauses:
        body += '<section data-anchor="non-goals">\n' + "\n".join(non_goal_clauses) + "\n</section>\n"
    return f"<html><body>{body}</body></html>"


def _other_spec(clauses):
    return f"<html><body><section data-anchor='other-root'>\n" + "\n".join(clauses) + "\n</section></body></html>"


class TestTopK(unittest.TestCase):

    def test_at_most_5_same_spec_and_5_cross_spec_plus_non_goals(self):
        """With a changed clause and 20 same-spec + 20 cross-spec candidates,
        build_corpus_questions returns questions for at most 5 same-spec and
        5 cross-spec candidates, always including non-goals."""
        # The changed clause uses a unique rare word "zephyr" shared with some candidates.
        changed = _clause("changed", "The zephyr routing module handles authentication tokens")

        # 20 same-spec candidates, each with some overlapping words.
        same_clauses = []
        for i in range(20):
            same_clauses.append(_clause(f"same-{i}", f"Candidate {i} uses the routing module for processing"))

        # 2 non-goal clauses ranked outside top 5 (no overlapping words beyond trivial).
        non_goal_clauses = [
            f'<p data-anchor="non-goal-a">Non-goal alpha: unrelated concept about painting</p>',
            f'<p data-anchor="non-goal-b">Non-goal beta: unrelated concept about gardening</p>',
        ]

        baseline_clauses = [_clause("changed", "Old text before change")]
        baseline = _spec(baseline_clauses + same_clauses, non_goal_clauses)
        current = _spec([changed] + same_clauses, non_goal_clauses)

        # 20 cross-spec candidates.
        cross_clauses = []
        for i in range(20):
            cross_clauses.append(_clause(f"cross-{i}", f"Cross candidate {i} with routing and tokens"))
        other = _other_spec(cross_clauses)

        questions = jev.build_corpus_questions(current, baseline, "spec.html", "base", "head",
                                               [{"path": "other.spec.html", "source": other}])

        # Collect targets per changed clause.
        same_targets = []
        cross_targets = []
        for q in questions:
            if q.get("id") != "changed":
                continue
            target = q["target"]
            if "#" in target:
                cross_targets.append(target)
            else:
                same_targets.append(target)

        # Separate non-goals from regular same-spec.
        same_non_goals = [t for t in same_targets if "non-goal" in t]
        same_regular = [t for t in same_targets if "non-goal" not in t]

        # At most 5 regular same-spec candidates.
        self.assertLessEqual(len(same_regular), 5,
                             f"Expected at most 5 same-spec candidates, got {len(same_regular)}")
        # At most 5 cross-spec candidates.
        self.assertLessEqual(len(cross_targets), 5,
                             f"Expected at most 5 cross-spec candidates, got {len(cross_targets)}")
        # Both non-goals included.
        self.assertEqual(len(same_non_goals), 2,
                         f"Expected 2 non-goals, got {len(same_non_goals)}: {same_non_goals}")
        # Total same-spec is at most 5 regular + all non-goals.
        self.assertLessEqual(len(same_regular), jev.TOPK_SAME)
        self.assertLessEqual(len(cross_targets), jev.TOPK_CROSS)

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
