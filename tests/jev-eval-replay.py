"""Eval replay: baseline pipeline measurement (ANN-397).

Replays the frozen eval set through the current pipeline (post-#96), exercising
candidate ranking (build_corpus_questions), gate question, Jev draft-check
questions, threshold logic, and fallback. Uses fakes for the Jev provider and
LLM where the eval set carries answers (jev_answer, jev_probs, jev_confidence).

Reports per-stage precision, recall, FP, FN, TP counts with Wilson score
intervals, split by train/test partition and by question kind.

Spec: jev-suggestions #measure-baseline, #measure-replay, #measure-report.

Run: python3 tests/jev-eval-replay.py
"""

import importlib.util
import json
import math
import os
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("jev_eval_replay", ROOT / "skill" / "review-spec" / "assets" / "jev.py")
jev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jev)
SETS = jev.load_question_sets(ROOT / "skill" / "review-spec" / "assets" / "jev")

EVAL_PATH = os.path.expanduser("~/ops/evals/jev-precision/eval-set.jsonl")
REPORT_PATH = os.path.expanduser("~/ops/evals/jev-precision/live-baseline-report.md")


# ---------------------------------------------------------------------------
# Eval set loading
# ---------------------------------------------------------------------------

def load_eval_set(path=EVAL_PATH):
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


# ---------------------------------------------------------------------------
# Translation: old multi-label or yes/no format to uniform yes/no
# ---------------------------------------------------------------------------

def translate_to_yesno(entry):
    """Translate eval entry to yes/no format for pipeline replay.

    Returns dict with label, probs, confidence, or None if not enough data.
    """
    kind = entry["kind"]
    jev_probs = entry.get("jev_probs") or {}
    jev_answer = entry.get("jev_answer")
    jev_confidence = entry.get("jev_confidence")

    if jev_answer is None and not jev_probs:
        return None

    # Already yes/no format: answer is "yes"/"no", or probs have yes/no keys
    if jev_answer in ("yes", "no") or "yes" in jev_probs or "no" in jev_probs:
        p_yes = jev_probs.get("yes", 1.0 if jev_answer == "yes" else 0.0)
        p_no = jev_probs.get("no", 1.0 - p_yes)
        label = jev_answer if jev_answer in ("yes", "no") else (
            "yes" if p_yes >= p_no else "no")
        return {"label": label, "probs": {"yes": p_yes, "no": p_no},
                "confidence": jev_confidence}

    # Multi-label format (audit entries with old pipeline data): translate
    p_yes = jev_probs.get(kind, 0.0)
    if jev_answer == kind:
        label = "yes"
        confidence = jev_confidence
    elif jev_answer is not None:
        label = "no"
        confidence = 1.0 - p_yes if p_yes > 0 else jev_confidence
    else:
        label = "yes" if p_yes >= 0.5 else "no"
        confidence = p_yes if label == "yes" else 1.0 - p_yes

    return {"label": label, "probs": {"yes": p_yes, "no": 1.0 - p_yes},
            "confidence": confidence}


# ---------------------------------------------------------------------------
# Fake provider: returns pre-recorded answers for eval replay
# ---------------------------------------------------------------------------

class EvalFakeProvider:
    """Returns gate passes and pre-recorded Jev answers for one eval entry.

    For the gate (about?): always yes with confidence 1.0.
    For the entry's kind: uses the translated yes/no answer.
    For earlier chain steps: confident no so the chain reaches the entry's kind.
    LLM fallback: returns the same label as Jev.
    """

    def __init__(self, kind, label, probs, confidence):
        self._kind = kind
        self._label = label
        self._probs = probs
        self._confidence = confidence
        self._last_kind = None
        self._lock = threading.Lock()

    def decide(self, payload):
        questions = payload.get("questions", {})
        qname = next(iter(questions))

        with self._lock:
            self._last_kind = qname

        if qname == "about":
            return {"model": "eval-gate", "answers": {"about": {
                "choice": "yes", "probabilities": {"yes": 1.0, "no": 0.0},
                "confidence": 1.0}}}

        if qname == self._kind:
            return {"model": "eval-jev", "answers": {qname: {
                "choice": self._label,
                "probabilities": dict(self._probs),
                "confidence": self._confidence}}}

        # Earlier chain step: confident no so chain continues
        return {"model": "eval-pass", "answers": {qname: {
            "choice": "no", "probabilities": {"yes": 0.0, "no": 1.0},
            "confidence": 1.0}}}

    def complete(self, payload):
        """LLM verifier/fallback: returns the same label Jev gave.

        For draft-check verifier calls, returns the verifier format with
        answer + quoted spans (#verifier).  For other fallback calls, returns
        the legacy choice format.
        """
        with self._lock:
            kind = self._last_kind
        label = self._label if kind == self._kind else "no"
        # Verifier format: answer + quoted spans (required by _verify)
        result = {"answer": label}
        if label == "yes":
            result["clause_span"] = "clause conflict"
            result["target_span"] = "target conflict"
        return {"choices": [{"message": {
            "content": json.dumps(result)}}]}


# ---------------------------------------------------------------------------
# Pipeline replay
# ---------------------------------------------------------------------------

def replay_entry(entry, question_sets):
    """Replay one eval entry through the full pipeline chain.

    Returns dict with: replayed, shown, outcome, answer_label, kind.
    """
    kind = entry["kind"]
    translated = translate_to_yesno(entry)

    if translated is None:
        # No Jev data: historically shown in old pipeline
        return {"replayed": False, "shown": True,
                "outcome": "shown (legacy)", "answer_label": kind,
                "kind": kind}

    clause_text = entry["clause_text"]
    target_text = entry["target_text"]

    provider = EvalFakeProvider(
        kind, translated["label"], translated["probs"],
        translated["confidence"])

    store = jev.JudgmentStore()
    seam = jev.JevSeam(question_sets, provider=provider, api_key="fake-key",
                       llm_model="eval-replay-llm", record_store=store,
                       clock=lambda: "2026-09-28T00:00:00.000Z")

    # Build the draft-check chain for this pair
    item = jev.draft_check(
        anchor="eval-clause", before="", after=clause_text,
        target="eval-target", target_text=target_text,
        path="eval", base="base", revision="head")

    # Run through the chain (exercises gate, threshold, fallback)
    result = seam.ask_chain(item["chain"])
    outcome = result.get("outcome")
    answer_label = result.get("answer", {}).get("label")

    # A mark is shown when the chain returns a non-None label with shown outcome
    shown = outcome == "shown" and answer_label is not None

    return {"replayed": True, "shown": shown, "outcome": outcome,
            "answer_label": answer_label, "kind": kind,
            "unsure": result.get("unsure", 0)}


def replay_all(entries, question_sets):
    """Replay every eval entry and return merged results."""
    results = []
    for entry in entries:
        result = replay_entry(entry, question_sets)
        results.append({**entry, **result})
    return results


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def wilson_interval(successes, total, z=1.96):
    """Wilson score 95% confidence interval."""
    if total == 0:
        return (0.0, 1.0)
    p = successes / total
    denom = 1 + z ** 2 / total
    center = p + z ** 2 / (2 * total)
    margin = z * math.sqrt(p * (1 - p) / total + z ** 2 / (4 * total ** 2))
    lower = (center - margin) / denom
    upper = (center + margin) / denom
    return (max(0.0, lower), min(1.0, upper))


def compute_metrics(results):
    """TP, FP, FN, TN, precision, recall with Wilson intervals."""
    tp = sum(1 for r in results if r["shown"] and r["ground_truth"] == "yes")
    fp = sum(1 for r in results if r["shown"] and r["ground_truth"] == "no")
    fn = sum(1 for r in results if not r["shown"] and r["ground_truth"] == "yes")
    tn = sum(1 for r in results if not r["shown"] and r["ground_truth"] == "no")
    total_shown = tp + fp
    total_positive = tp + fn
    precision = tp / total_shown if total_shown > 0 else 0.0
    recall = tp / total_positive if total_positive > 0 else 0.0
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "total": len(results), "total_shown": total_shown,
        "total_positive": total_positive,
        "precision": precision, "recall": recall,
        "precision_ci": wilson_interval(tp, total_shown),
        "recall_ci": wilson_interval(tp, total_positive),
    }


def fmt_pct(val):
    return f"{val:.1%}"


def fmt_ci(ci):
    return f"[{ci[0]:.1%}, {ci[1]:.1%}]"


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(results):
    """Generate the baseline report as Markdown."""
    lines = []
    lines.append("# Jev precision: live baseline report")
    lines.append("")
    lines.append("Pipeline: post-#96 (yes/no questions, current thresholds, LLM fallback).")
    lines.append(f"Eval set: {len(results)} entries "
                 f"({sum(1 for r in results if r['ground_truth']=='yes')} positive, "
                 f"{sum(1 for r in results if r['ground_truth']=='no')} negative).")

    replayed = [r for r in results if r["replayed"]]
    legacy = [r for r in results if not r["replayed"]]
    lines.append(f"Replayed through pipeline: {len(replayed)}. "
                 f"Legacy (no Jev data, historically shown): {len(legacy)}.")
    lines.append("")

    # Thresholds
    lines.append("## Thresholds")
    lines.append("")
    lines.append("| Question | Threshold | Fallback |")
    lines.append("|---|---|---|")
    for name in ("contradicts", "oversteps", "overlaps"):
        qs = SETS[name]
        lines.append(f"| {name} | {qs.threshold} | {qs.fallback} |")
    lines.append("")

    # Overall metrics
    lines.append("## Overall baseline")
    lines.append("")
    m = compute_metrics(results)
    _metrics_table(lines, m)

    # Per-partition
    for partition in ("train", "test"):
        subset = [r for r in results if r["partition"] == partition]
        if not subset:
            continue
        lines.append(f"## Partition: {partition}")
        lines.append("")
        m = compute_metrics(subset)
        _metrics_table(lines, m)

    # Per-question kind
    for kind in ("contradicts", "oversteps", "overlaps"):
        subset = [r for r in results if r["kind"] == kind]
        if not subset:
            continue
        lines.append(f"## Question: {kind}")
        lines.append("")
        m = compute_metrics(subset)
        _metrics_table(lines, m)
        # Per partition within kind
        for partition in ("train", "test"):
            part = [r for r in subset if r["partition"] == partition]
            if not part:
                continue
            mp = compute_metrics(part)
            lines.append(f"### {kind} / {partition}")
            lines.append("")
            _metrics_table(lines, mp)

    # Replayed-only metrics
    lines.append("## Replayed entries only")
    lines.append("")
    lines.append(f"Entries with Jev data replayed through the current pipeline: {len(replayed)}.")
    lines.append("")
    m = compute_metrics(replayed)
    _metrics_table(lines, m)

    for partition in ("train", "test"):
        part = [r for r in replayed if r["partition"] == partition]
        if not part:
            continue
        lines.append(f"### Replayed / {partition}")
        lines.append("")
        _metrics_table(lines, compute_metrics(part))

    # Known TPs
    lines.append("## Known true positives")
    lines.append("")
    tps = [r for r in results if r["ground_truth"] == "yes"]
    shown_tps = [r for r in tps if r["shown"]]
    missed_tps = [r for r in tps if not r["shown"]]
    lines.append(f"Total: {len(tps)}. Shown: {len(shown_tps)}. Missed: {len(missed_tps)}.")
    lines.append("")
    lines.append("| ID | Kind | Partition | Replayed | Shown | Outcome |")
    lines.append("|---|---|---|---|---|---|")
    for r in tps:
        lines.append(f"| {r['id']} | {r['kind']} | {r['partition']} | "
                     f"{'yes' if r['replayed'] else 'no'} | "
                     f"{'yes' if r['shown'] else 'NO'} | {r['outcome']} |")
    lines.append("")

    if missed_tps:
        lines.append("### Missed true positives")
        lines.append("")
        for r in missed_tps:
            lines.append(f"- {r['id']} ({r['kind']}): {r['clause_addr']} vs {r['target_addr']}")
        lines.append("")

    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append("| Scope | Total | Shown | TP | FP | FN | Precision | 95% CI | Recall | 95% CI |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for label, subset in [
        ("All", results),
        ("Replayed", replayed),
        ("Train", [r for r in results if r["partition"] == "train"]),
        ("Test", [r for r in results if r["partition"] == "test"]),
        ("contradicts", [r for r in results if r["kind"] == "contradicts"]),
        ("oversteps", [r for r in results if r["kind"] == "oversteps"]),
        ("overlaps", [r for r in results if r["kind"] == "overlaps"]),
    ]:
        m = compute_metrics(subset)
        lines.append(f"| {label} | {m['total']} | {m['total_shown']} | "
                     f"{m['tp']} | {m['fp']} | {m['fn']} | "
                     f"{fmt_pct(m['precision'])} | {fmt_ci(m['precision_ci'])} | "
                     f"{fmt_pct(m['recall'])} | {fmt_ci(m['recall_ci'])} |")
    lines.append("")

    return "\n".join(lines)


def _metrics_table(lines, m):
    lines.append(f"- Pairs: {m['total']}")
    lines.append(f"- Shown (marks displayed): {m['total_shown']}")
    lines.append(f"- TP: {m['tp']}, FP: {m['fp']}, FN: {m['fn']}, TN: {m['tn']}")
    prevalence = m['total_positive'] / m['total'] if m['total'] > 0 else 0
    lines.append(f"- Prevalence: {fmt_pct(prevalence)} "
                 f"({m['total_positive']} positives in {m['total']} pairs)")
    lines.append(f"- Precision: {fmt_pct(m['precision'])} {fmt_ci(m['precision_ci'])}")
    lines.append(f"- Recall: {fmt_pct(m['recall'])} {fmt_ci(m['recall_ci'])}")
    lines.append("")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTranslation(unittest.TestCase):
    """Translation from eval format to yes/no."""

    def test_yesno_format_passthrough(self):
        entry = {"kind": "contradicts", "jev_answer": "no",
                 "jev_probs": {"yes": 0.02, "no": 0.98}, "jev_confidence": 0.95}
        t = translate_to_yesno(entry)
        self.assertEqual(t["label"], "no")
        self.assertAlmostEqual(t["probs"]["yes"], 0.02)
        self.assertEqual(t["confidence"], 0.95)

    def test_yesno_format_yes(self):
        entry = {"kind": "contradicts", "jev_answer": "yes",
                 "jev_probs": {"yes": 0.55, "no": 0.45}, "jev_confidence": 0.1}
        t = translate_to_yesno(entry)
        self.assertEqual(t["label"], "yes")
        self.assertAlmostEqual(t["probs"]["yes"], 0.55)
        self.assertEqual(t["confidence"], 0.1)

    def test_multi_label_answer_matches_kind(self):
        entry = {"kind": "contradicts", "jev_answer": "contradicts",
                 "jev_probs": {"contradicts": 0.72, "overlaps": 0.24,
                               "oversteps": 0.03, "unrelated": 0.01},
                 "jev_confidence": 0.61}
        t = translate_to_yesno(entry)
        self.assertEqual(t["label"], "yes")
        self.assertAlmostEqual(t["probs"]["yes"], 0.72)
        self.assertEqual(t["confidence"], 0.61)

    def test_multi_label_answer_differs(self):
        entry = {"kind": "oversteps", "jev_answer": "contradicts",
                 "jev_probs": {"contradicts": 0.72, "overlaps": 0.24,
                               "oversteps": 0.03, "unrelated": 0.01},
                 "jev_confidence": 0.61}
        t = translate_to_yesno(entry)
        self.assertEqual(t["label"], "no")
        self.assertAlmostEqual(t["probs"]["yes"], 0.03)
        self.assertAlmostEqual(t["confidence"], 0.97)

    def test_no_data_returns_none(self):
        entry = {"kind": "contradicts", "jev_answer": None,
                 "jev_probs": {}, "jev_confidence": None}
        self.assertIsNone(translate_to_yesno(entry))

    def test_answer_only_no_probs(self):
        entry = {"kind": "overlaps", "jev_answer": "yes",
                 "jev_probs": {}, "jev_confidence": None}
        t = translate_to_yesno(entry)
        self.assertIsNotNone(t)
        self.assertEqual(t["label"], "yes")


class TestFakeProvider(unittest.TestCase):
    """Fake provider returns expected shapes."""

    def test_gate_always_yes(self):
        provider = EvalFakeProvider("contradicts", "yes", {"yes": 0.7, "no": 0.3}, 0.6)
        resp = provider.decide({"questions": {"about": {}}, "state": {}})
        self.assertEqual(resp["answers"]["about"]["choice"], "yes")
        self.assertEqual(resp["answers"]["about"]["confidence"], 1.0)

    def test_kind_returns_recorded(self):
        provider = EvalFakeProvider("contradicts", "yes", {"yes": 0.7, "no": 0.3}, 0.6)
        resp = provider.decide({"questions": {"contradicts": {}}, "state": {}})
        self.assertEqual(resp["answers"]["contradicts"]["choice"], "yes")
        self.assertEqual(resp["answers"]["contradicts"]["confidence"], 0.6)

    def test_earlier_step_returns_confident_no(self):
        provider = EvalFakeProvider("overlaps", "yes", {"yes": 0.8, "no": 0.2}, 0.7)
        resp = provider.decide({"questions": {"contradicts": {}}, "state": {}})
        self.assertEqual(resp["answers"]["contradicts"]["choice"], "no")
        self.assertEqual(resp["answers"]["contradicts"]["confidence"], 1.0)

    def test_llm_verifier(self):
        provider = EvalFakeProvider("contradicts", "yes", {"yes": 0.5, "no": 0.5}, 0.1)
        provider.decide({"questions": {"contradicts": {}}, "state": {}})
        resp = provider.complete({"messages": [], "model": "test"})
        body = json.loads(resp["choices"][0]["message"]["content"])
        self.assertEqual(body["answer"], "yes")
        self.assertTrue(body.get("clause_span"))
        self.assertTrue(body.get("target_span"))


class TestWilsonInterval(unittest.TestCase):
    def test_basic(self):
        lo, hi = wilson_interval(5, 10)
        self.assertAlmostEqual(lo, 0.2366, places=3)
        self.assertAlmostEqual(hi, 0.7634, places=3)

    def test_zero_total(self):
        lo, hi = wilson_interval(0, 0)
        self.assertEqual(lo, 0.0)
        self.assertEqual(hi, 1.0)

    def test_all_success(self):
        lo, hi = wilson_interval(10, 10)
        self.assertGreater(lo, 0.7)
        self.assertAlmostEqual(hi, 1.0, places=2)

    def test_zero_success(self):
        lo, hi = wilson_interval(0, 10)
        self.assertAlmostEqual(lo, 0.0, places=2)
        self.assertLess(hi, 0.3)


class TestReplayEntry(unittest.TestCase):
    """Replay exercises the pipeline correctly."""

    def test_confident_yes_shows_mark(self):
        entry = {"id": "t1", "kind": "contradicts", "partition": "test",
                 "source": "test", "ground_truth": "yes",
                 "clause_text": "A", "target_text": "B",
                 "clause_addr": "#a", "target_addr": "#b",
                 "jev_answer": "yes", "jev_probs": {"yes": 0.9, "no": 0.1},
                 "jev_confidence": 0.85, "jev_outcome": "shown",
                 "cause": "", "notes": []}
        result = replay_entry(entry, SETS)
        self.assertTrue(result["replayed"])
        self.assertTrue(result["shown"])
        self.assertEqual(result["answer_label"], "contradicts")

    def test_confident_no_hides_mark(self):
        entry = {"id": "t2", "kind": "contradicts", "partition": "test",
                 "source": "test", "ground_truth": "no",
                 "clause_text": "A", "target_text": "B",
                 "clause_addr": "#a", "target_addr": "#b",
                 "jev_answer": "no", "jev_probs": {"yes": 0.02, "no": 0.98},
                 "jev_confidence": 0.95, "jev_outcome": "shown",
                 "cause": "", "notes": []}
        result = replay_entry(entry, SETS)
        self.assertTrue(result["replayed"])
        self.assertFalse(result["shown"])

    def test_unsure_yes_in_verify_band_shows(self):
        # P(yes) in verify band [0.70, 0.90): verifier confirms -> shown
        entry = {"id": "t3", "kind": "contradicts", "partition": "test",
                 "source": "test", "ground_truth": "no",
                 "clause_text": "A", "target_text": "B",
                 "clause_addr": "#a", "target_addr": "#b",
                 "jev_answer": "yes", "jev_probs": {"yes": 0.75, "no": 0.25},
                 "jev_confidence": 0.6, "jev_outcome": "escalated",
                 "cause": "", "notes": []}
        result = replay_entry(entry, SETS)
        self.assertTrue(result["replayed"])
        self.assertTrue(result["shown"])

    def test_below_verify_cutoff_silent_no(self):
        # P(yes) < verify_cutoff (0.70): silent no, never shown (#asymmetric)
        entry = {"id": "t3b", "kind": "contradicts", "partition": "test",
                 "source": "test", "ground_truth": "no",
                 "clause_text": "A", "target_text": "B",
                 "clause_addr": "#a", "target_addr": "#b",
                 "jev_answer": "yes", "jev_probs": {"yes": 0.55, "no": 0.45},
                 "jev_confidence": 0.1, "jev_outcome": "escalated",
                 "cause": "", "notes": []}
        result = replay_entry(entry, SETS)
        self.assertTrue(result["replayed"])
        self.assertFalse(result["shown"])

    def test_legacy_entry_counts_as_shown(self):
        entry = {"id": "t4", "kind": "contradicts", "partition": "test",
                 "source": "audit", "ground_truth": "no",
                 "clause_text": "A", "target_text": "B",
                 "clause_addr": "#a", "target_addr": "#b",
                 "jev_answer": None, "jev_probs": {},
                 "jev_confidence": None, "jev_outcome": "shown",
                 "cause": "", "notes": []}
        result = replay_entry(entry, SETS)
        self.assertFalse(result["replayed"])
        self.assertTrue(result["shown"])

    def test_oversteps_chain_reaches_oversteps(self):
        entry = {"id": "t5", "kind": "oversteps", "partition": "test",
                 "source": "test", "ground_truth": "no",
                 "clause_text": "X", "target_text": "Y",
                 "clause_addr": "#a", "target_addr": "#b",
                 "jev_answer": "yes", "jev_probs": {"yes": 0.9, "no": 0.1},
                 "jev_confidence": 0.85, "jev_outcome": "shown",
                 "cause": "", "notes": []}
        result = replay_entry(entry, SETS)
        self.assertTrue(result["shown"])
        self.assertEqual(result["answer_label"], "oversteps")

    def test_overlaps_chain_reaches_overlaps(self):
        entry = {"id": "t6", "kind": "overlaps", "partition": "test",
                 "source": "test", "ground_truth": "no",
                 "clause_text": "P", "target_text": "Q",
                 "clause_addr": "#a", "target_addr": "#b",
                 "jev_answer": "yes", "jev_probs": {"yes": 0.9, "no": 0.1},
                 "jev_confidence": 0.85, "jev_outcome": "shown",
                 "cause": "", "notes": []}
        result = replay_entry(entry, SETS)
        self.assertTrue(result["shown"])
        self.assertEqual(result["answer_label"], "overlaps")


class TestMetrics(unittest.TestCase):
    def test_simple_metrics(self):
        results = [
            {"shown": True, "ground_truth": "yes"},
            {"shown": True, "ground_truth": "no"},
            {"shown": False, "ground_truth": "yes"},
            {"shown": False, "ground_truth": "no"},
        ]
        m = compute_metrics(results)
        self.assertEqual(m["tp"], 1)
        self.assertEqual(m["fp"], 1)
        self.assertEqual(m["fn"], 1)
        self.assertEqual(m["tn"], 1)
        self.assertAlmostEqual(m["precision"], 0.5)
        self.assertAlmostEqual(m["recall"], 0.5)


class TestBaselineOnEvalSet(unittest.TestCase):
    """Integration: full replay on the frozen eval set."""

    def setUp(self):
        if not os.path.exists(EVAL_PATH):
            self.skipTest("Eval set not available")
        self.entries = load_eval_set()
        self.results = replay_all(self.entries, SETS)

    def test_all_entries_processed(self):
        self.assertEqual(len(self.results), len(self.entries))

    def test_known_tp_count(self):
        tps = [r for r in self.results if r["ground_truth"] == "yes"]
        self.assertEqual(len(tps), 17)

    def test_replayed_count(self):
        replayed = [r for r in self.results if r["replayed"]]
        # 319 with probs + 7 with answer only = 326
        self.assertGreaterEqual(len(replayed), 319)

    def test_replayed_tp_recall(self):
        """Report replayed TP recall; untuned cutoffs may lose some (#measure-bar).

        Tuning runs in a separate correctness loop that uses this
        infrastructure.  Here we assert that at least half the replayed TPs
        are shown (sanity) and report the actual recall.
        """
        replayed_tps = [r for r in self.results
                        if r["replayed"] and r["ground_truth"] == "yes"]
        shown_tps = [r for r in replayed_tps if r["shown"]]
        self.assertGreater(len(shown_tps), len(replayed_tps) // 2,
                           f"Too many TPs lost: {len(shown_tps)}/{len(replayed_tps)}")

    def test_precision_below_bar(self):
        """Baseline precision is expected to be well below 80% (the target)."""
        m = compute_metrics(self.results)
        # With ~17 TP and ~2500 FP, precision is < 1%
        self.assertLess(m["precision"], 0.05)

    def test_report_generated(self):
        report = generate_report(self.results)
        self.assertIn("Jev precision: live baseline report", report)
        self.assertIn("95% CI", report)
        self.assertIn("TP:", report)


# ---------------------------------------------------------------------------
# Main: run replay and write report
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if os.path.exists(EVAL_PATH):
        print("Loading eval set...")
        entries = load_eval_set()
        print(f"Loaded {len(entries)} entries.")

        print("Replaying through pipeline...")
        results = replay_all(entries, SETS)
        replayed = sum(1 for r in results if r["replayed"])
        print(f"Replayed: {replayed}, legacy: {len(results) - replayed}")

        print("Computing metrics...")
        m = compute_metrics(results)
        print(f"Overall: TP={m['tp']}, FP={m['fp']}, FN={m['fn']}, TN={m['tn']}")
        print(f"Precision: {fmt_pct(m['precision'])} {fmt_ci(m['precision_ci'])}")
        print(f"Recall: {fmt_pct(m['recall'])} {fmt_ci(m['recall_ci'])}")

        report = generate_report(results)
        os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
        with open(REPORT_PATH, "w") as f:
            f.write(report)
        print(f"\nReport written to {REPORT_PATH}")
        print("\n" + "=" * 60)

    unittest.main(argv=[""], exit=True, verbosity=2)
