"""Eval replay: live replay of the frozen eval set through the current pipeline.

Each eval pair is asked through the review server's own Jev seam (JevSeam with
the box's Jev key and general LLM model): about? gate, the draft-check chain,
the asymmetric show and verify cutoffs, and the verifier. No recorded answer
and no simulated filter effect enters the result; each pair makes the calls
the pipeline makes.

Pairs are the eval set's pairs: candidate ranking chose them when they were
asked, and the set holds no corpus to rank again, so the report starts at the
gate.

Reports per stage (gate, Jev threshold, verifier) and per question: pairs,
prevalence, TP, FP, FN, precision and recall of shown marks, with Wilson score
intervals, split by train and test partition.

Spec: jev-suggestions #measure-replay, #measure-report, #measure-baseline.

Tests (offline, fake provider):  python3 tests/jev-eval-replay.py
Live replay (real calls):        python3 tests/jev-eval-replay.py --live \
    --state <review service state dir> [--sample N] [--partition train|test]
"""

import argparse
import importlib.util
import json
import math
import os
import random
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "skill" / "review-spec" / "assets"
_spec = importlib.util.spec_from_file_location("jev_eval_replay", ASSETS / "jev.py")
jev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jev)
_serve_spec = importlib.util.spec_from_file_location("jev_eval_replay_serve", ASSETS / "review-serve.py")
serve = importlib.util.module_from_spec(_serve_spec)
_serve_spec.loader.exec_module(serve)
SETS = jev.load_question_sets(ASSETS / "jev")

EVAL_PATH = os.path.expanduser("~/ops/evals/jev-precision/eval-set.jsonl")
REPORT_PATH = os.path.expanduser("~/ops/evals/jev-precision/live-replay-report.md")
KINDS = ("contradicts", "oversteps", "overlaps")
STAGES = ("gate", "threshold", "verifier")
RETRIES = 2
MAX_PAUSE = 60.0


# ---------------------------------------------------------------------------
# Eval set
# ---------------------------------------------------------------------------

def load_eval_set(path=EVAL_PATH):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def sample(entries, n=None, partition=None, seed=0):
    picked = [e for e in entries if partition is None or e["partition"] == partition]
    if n is not None and n < len(picked):
        picked = random.Random(seed).sample(picked, n)
    return picked


def _has_text(entry):
    clause = (entry.get("clause_text") or "").strip()
    target = (entry.get("target_text") or "").strip()
    return bool(clause) and bool(target) and target != "<unresolved>"


def _spec_file(addr):
    """The spec file part of an eval address, "" when the address is a bare anchor."""
    head = addr.rsplit("#", 1)[0] if "#" in addr else ""
    return head.split("::")[-1].rsplit("/", 1)[-1].split()[-1] if head.strip() else ""


def pair_item(entry):
    """The draft-check item the pipeline builds for this pair (jev.draft_check)."""
    target_addr = entry["target_addr"]
    target_file = _spec_file(target_addr)
    same_spec = not target_file or target_file == _spec_file(entry["clause_addr"])
    anchor = target_addr.rsplit("#", 1)[-1]
    non_goal = "non-goal" in anchor.lower() or "nongoal" in anchor.lower()
    rule = "non-goal" if non_goal else ("same-spec" if same_spec else "cross-spec")
    return jev.draft_check(
        anchor=entry["clause_addr"].rsplit("#", 1)[-1], before="", after=entry["clause_text"],
        target=target_addr, target_text=entry["target_text"],
        target_non_goal=non_goal, same_spec=same_spec,
        path="eval", base="eval-base", revision="eval-head",
        pairing={"rule": rule, "rank": 0, "gate": "yes"})


# ---------------------------------------------------------------------------
# Live replay
# ---------------------------------------------------------------------------

def live_seam(state_dir, store=None):
    """The review server's seam: its question sets, Jev key, and general LLM model from the service state."""
    return jev.JevSeam(SETS, api_key=lambda: serve.jev_provider(state_dir),
                       llm_model=lambda: serve.jev_llm_model(state_dir),
                       record_store=store or jev.JudgmentStore())


def _step_trace(step, record):
    answer = record.get("answer") or {}
    return {"kind": step["kind"], "outcome": record.get("outcome"),
            "escalated": bool(record.get("escalated")), "label": answer.get("label"),
            "p_yes": (answer.get("probabilities") or {}).get("yes")}


def replay_pair(seam, entry, sleep=time.sleep):
    """Ask one eval pair through the seam's chain; return its outcome and the stage that decided it."""
    if not _has_text(entry):
        return {"replayed": False, "shown": False, "stage": "no text", "outcome": None,
                "answer_label": None, "trace": []}
    chain = pair_item(entry)["chain"]
    for attempt in range(RETRIES + 1):
        trace, records = [], []

        def ask(step):
            record = seam.ask(step)
            trace.append(_step_trace(step, record))
            records.append(record)
            return record

        result = jev.chain_result(chain, ask)
        if result.get("outcome") != "unavailable" or attempt == RETRIES:
            break
        sleep(min(jev.pause_left(records[-1]), MAX_PAUSE))
    outcome = result.get("outcome")
    label = result.get("answer", {}).get("label")
    shown = outcome == "shown" and label is not None
    if outcome != "shown":
        stage = outcome or "unavailable"
    elif trace[0]["label"] != "yes":
        stage = "gate"
    else:
        stage = "verifier" if trace[-1]["escalated"] else "threshold"
    return {"replayed": outcome == "shown", "shown": shown, "stage": stage, "outcome": outcome,
            "answer_label": label, "trace": trace, "unsure": result.get("unsure", 0)}


def kept(result, stage):
    """Whether the pair is still a mark after the given stage."""
    trace = result["trace"]
    if not result["replayed"] or not trace or trace[0]["label"] != "yes":
        return False
    if stage == "gate":
        return True
    if stage == "threshold":
        return any(t["escalated"] or (t["kind"] != "about" and t["outcome"] == "shown" and t["label"] == "yes")
                   for t in trace[1:])
    return result["shown"]


def replay_all(seam, entries, workers=4, progress=None):
    def one(entry):
        result = {**entry, **replay_pair(seam, entry)}
        if progress:
            progress(result)
        return result
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(one, entries))


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
    return (max(0.0, (center - margin) / denom), min(1.0, (center + margin) / denom))


def compute_metrics(results, shown=lambda r: r["shown"]):
    """TP, FP, FN, TN, precision, recall with Wilson intervals."""
    tp = sum(1 for r in results if shown(r) and r["ground_truth"] == "yes")
    fp = sum(1 for r in results if shown(r) and r["ground_truth"] == "no")
    fn = sum(1 for r in results if not shown(r) and r["ground_truth"] == "yes")
    tn = sum(1 for r in results if not shown(r) and r["ground_truth"] == "no")
    total_shown, total_positive = tp + fp, tp + fn
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "total": len(results), "total_shown": total_shown, "total_positive": total_positive,
        "precision": tp / total_shown if total_shown else 0.0,
        "recall": tp / total_positive if total_positive else 0.0,
        "precision_ci": wilson_interval(tp, total_shown),
        "recall_ci": wilson_interval(tp, total_positive),
    }


def fmt_pct(val):
    return f"{val:.1%}"


def fmt_ci(ci):
    return f"[{ci[0]:.1%}, {ci[1]:.1%}]"


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _row(label, m):
    prevalence = m["total_positive"] / m["total"] if m["total"] else 0.0
    return (f"| {label} | {m['total']} | {fmt_pct(prevalence)} | {m['total_shown']} | {m['tp']} | {m['fp']} | "
            f"{m['fn']} | {fmt_pct(m['precision'])} {fmt_ci(m['precision_ci'])} | "
            f"{fmt_pct(m['recall'])} {fmt_ci(m['recall_ci'])} |")


def _table(lines, rows):
    lines.append("| Scope | Pairs | Prevalence | Kept | TP | FP | FN | Precision (95% CI) | Recall (95% CI) |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    lines.extend(_row(label, m) for label, m in rows)
    lines.append("")


def generate_report(results):
    replayed = [r for r in results if r["replayed"]]
    lines = ["# Jev precision: live replay report", ""]
    lines.append("Live replay through the current pipeline: about? gate, draft-check chain, "
                 "asymmetric show and verify cutoffs, verifier. Pairs are the eval set's pairs; "
                 "candidate ranking is not re-run.")
    lines.append(f"Pairs: {len(results)}. Replayed: {len(replayed)}. "
                 f"Not replayed: {', '.join(f'{s} {n}' for s, n in _counts(r['stage'] for r in results if not r['replayed']).items()) or 'none'}.")
    lines.append("")
    lines.append("## Cutoffs")
    lines.append("")
    lines.append("| Question | Version | Show | Verify |")
    lines.append("|---|---|---|---|")
    for name in ("about",) + KINDS:
        qs = SETS[name]
        lines.append(f"| {name} | {qs.version} | {qs.show_cutoff if qs.show_cutoff is not None else qs.threshold} | "
                     f"{qs.verify_cutoff if qs.verify_cutoff is not None else '-'} |")
    lines.append("")
    for partition in ("all", "train", "test"):
        part = [r for r in replayed if partition == "all" or r["partition"] == partition]
        if not part:
            continue
        lines.append(f"## Per stage: {partition}")
        lines.append("")
        _table(lines, [(stage, compute_metrics(part, lambda r, s=stage: kept(r, s))) for stage in STAGES])
        lines.append(f"## Per question: {partition}")
        lines.append("")
        _table(lines, [(kind, compute_metrics([r for r in part if r["kind"] == kind]))
                       for kind in KINDS if any(r["kind"] == kind for r in part)])
    lines.append("## Known true positives")
    lines.append("")
    tps = [r for r in results if r["ground_truth"] == "yes"]
    lines.append(f"Total: {len(tps)}. Shown: {sum(1 for r in tps if r['shown'])}.")
    lines.append("")
    lines.append("| ID | Kind | Partition | Shown | Label | Stage |")
    lines.append("|---|---|---|---|---|---|")
    for r in tps:
        lines.append(f"| {r['id']} | {r['kind']} | {r['partition']} | {'yes' if r['shown'] else 'NO'} | "
                     f"{r['answer_label'] or '-'} | {r['stage']} |")
    lines.append("")
    return "\n".join(lines)


def _counts(values):
    out = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


# ---------------------------------------------------------------------------
# Tests (offline: a fake Jev and general LLM behind the same seam)
# ---------------------------------------------------------------------------

class FakeModel:
    """Answers each question by kind with a set P(yes); the verifier answers `verify`. Records every call."""

    def __init__(self, p_yes, verify="yes", fail=0):
        self.p_yes = p_yes
        self.verify = verify
        self.fail = fail
        self.calls = []

    def decide(self, payload):
        kind = next(iter(payload["questions"]))
        self.calls.append(("decide", kind, payload["state"]))
        if self.fail:
            self.fail -= 1
            raise jev.ProviderWait("rate limited", 0.0)
        p = self.p_yes.get(kind, 0.0)
        return {"model": "fake", "answers": {kind: {
            "choice": "yes" if p >= 0.5 else "no", "probabilities": {"yes": p, "no": 1 - p},
            "confidence": max(p, 1 - p)}}}

    def complete(self, payload):
        self.calls.append(("complete", None, payload["messages"][-1]["content"]))
        spans = {"clause_span": "a", "target_span": "b"} if self.verify == "yes" else {"clause_span": "", "target_span": ""}
        return {"choices": [{"message": {"content": json.dumps({"answer": self.verify, **spans})}}]}


def entry(**over):
    base = {"id": "e1", "kind": "contradicts", "partition": "test", "ground_truth": "yes",
            "clause_addr": "aa/docs/specs/a.spec.html#c", "target_addr": "aa/docs/specs/b.spec.html#t",
            "clause_text": "Clause text.", "target_text": "Target text."}
    base.update(over)
    return base


def fake_seam(model):
    return jev.JevSeam(SETS, provider=model, api_key="fake-key", record_store=jev.JudgmentStore())


class TestReplayPair(unittest.TestCase):
    def test_pair_text_reaches_provider(self):
        model = FakeModel({"about": 0.9, "contradicts": 0.95})
        replay_pair(fake_seam(model), entry())
        self.assertEqual(model.calls[0][2]["first"]["text"], "Clause text.")
        self.assertEqual(model.calls[0][2]["second"]["text"], "Target text.")
        self.assertEqual(model.calls[1][2]["after"], "Clause text.")

    def test_gate_no_stops_chain(self):
        model = FakeModel({"about": 0.1, "contradicts": 0.99})
        r = replay_pair(fake_seam(model), entry())
        self.assertFalse(r["shown"])
        self.assertEqual(r["stage"], "gate")
        self.assertEqual([c[1] for c in model.calls], ["about"])

    def test_above_show_cutoff_shows_without_verifier(self):
        model = FakeModel({"about": 0.9, "contradicts": 0.95})
        r = replay_pair(fake_seam(model), entry())
        self.assertTrue(r["shown"])
        self.assertEqual((r["answer_label"], r["stage"]), ("contradicts", "threshold"))
        self.assertNotIn("complete", [c[0] for c in model.calls])

    def test_verify_band_calls_verifier(self):
        for verify, shown in (("yes", True), ("no", False)):
            model = FakeModel({"about": 0.9, "contradicts": 0.8}, verify=verify)
            r = replay_pair(fake_seam(model), entry(target_addr="#t"))
            self.assertEqual(r["shown"], shown)
            self.assertEqual(r["stage"], "verifier")
            self.assertIn("complete", [c[0] for c in model.calls])
            self.assertTrue(kept(r, "threshold"))

    def test_below_verify_cutoff_silent_no(self):
        model = FakeModel({"about": 0.9, "contradicts": 0.5})
        r = replay_pair(fake_seam(model), entry(target_addr="#t"))
        self.assertFalse(r["shown"])
        self.assertFalse(kept(r, "threshold"))
        self.assertTrue(kept(r, "gate"))
        self.assertNotIn("complete", [c[0] for c in model.calls])

    def test_cross_spec_chain_reaches_overlaps(self):
        model = FakeModel({"about": 0.9, "overlaps": 0.95})
        r = replay_pair(fake_seam(model), entry())
        self.assertEqual(r["answer_label"], "overlaps")
        self.assertEqual([c[1] for c in model.calls], ["about", "contradicts", "oversteps", "overlaps"])

    def test_same_spec_chain_stops_after_contradicts(self):
        for addr in ("#t", "t", "aa/docs/specs/a.spec.html#t"):
            model = FakeModel({"about": 0.9, "overlaps": 0.95})
            r = replay_pair(fake_seam(model), entry(target_addr=addr))
            self.assertFalse(r["shown"])
            self.assertEqual([c[1] for c in model.calls], ["about", "contradicts"], addr)

    def test_non_goal_target_asks_nongoal_set(self):
        model = FakeModel({"about": 0.9})
        replay_pair(fake_seam(model), entry(target_addr="b.spec.html#non-goal-x"))
        self.assertEqual([c[1] for c in model.calls], ["about", "contradicts-nongoal"])

    def test_missing_text_not_replayed(self):
        model = FakeModel({})
        for over in ({"clause_text": ""}, {"target_text": "<unresolved>"}):
            r = replay_pair(fake_seam(model), entry(**over))
            self.assertEqual((r["replayed"], r["stage"]), (False, "no text"))
        self.assertEqual(model.calls, [])

    def test_rate_limit_retries(self):
        model = FakeModel({"about": 0.9, "contradicts": 0.95}, fail=1)
        r = replay_pair(fake_seam(model), entry(), sleep=lambda s: None)
        self.assertTrue(r["shown"])

    def test_unavailable_after_retries_not_counted(self):
        model = FakeModel({"about": 0.9}, fail=RETRIES + 1)
        r = replay_pair(fake_seam(model), entry(), sleep=lambda s: None)
        self.assertEqual((r["replayed"], r["stage"]), (False, "unavailable"))


class TestLiveSeam(unittest.TestCase):
    def test_uses_service_key_and_real_providers(self):
        with tempfile.TemporaryDirectory() as state:
            os.makedirs(os.path.join(state, "providers"))
            path = os.path.join(state, "providers", "jev.toml")
            with open(path, "w") as f:
                f.write('key = "k-test"\nllm_model = "m-test"\n')
            os.chmod(path, 0o600)
            seam = live_seam(state)
            provider = seam.provider()
            self.assertIsInstance(provider, jev.OpenRouterProvider)
            self.assertIsInstance(seam._fallback_provider(provider), jev.ClaudeCliProvider)
            self.assertEqual(seam.llm_model(), "m-test")

    def test_no_key_is_off(self):
        with tempfile.TemporaryDirectory() as state:
            self.assertIsNone(live_seam(state).provider())


class TestReport(unittest.TestCase):
    def test_wilson(self):
        lo, hi = wilson_interval(5, 10)
        self.assertAlmostEqual(lo, 0.2366, places=3)
        self.assertAlmostEqual(hi, 0.7634, places=3)
        self.assertEqual(wilson_interval(0, 0), (0.0, 1.0))

    def test_report_per_stage_and_question(self):
        seam = fake_seam(FakeModel({"about": 0.9, "contradicts": 0.95}))
        results = replay_all(seam, [entry(id="a"), entry(id="b", ground_truth="no", partition="train"),
                                    entry(id="c", clause_text="")], workers=2)
        report = generate_report(results)
        for text in ("## Per stage: test", "## Per question: train", "| verifier |", "| contradicts |",
                     "Not replayed: no text 1", "95% CI"):
            self.assertIn(text, report)
        m = compute_metrics([r for r in results if r["replayed"]])
        self.assertEqual((m["tp"], m["fp"]), (1, 1))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--live", action="store_true", help="replay through the real providers (makes model calls)")
    parser.add_argument("--state", help="review service state dir holding providers/jev.toml")
    parser.add_argument("--eval", default=EVAL_PATH)
    parser.add_argument("--report", default=REPORT_PATH)
    parser.add_argument("--records", help="keep the replay's judgment records in this store path")
    parser.add_argument("--sample", type=int, help="replay a seeded random sample of N pairs")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--partition", choices=("train", "test"))
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    if not args.state:
        parser.error("--live needs --state")
    seam = live_seam(args.state, jev.JudgmentStore(args.records) if args.records else None)
    if seam.provider() is None:
        parser.error("no Jev key in %s/providers/jev.toml (mode 0600)" % args.state)
    entries = sample(load_eval_set(args.eval), args.sample, args.partition, args.seed)
    print(f"Replaying {len(entries)} pairs live...", flush=True)
    done = []

    def progress(r):
        done.append(r)
        if len(done) % 25 == 0 or len(done) == len(entries):
            print(f"  {len(done)}/{len(entries)}", flush=True)

    results = replay_all(seam, entries, args.workers, progress)
    report = generate_report(results)
    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    with open(args.report, "w") as f:
        f.write(report)
    print(report)
    print(f"\nReport written to {args.report}")


if __name__ == "__main__":
    if "--live" in sys.argv[1:]:
        main(sys.argv[1:])
    else:
        unittest.main(argv=sys.argv[:1] + sys.argv[1:], verbosity=2)
