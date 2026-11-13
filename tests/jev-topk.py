"""Spec #proof-top-k: build_corpus_questions returns at most 5 cross-spec candidates per changed clause
and none for a clause of its own spec, non-goal or not. Cross-lane pairs are ranked the same way
(#candidate-ranking-lane): lane_shortlist."""
import math, os, random, sys, unittest
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


def lane_clauses(texts_by_slug):
    """Each slug's changed clauses, as the service reads them: {path, anchor, before, after, base}."""
    return {slug: [{"path": slug + "/docs/specs/x.spec.html", "anchor": "c%d" % i, "before": "Keep.", "after": text,
                    "base": "main"} for i, text in enumerate(texts)] for slug, texts in texts_by_slug.items()}


def top_texts(texts_by_slug, slug, index):
    """#candidate-ranking-lane by brute force: a changed clause's top 5 distinct texts among the other slugs' changed
    clauses, by rarity-weighted overlap with each word's rarity counted over the project's changed clauses, summed
    exactly rounded, ties by text, never a text reading the same."""
    freqs = jev._doc_freqs([text for texts in texts_by_slug.values() for text in texts])
    text = texts_by_slug[slug][index]

    def score(other):
        return math.fsum(jev._rarity_weight(word, freqs) for word in jev._anchor_words(text) & jev._anchor_words(other))

    others = sorted({u for other, more in texts_by_slug.items() if other != slug for u in more} - {text})
    return sorted(others, key=lambda u: (-score(u), u))[:jev.TOPK_CROSS]


def ranked_pairs(texts_by_slug):
    """Each changed clause paired with every clause of another slug carrying one of its top texts: pairs of
    (slug, index), a pair either side ranks once."""
    pairs = set()
    for slug, texts in texts_by_slug.items():
        for index in range(len(texts)):
            top = top_texts(texts_by_slug, slug, index)
            for other, more in texts_by_slug.items():
                for other_index, u in enumerate(more):
                    if other != slug and u in top:
                        pairs.add(frozenset({(slug, index), (other, other_index)}))
    return pairs


def shortlisted(shortlist):
    return {frozenset({(left, i), (right, j)}) for (left, right), indices in shortlist.items() for i, j in indices}


def random_lanes(lanes, per, seed=7, vocabulary=400, words=8):
    rng = random.Random(seed)
    pool = ["w%03d" % n for n in range(vocabulary)]
    return {"ann%02d" % lane: ["Lane rule " + " ".join(rng.sample(pool, words)) + "." for _ in range(per)]
            for lane in range(lanes)}


def random_project(seed, lanes=5, per=40, vocabulary=150):
    """Clauses whose words follow a Zipf law, as prose's do, with words most clauses carry; some texts in two slugs or
    twice in one, some sharing no word or holding none, and some reading differently with the same words, a true
    tie."""
    rng = random.Random(seed)
    pool = ["v%03d" % n for n in range(vocabulary)]
    zipf = [1 / (rank + 1) for rank in range(vocabulary)]
    common = ["owner", "spec", "marks", "page"]
    texts, seen = {}, []
    for lane in range(lanes):
        mine = texts["ann%02d" % lane] = []
        for index in range(per):
            roll = rng.random()
            if seen and roll < 0.06:
                mine.append(rng.choice(seen))
            elif roll < 0.09:
                mine.append("Lone%dx%d." % (lane, index))
            elif roll < 0.1:
                mine.append("Ok.")
            else:
                words = rng.choices(pool, zipf, k=rng.randint(2, 12)) + rng.sample(common, rng.randint(0, 4))
                mine.append(" ".join(words) + rng.choice(("", ".", "!")))
            seen.append(mine[-1])
    return texts


def lines_run(function, *args):
    """The lines of jev.py a call runs: its cost, counted the same on any machine."""
    count = [0]

    def line(frame, event, arg):
        count[0] += event == "line"
        return line

    running = sys.gettrace()
    sys.settrace(lambda frame, event, arg: line if frame.f_code.co_filename == jev.__file__ else None)
    try:
        function(*args)
    finally:
        sys.settrace(running)
    return count[0]


class TestLaneTopK(unittest.TestCase):
    """#candidate-ranking, #candidate-ranking-lane, #cross-lane-pairs, #feature-cross-lane, #acceptance-lane-topk,
    #acceptance-lane-same-text: cross-lane pairs ranked as draft checks rank candidates."""

    def test_each_clause_pairs_with_its_top_texts_either_side(self):
        """#acceptance-lane-topk: each changed clause is paired only with the other lane's changed clauses that rank
        highest for it and those for which it ranks highest, never with every changed clause of the other lane."""
        texts = random_lanes(2, 20)
        hub = " zephyr quasar nimbus"
        texts["ann00"][0] = texts["ann00"][0][:-1] + hub + "."
        for j in range(10):  # ten clauses of the other lane each rank the hub highest; the hub ranks only 5 of them
            texts["ann01"][j] = texts["ann01"][j][:-1] + hub + "."
        shortlist = jev.lane_shortlist(lane_clauses(texts))
        self.assertEqual(shortlisted(shortlist), ranked_pairs(texts))
        self.assertEqual(list(shortlist), [("ann00", "ann01")])
        hub_partners = {j for i, j in shortlist[("ann00", "ann01")] if i == 0}
        self.assertLessEqual(set(range(10)), hub_partners)  # those for which it ranks highest, beyond its own 5
        self.assertLessEqual(len(shortlisted(shortlist)), jev.TOPK_CROSS * 40)
        self.assertLess(len(shortlisted(shortlist)), 20 * 20)
        # The chains the builder asks are these pairs, the lower slug's clause first (#cross-lane-question).
        chains = jev.build_board_conflict_questions(lane_clauses(texts))
        self.assertEqual(len(chains), len(shortlisted(shortlist)))
        self.assertTrue(all(chain["sides"][0][0] == "ann00" for chain in chains))

    def test_shared_text_counts_once_and_is_never_paired_with_itself(self):
        """#acceptance-lane-same-text, #cross-lane-pairs: a text two slugs carry is one of a clause's top 5 texts and
        pairs with both clauses carrying it; the two clauses reading the same are never paired."""
        shared = "Owners reconcile zephyr quasar marks before hand-off."
        third = "Owners reconcile zephyr quasar marks before review."
        # The third lane's clause ranks the shared text first, then three close texts, then `links` fifth; `links`
        # ranks five kiln texts above it, so only counting the shared text once pairs the two.
        links = "Owners zephyr links " + " ".join("kiln%d glaze%d" % (k, k) for k in range(1, 6)) + "."
        kilns = ["Kiln%d glaze%d cools." % (k, k) for k in range(1, 6)]
        texts = {"ann1": [shared, "Owners reconcile zephyr quasar notes daily.", "Owners reconcile zephyr quasar rows."]
                 + kilns,
                 "ann2": [shared, "Owners reconcile zephyr quasar cards weekly.", links],
                 "ann3": [third]}
        shortlist = jev.lane_shortlist(lane_clauses(texts))
        pairs = shortlisted(shortlist)
        self.assertEqual(pairs, ranked_pairs(texts))
        self.assertEqual(top_texts(texts, "ann3", 0), [shared] + sorted(texts["ann1"][1:3] + texts["ann2"][1:2]) + [links])
        self.assertNotIn(third, top_texts(texts, "ann2", 2))
        partners = {next(iter(pair - {("ann3", 0)})) for pair in pairs if ("ann3", 0) in pair}
        self.assertLessEqual({("ann1", 0), ("ann2", 0), ("ann1", 1), ("ann1", 2), ("ann2", 1), ("ann2", 2)}, partners)
        self.assertNotIn(frozenset({("ann1", 0), ("ann2", 0)}), pairs)
        # A slug's own texts are the draft check's: never paired within a slug.
        self.assertTrue(all(left != right for left, right in shortlist))

    def test_pairs_grow_with_clauses_not_their_product(self):
        """#candidate-ranking-lane: the pairs grow with the changed clauses, never with every pair of them."""
        counts = {}
        for lanes, per in ((4, 25), (8, 25), (8, 50)):
            pairs = shortlisted(jev.lane_shortlist(lane_clauses(random_lanes(lanes, per))))
            product = sum(per * per for _ in range(lanes * (lanes - 1) // 2))
            self.assertLessEqual(len(pairs), jev.TOPK_CROSS * lanes * per)
            self.assertLess(len(pairs) * 4, product)
            counts[lanes, per] = len(pairs)
        # Twice the clauses, about twice the pairs: the product would be four times.
        self.assertLess(counts[8, 25], 3 * counts[4, 25])
        self.assertLess(counts[8, 50], 3 * counts[8, 25])

    def test_the_shortlist_is_the_ranking_a_score_per_pair_gives(self):
        """#candidate-ranking-lane: on random projects whose words follow a Zipf law, the shortlist pairs exactly what
        ranking every text against every other text pairs, true ties by text and texts sharing no word included."""
        for seed in range(4):
            texts = random_project(seed)
            self.assertEqual(shortlisted(jev.lane_shortlist(lane_clauses(texts))), ranked_pairs(texts), seed)

    def test_ranking_cost_grows_with_the_words_texts_share_not_with_every_pair(self):
        """#candidate-ranking-lane, #acceptance-lane-read: four times the texts, sharing words as often, cost about
        four times as much, never the sixteen times a score per pair of texts costs; the words every text carries
        are passed over."""
        def cost(per):
            return lines_run(jev.lane_shortlist, lane_clauses(random_lanes(8, per, vocabulary=2 * per)))

        small, large = cost(50), cost(200)
        self.assertLess(large, 6 * small, (small, large))


if __name__ == "__main__":
    unittest.main()
