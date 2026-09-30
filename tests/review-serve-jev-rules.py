"""Project-wide rules behind fakes (project-rules #questions, #marks, #pending)."""

import importlib.util
import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("review_serve_jev_rules_test", ROOT / "skill" / "review-spec" / "assets" / "jev.py")
jev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jev)

ONBOARDING = "Every change to what a user sees or does adds its onboarding section."
LOCAL = "The report page shows totals per week."
FEATURE = "The export button downloads a CSV of the current table."


def git(root, *args):
    return subprocess.check_output(("git", "-C", str(root), *args), text=True).strip()


def spec(*criteria, body=""):
    items = "".join(f'<p data-acceptance-criterion data-anchor="{anchor}">{text}</p>' for anchor, text in criteria)
    return ('<header data-anchor="header"><h1 data-anchor="title">Spec</h1></header>'
            f'<section data-spec-section="acceptance" data-anchor="acceptance"><h2>Acceptance criteria</h2>{items}</section>'
            f'<section data-anchor="behavior"><p data-anchor="body">{body}</p></section>')


SETS = jev.load_question_sets(ROOT / "skill" / "review-spec" / "assets" / "jev")
# The rule check is a chain (#q-rule): triggered? then covered?; the maps below name the outcome it reaches.
RULE_ANSWERS = {"triggered": {"not triggered": "no", "covered": "yes", "missed": "yes"},
                "covered": {"not triggered": "yes", "covered": "yes", "missed": "no"}}


def general_kind(payload):
    question = json.loads(payload["messages"][0]["content"])["question"]
    return next(name for name, qset in SETS.items() if qset.instructions == question)


class FakeProvider:
    """Jev answers from scope and rule maps; the general LLM from its own map; every payload is kept.
    A rule map value is the rule check's outcome, missed, covered, or not triggered, answered per chain question."""

    def __init__(self, scope=None, rule=None, general=None, confidence=0.95, gate=None):
        self.scope, self.rule, self.general = dict(scope or {}), dict(rule or {}), dict(general or {})
        self.confidence = confidence
        self.gate = gate
        self.calls, self.general_calls = [], []
        self.lock = threading.Lock()

    def decide(self, payload):
        with self.lock:
            self.calls.append(payload)
        kind = next(iter(payload["questions"]))
        state = payload["state"]
        if kind == "scope":
            label, confidence = self.scope.get(state["criterion"], ("this feature", self.confidence))
        elif kind in RULE_ANSWERS:
            label, confidence = self.rule.get(state["rule"], ("not triggered", self.confidence))
            if callable(label):
                label = label(state["spec"])
            label = RULE_ANSWERS[kind][label]
        else:
            label, confidence = "unrelated", 0.95
        return {"answers": {kind: {"choice": label, "confidence": confidence}}}

    def complete(self, payload):
        if self.gate is not None:
            self.gate.wait(5)
        with self.lock:
            self.general_calls.append(payload)
        state = json.loads(payload["messages"][-1]["content"])
        kind = general_kind(payload)
        answer = self.general.get(("scope" if kind == "scope" else "rule", state.get("criterion", state.get("rule"))))
        if isinstance(answer, list):  # one answer per call, the last repeating
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if isinstance(answer, BaseException):
            raise answer
        answer = RULE_ANSWERS.get(kind, {}).get(answer, answer)
        return {"model": payload["model"], "choices": [{"message": {"content": json.dumps({"choice": answer})}}]}

    def asked(self, kind):
        """Jev calls for one question; rule is the rule check's first, triggered?."""
        kind = "triggered" if kind == "rule" else kind
        return [call for call in self.calls if kind in call["questions"]]

    def general_asked(self, kind, rule=ONBOARDING):
        return [call for call in self.general_calls
                if general_kind(call) == kind and json.loads(call["messages"][-1]["content"]).get("rule") == rule]


class RulesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.specs = self.root / "docs" / "specs"
        self.specs.mkdir(parents=True)
        git(self.root, "init", "-q")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        (self.specs / name).write_text(text, encoding="utf-8")

    def commit(self):
        git(self.root, "add", "-A")
        git(self.root, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qm", "c", "--allow-empty")
        return git(self.root, "rev-parse", "HEAD")

    def service(self, provider, state="state"):
        return jev.JevService(state_dir=Path(self.tmp.name) / state, provider=provider, api_key="fake")

    def mount(self):
        return {"slug": "", "root": str(self.root), "narrow_root": str(self.root / "docs")}

    def read(self, service, name="b.spec.html", base=None, events=(), settle=True):
        mount = self.mount()
        base = base or self.base
        result = service.response(mount, str(self.specs / name), "specs/" + name, base, list(events), "", [mount])
        deadline = time.time() + 5
        while settle and any(item["state"] == "pending" for item in result["items"]) and time.time() < deadline:
            time.sleep(0.01)
            result = service.response(mount, str(self.specs / name), "specs/" + name, base, list(events), "", [mount])
        return result

    @staticmethod
    def rules(result):
        return [item for item in result["items"] if item["kind"] == "rule"]

    def seed(self, b_body="The report page gains an export button."):
        self.write("onboarding.spec.html", spec(("acceptance-onboarding", ONBOARDING), ("local", LOCAL)))
        self.write("b.spec.html", spec(("b-one", "Old criterion."), body="Old body."))
        self.base = self.commit()
        self.write("b.spec.html", spec(("b-one", FEATURE), body=b_body))

    def test_first_failing_rule_item_targets_the_home_criterion(self):
        self.seed()
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.95)})
        rules = self.rules(self.read(self.service(provider)))
        shown = [item for item in rules if item["state"] == "label"]
        self.assertEqual(len(shown), 1)
        item = shown[0]
        self.assertEqual(item["label"], "missed")
        self.assertEqual(item["target"], "specs/onboarding.spec.html#acceptance-onboarding")
        self.assertEqual(item["id"], "acceptance")
        self.assertEqual(item["word"], "onboarding")
        self.assertEqual(item["text"], ONBOARDING)  # the served rule clause, in full (#mark-rule-text)

    def test_rule_miss_level_is_important_from_the_one_levels_table(self):
        # project-rules #mark-order, jev-suggestions #level-important: one MARK_LEVELS row, returned in /api/jev.
        self.seed()
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.95)})
        result = self.read(self.service(provider))
        self.assertEqual(result["levels"]["missed"], {"human": "important", "agent": "important"})
        self.assertEqual([(item.get("level"), item.get("agent_level")) for item in self.rules(result)], [("important", "important")])
        # the row alone sets it: changing the table changes the item
        with unittest.mock.patch.dict(jev.MARK_LEVELS, {"missed": {"human": "warning", "agent": "important"}}):
            result = self.read(self.service(provider))
        self.assertEqual((result["levels"]["missed"]["human"], self.rules(result)[0]["level"], self.rules(result)[0]["agent_level"]),
                         ("warning", "warning", "important"))
        # covered or pending notes carry no level
        self.write("b.spec.html", spec(("b-one", FEATURE), body="Covered body."))
        covered = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("covered", 0.95)})
        self.assertEqual([item.get("level") for item in self.rules(self.read(self.service(covered)))], [None])

    def test_derive_only_every_feature_criteria_are_rules_and_no_state_names_them(self):
        self.seed()
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95), LOCAL: ("this feature", 0.95)},
                                rule={ONBOARDING: ("missed", 0.95), LOCAL: ("missed", 0.95)})
        result = self.read(self.service(provider))
        self.assertEqual(result["rules"], ["specs/onboarding.spec.html#acceptance-onboarding"])
        self.assertEqual([item["target"] for item in self.rules(result)], result["rules"])
        # the rule check is asked only for the rule; B's own criterion is never a rule for B
        self.assertEqual([call["state"]["rule"] for call in provider.asked("rule")], [ONBOARDING])
        self.assertNotIn(FEATURE, [call["state"]["criterion"] for call in provider.asked("scope")])
        for path in (Path(self.tmp.name) / "state").rglob("*"):
            if path.is_file():
                self.assertNotIn(ONBOARDING, path.read_text(encoding="utf-8"))

    def test_spec_without_criteria_holds_no_rules(self):
        self.write("onboarding.spec.html", '<section data-anchor="s"><p data-anchor="p">' + ONBOARDING + '</p></section>')
        self.write("b.spec.html", spec(("b-one", "Old.")))
        self.base = self.commit()
        self.write("b.spec.html", spec(("b-one", FEATURE)))
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.95)})
        result = self.read(self.service(provider))
        self.assertEqual((self.rules(result), result["rules"]), ([], []))
        self.assertEqual(provider.asked("scope"), [])

    def test_reworded_or_deleted_rule_shows_no_mark_on_next_load(self):
        self.seed()
        reworded = "The report page adds its onboarding section."
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95), reworded: ("this feature", 0.95)},
                                rule={ONBOARDING: ("missed", 0.95)})
        service = self.service(provider)
        self.assertEqual(len([i for i in self.rules(self.read(service)) if i["state"] == "label"]), 1)
        self.write("onboarding.spec.html", spec(("acceptance-onboarding", reworded)))
        self.assertEqual(self.rules(self.read(service)), [])
        self.write("onboarding.spec.html", spec(("local", LOCAL)))
        result = self.read(service)
        self.assertEqual((self.rules(result), result["rules"]), ([], []))

    def test_scope_asked_once_across_heads_and_specs_and_rebuilt_after_records_deleted(self):
        self.seed()
        self.write("c.spec.html", spec(("c-one", "Another feature criterion."), body="C body."))
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.95)})
        service = self.service(provider)
        first = self.read(service)
        self.commit()  # new head, same criterion text
        self.write("b.spec.html", spec(("b-one", FEATURE), body="Changed again."))
        self.read(service)
        self.read(service, "c.spec.html")
        onboarding_scope = [c for c in provider.asked("scope") if c["state"]["criterion"] == ONBOARDING]
        self.assertEqual(len(onboarding_scope), 1)
        shutil.rmtree(Path(self.tmp.name) / "state")
        again = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.95)})
        rebuilt = self.read(self.service(again), base=self.base)
        self.assertEqual(rebuilt["rules"], first["rules"])

    def test_unsure_goes_once_to_general_llm_which_decides_and_is_reused(self):
        self.seed()
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.2)}, rule={ONBOARDING: ("covered", 0.2)},
                                general={("scope", ONBOARDING): "every feature", ("rule", ONBOARDING): "missed"})
        service = self.service(provider)
        result = self.read(service)
        onboarding = [i for i in self.rules(result) if i["target"].endswith("#acceptance-onboarding")]
        self.assertEqual([(i["state"], i["label"]) for i in onboarding], [("label", "missed")])
        self.assertFalse([i for i in result["items"] if i["state"] == "unsure"])
        # once per chain question: triggered? then covered?
        self.assertEqual((len(provider.general_asked("triggered")), len(provider.general_asked("covered"))), (1, 1))
        self.assertEqual(onboarding[0]["unsure"], 2)
        self.assertEqual({c["model"] for c in provider.general_calls}, {""})
        rule_call = provider.general_asked("covered")[0]
        schema = rule_call["response_format"]["json_schema"]
        self.assertTrue(schema["strict"] and rule_call["provider"]["require_parameters"])
        self.assertEqual(schema["schema"]["properties"]["choice"]["enum"], ["yes", "no"])
        records = [json.loads(line) for line in (Path(self.tmp.name) / "state" / "records.jsonl").read_text().splitlines()]
        decided = [r for r in records if r.get("escalated") and r["outcome"] == "shown"]
        self.assertTrue(decided and all(r["model"] != jev.MODEL for r in decided))
        calls = (len(provider.calls), len(provider.general_calls))
        reopened = self.service(provider)
        self.assertEqual(self.rules(self.read(reopened)), self.rules(result))
        self.assertEqual((len(provider.calls), len(provider.general_calls)), calls)

    def test_failed_rule_check_waits_its_pause_then_is_asked_again(self):
        """project-rules #q-fallback cites jev-suggestions #state-error: the one retry rule, never every load."""
        self.seed()
        now = [1000.0]
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.2)},
                                general={("rule", ONBOARDING): RuntimeError("down")})
        onboarding = lambda result: next(i for i in self.rules(result) if i["target"].endswith("#acceptance-onboarding"))
        general = lambda: len([c for c in provider.general_calls if json.loads(c["messages"][-1]["content"]).get("rule") == ONBOARDING])
        with unittest.mock.patch.object(jev, "_wall", lambda: now[0]):
            service = self.service(provider)
            self.assertEqual(onboarding(self.read(service))["state"], "unavailable")
            asked, jev_calls = general(), len(provider.asked("rule"))
            provider.general[("rule", ONBOARDING)] = "not triggered"
            now[0] += jev.RETRY_PAUSE - 1
            for _ in range(2):
                self.assertEqual(onboarding(self.read(service, settle=False))["state"], "unavailable")
            time.sleep(0.05)
            self.assertEqual(general(), asked)
            now[0] += 2
            started = time.time()
            self.assertEqual(onboarding(self.read(service, settle=False))["state"], "pending")
            self.assertLess(time.time() - started, 0.5)
            item = onboarding(self.read(service))
            self.assertEqual((item["state"], item["label"]), ("none", None))
            self.assertEqual(general(), asked + 1)
            self.assertEqual(len(provider.asked("rule")), jev_calls)  # Jev is not asked again, only the LLM

    def test_failed_rule_check_honors_the_provider_wait(self):
        self.seed()
        now = [1000.0]
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.2)},
                                general={("rule", ONBOARDING): jev.ProviderWait("rate limited", 300.0)})
        onboarding = lambda result: next(i for i in self.rules(result) if i["target"].endswith("#acceptance-onboarding"))
        with unittest.mock.patch.object(jev, "_wall", lambda: now[0]):
            service = self.service(provider)
            self.assertEqual(onboarding(self.read(service))["state"], "unavailable")
            asked = len(provider.general_calls)
            provider.general[("rule", ONBOARDING)] = "covered"
            now[0] += jev.RETRY_PAUSE + 1
            self.assertEqual(onboarding(self.read(service, settle=False))["state"], "unavailable")
            time.sleep(0.05)
            self.assertEqual(len(provider.general_calls), asked)
            now[0] += 300
            self.assertEqual(onboarding(self.read(service))["state"], "none")
            self.assertEqual(len(provider.general_calls), asked + 2)  # triggered retry + covered escalation

    def test_unavailable_scope_yields_no_rule_item_and_is_asked_again(self):
        self.seed()
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.2), LOCAL: ("this feature", 0.2)},
                                rule={ONBOARDING: ("missed", 0.95)},
                                general={("scope", ONBOARDING): RuntimeError("down"), ("scope", LOCAL): RuntimeError("down")})
        now = [1000.0]
        with unittest.mock.patch.object(jev, "_wall", lambda: now[0]):
            service = self.service(provider)
            self.assertEqual(self.rules(self.read(service)), [])
            provider.general.update({("scope", ONBOARDING): "every feature", ("scope", LOCAL): "this feature"})
            self.assertEqual(self.rules(self.read(service)), [])  # during its pause: nothing shown, nothing asked
            now[0] += jev.RETRY_PAUSE + 1
            rules = self.rules(self.read(service))
        self.assertEqual([(i["target"], i["state"]) for i in rules],
                         [("specs/onboarding.spec.html#acceptance-onboarding", "label")])

    def test_pending_says_whether_escalated(self):
        self.seed()
        gate = threading.Event()
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.2)},
                                general={("rule", ONBOARDING): "missed"}, gate=gate)
        service = self.service(provider)
        first = self.rules(self.read(service, settle=False))
        self.assertTrue(first and all(i["state"] == "pending" for i in first))
        self.assertFalse(first[0]["escalated"])
        deadline = time.time() + 5
        while time.time() < deadline:
            item = next(i for i in self.rules(self.read(service, settle=False)) if i["target"].endswith("#acceptance-onboarding"))
            if item.get("escalated"):
                break
            time.sleep(0.01)
        self.assertEqual((item["state"], item["escalated"]), ("pending", True))
        gate.set()
        item = next(i for i in self.rules(self.read(service)) if i["target"].endswith("#acceptance-onboarding"))
        self.assertEqual((item["state"], item["label"]), ("label", "missed"))
        self.assertNotIn("escalated", item)

    def test_cover_or_reason_line_clears_a_thread_alone_does_not_and_untriggered_never_shows(self):
        self.seed()
        reason = "Onboarding: not needed, this changes no screen."
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)},
                                rule={ONBOARDING: (lambda text: "covered" if reason in text else "missed", 0.95)})
        service = self.service(provider)
        thread = [{"name": "1", "actor": "human", "body": {"event": "comment", "id": "m1", "actor": "human",
                                                           "anchorId": "acceptance", "text": "Onboarding is not needed."}}]
        missed = [i for i in self.rules(self.read(service, events=thread)) if i["state"] == "label"]
        self.assertEqual(len(missed), 1)
        self.write("b.spec.html", spec(("b-one", FEATURE), ("b-reason", reason), body="The report page gains an export button."))
        self.assertFalse([i for i in self.rules(self.read(service)) if i["state"] == "label"])
        other = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("not triggered", 0.95)})
        self.assertFalse([i for i in self.rules(self.read(self.service(other, "other"))) if i["state"] == "label"])

    def test_unchanged_spec_is_not_checked(self):
        self.seed()
        self.base = self.commit()
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.95)})
        result = self.read(self.service(provider))
        self.assertEqual((self.rules(result), result["rules"]), ([], []))
        self.assertEqual(provider.asked("rule"), [])

    def test_rule_asks_share_the_one_ask_pool_and_stop_drops_them(self):
        """Rule and suggestion asks go through one pool (jev-suggestions #fast-marks-background, project-rules #pending)."""
        self.seed()
        gate = threading.Event()

        class Gated(FakeProvider):
            def decide(self, payload):
                gate.wait(10)
                return super().decide(payload)

        provider = Gated()
        with unittest.mock.patch.object(jev, "ASK_WORKERS", 1):
            service = self.service(provider)
        try:
            first = self.rules(self.read(service, settle=False))
            self.assertTrue(first and all(i["state"] == "pending" for i in first))
            queued = list(service._asking.values())
            self.assertGreater(len(queued), 1)
            self.assertFalse(hasattr(service, "_rule_pool"))
            stopper = threading.Thread(target=service.stop)
            stopper.start()
            deadline = time.time() + 10
            while not all(f.cancelled() or f.running() for f in queued) and time.time() < deadline:
                time.sleep(0.005)
        finally:
            gate.set()
        stopper.join(10)
        self.assertFalse(stopper.is_alive())
        self.assertEqual(len(provider.calls), 1)  # the in-flight scope ask; queued ones were dropped
        self.read(service, settle=False)
        time.sleep(0.05)
        self.assertEqual(len(provider.calls), 1)  # nothing asks after stop

    def test_explicit_every_feature_wording_asks_jev_which_decides_not_a_new_mechanism(self):
        """acceptance-explicit-rule: criteria with 'every change', 'any change', 'every feature' wording
        are scoped by the same scope question; Jev's answer decides; no new mechanism bypasses it."""
        ANY_CHANGE = "When any change adds a public endpoint, it ships its rate-limit test."
        EVERY_FEATURE_RULE = "Every feature that alters what a user sees ships with its accessibility check."
        self.write("onboarding.spec.html", spec(
            ("acceptance-onboarding", ONBOARDING),
            ("endpoints", ANY_CHANGE),
            ("accessibility", EVERY_FEATURE_RULE)))
        self.write("b.spec.html", spec(("b-one", "Old criterion."), body="Old body."))
        self.base = self.commit()
        self.write("b.spec.html", spec(("b-one", FEATURE), body="The report page gains an export button."))
        provider = FakeProvider(
            scope={ONBOARDING: ("every feature", 0.95),
                   ANY_CHANGE: ("every feature", 0.95),
                   EVERY_FEATURE_RULE: ("every feature", 0.95)},
            rule={ONBOARDING: ("missed", 0.95), ANY_CHANGE: ("covered", 0.95),
                  EVERY_FEATURE_RULE: ("missed", 0.95)})
        result = self.read(self.service(provider))
        # All three are rules; Jev's scope question decided each, no new mechanism bypassed it
        self.assertEqual(sorted(result["rules"]),
                         sorted(["specs/onboarding.spec.html#acceptance-onboarding",
                                 "specs/onboarding.spec.html#endpoints",
                                 "specs/onboarding.spec.html#accessibility"]))
        scope_criteria = {c["state"]["criterion"] for c in provider.asked("scope")}
        for criterion in (ONBOARDING, ANY_CHANGE, EVERY_FEATURE_RULE):
            self.assertIn(criterion, scope_criteria, "scope question asked Jev for each criterion")
        # Only missed rules show a mark; covered ones do not
        shown = [item for item in self.rules(result) if item["state"] == "label"]
        self.assertEqual(sorted(item["target"] for item in shown),
                         sorted(["specs/onboarding.spec.html#acceptance-onboarding",
                                 "specs/onboarding.spec.html#accessibility"]))

    def test_criterion_without_every_feature_wording_is_never_a_project_rule(self):
        """acceptance-no-false-rule: feature-local criteria, even with 'every' or 'all', are 'this feature'."""
        FEATURE_EVERY = "Every row of the billing table shows its due date and amount."
        FEATURE_ALL = "The validator checks all fields of the submitted form."
        self.write("billing.spec.html", spec(("billing-rows", FEATURE_EVERY), ("billing-validate", FEATURE_ALL)))
        self.write("b.spec.html", spec(("b-one", "Old criterion."), body="Old body."))
        self.base = self.commit()
        self.write("b.spec.html", spec(("b-one", FEATURE), body="The report page gains an export button."))
        provider = FakeProvider(
            scope={FEATURE_EVERY: ("this feature", 0.95), FEATURE_ALL: ("this feature", 0.95)},
            rule={FEATURE_EVERY: ("missed", 0.95), FEATURE_ALL: ("missed", 0.95)})
        result = self.read(self.service(provider))
        # Neither becomes a rule; Jev answered "this feature" for both
        self.assertEqual(result["rules"], [])
        self.assertEqual(self.rules(result), [])
        # Rule check was never asked (they are not rules)
        rule_texts = [c["state"]["rule"] for c in provider.asked("rule")]
        self.assertNotIn(FEATURE_EVERY, rule_texts)
        self.assertNotIn(FEATURE_ALL, rule_texts)

    def copies(self):
        """A home rule, two linked copies (one through the other), a diverging own rule (#q-copies)."""
        link = '<a href="onboarding.spec.html#acceptance-onboarding">onboarding</a>'
        self.write("onboarding.spec.html", spec(("acceptance-onboarding", ONBOARDING)))
        self.write("c.spec.html", spec(("c-rule", "Every feature adds onboarding, as " + link + " states.")))
        self.write("d.spec.html", spec(("d-rule", 'Every feature adds onboarding, per <a href="c.spec.html#c-rule">c</a>.')))
        self.write("e.spec.html", spec(("e-rule", "Every feature adds its own CI job.")))
        self.write("b.spec.html", spec(("b-one", "Old criterion."), body="Old body."))
        self.base = self.commit()
        self.write("b.spec.html", spec(("b-one", FEATURE), body="The report page gains an export button."))
        texts = [ONBOARDING, "Every feature adds onboarding, as onboarding states.",
                 "Every feature adds onboarding, per c.", "Every feature adds its own CI job."]
        return FakeProvider(scope={text: ("every feature", 0.95) for text in texts},
                            rule={text: ("missed", 0.95) for text in texts}), texts

    def test_linked_copies_collapse_to_their_home_rule(self):
        # project-rules #q-copies, #q-copies-diverge, #acceptance-copies, #proof-dismiss (copy part)
        provider, texts = self.copies()
        result = self.read(self.service(provider))
        home = "specs/onboarding.spec.html#acceptance-onboarding"
        self.assertEqual(result["rules"], sorted([home, "specs/e.spec.html#e-rule"]))
        items = {item["target"]: item for item in self.rules(result)}
        self.assertEqual(sorted(items), result["rules"])
        self.assertEqual((items[home]["word"], items[home]["text"], items[home]["state"]), ("onboarding", ONBOARDING, "label"))
        self.assertEqual(items["specs/e.spec.html#e-rule"]["text"], texts[3])
        # copies are checked with the home's text only, never their own
        self.assertEqual(sorted({call["state"]["rule"] for call in provider.asked("rule")}), sorted([ONBOARDING, texts[3]]))

    def test_link_to_a_criterion_that_is_no_rule_is_no_copy(self):
        provider, texts = self.copies()
        provider.scope[ONBOARDING] = ("this feature", 0.95)
        result = self.read(self.service(provider))
        # c stays its own rule; d, linking c, is c's copy
        self.assertEqual(result["rules"], ["specs/c.spec.html#c-rule", "specs/e.spec.html#e-rule"])
        self.assertEqual({item["target"]: item["text"] for item in self.rules(result)}["specs/c.spec.html#c-rule"], texts[1])

    def test_home_spec_shows_no_note_for_copies_of_its_own_rule(self):
        provider, texts = self.copies()
        service = self.service(provider)
        self.read(service)  # decides every scope, the home's included
        self.write("onboarding.spec.html", spec(("acceptance-onboarding", ONBOARDING), body="Changed."))
        result = self.read(service, "onboarding.spec.html")
        self.assertEqual(result["rules"], ["specs/e.spec.html#e-rule"])
        self.assertEqual([item["target"] for item in self.rules(result)], ["specs/e.spec.html#e-rule"])

    def test_off_returns_off(self):
        self.seed()
        service = jev.JevService(state_dir=Path(self.tmp.name) / "state", api_key="")
        self.assertEqual(self.read(service), {"jev": "off", "items": [], "levels": jev.MARK_LEVELS})


if __name__ == "__main__":
    unittest.main()
