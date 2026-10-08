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


def is_name(payload):
    return payload["response_format"]["json_schema"]["name"] == jev.RULE_NAME_SCHEMA


def name_of(text):
    """The fake general LLM's plain-words name for a rule (project-rules #card-name-source)."""
    return "Name: " + text[:24]


def general_kind(payload):
    if is_name(payload):
        return "name"
    question = json.loads(payload["messages"][0]["content"])["question"]
    return next(name for name, qset in SETS.items() if qset.instructions == question)



def record_lines(state):
    """Every line of the store's record files (jev-suggestions #record-store); one key's lines keep their order."""
    return [line for f in sorted((state / "records").glob("*.jsonl")) if f.name != "decisions.jsonl"
            for line in f.read_text().splitlines()]

class FakeProvider:
    """Jev answers from scope and rule maps; the general LLM from its own map; every payload is kept.
    A rule map value is the rule check's outcome, missed, covered, or not triggered, answered per chain question.
    coverage holds the (story anchor, criterion anchor) pairs that verify; every other pair is unrelated.
    draft maps a target clause's text to the draft-check label every changed clause gets against it."""

    def __init__(self, scope=None, rule=None, general=None, confidence=0.95, gate=None, coverage=(), draft=None):
        self.scope, self.rule, self.general = dict(scope or {}), dict(rule or {}), dict(general or {})
        self.draft = dict(draft or {})
        self.coverage = set(coverage)
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
        elif kind == "coverage":
            pair = tuple((state.get(side) or {}).get("anchor") for side in ("story", "criterion"))
            label, confidence = ("verifies" if pair in self.coverage else "unrelated"), self.confidence
        elif kind in ("about", "contradicts", "oversteps", "overlaps"):
            target = state.get("target", (state.get("second") or {}).get("text"))
            wanted = self.draft.get(target)
            label, confidence = ("yes" if wanted and kind in ("about", wanted) else "no"), 0.95
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
        if kind == "name":
            answer = self.general.get(("name", state["rule"]), name_of(state["rule"]))
            if isinstance(answer, BaseException):
                raise answer
            return {"model": payload["model"], "choices": [{"message": {"content": json.dumps({"name": answer})}}]}
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

    def service(self, provider, state="state", approve=(), project="alpha"):
        """A service whose project confirmed each text in approve as a rule (project-rules #approval)."""
        service = jev.JevService(state_dir=Path(self.tmp.name) / state, provider=provider, api_key="fake")
        self.approve(service, *approve, project=project)
        return service

    def approve(self, service, *texts, project="alpha", name=None):
        """The human's Confirm rule: the scope is answered first, as a candidate card needs."""
        for text in texts:
            service.seam.ask({"kind": "scope", "state": {"criterion": text}})
            self.assertTrue(service.confirm(self.mount(project), text, name), text)

    def mount(self, project=None):
        mount = {"slug": "", "root": str(self.root), "narrow_root": str(self.root / "docs")}
        return {**mount, "project": project} if project else mount

    def read(self, service, name="b.spec.html", base=None, events=(), settle=True, project="alpha"):
        mount = self.mount(project)
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

    @staticmethod
    def candidates(result):
        return [item for item in result["items"] if item["kind"] == "candidate"]

    def seed(self, b_body="The report page gains an export button."):
        self.write("onboarding.spec.html", spec(("acceptance-onboarding", ONBOARDING), ("local", LOCAL)))
        self.write("b.spec.html", spec(("b-one", "Old criterion."), body="Old body."))
        self.base = self.commit()
        self.write("b.spec.html", spec(("b-one", FEATURE), body=b_body))

    def test_first_failing_rule_item_targets_the_home_criterion(self):
        self.seed()
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.95)})
        rules = self.rules(self.read(self.service(provider, approve=(ONBOARDING,))))
        shown = [item for item in rules if item["state"] == "label"]
        self.assertEqual(len(shown), 1)
        item = shown[0]
        self.assertEqual(item["label"], "missed")
        self.assertEqual(item["target"], "specs/onboarding.spec.html#acceptance-onboarding")
        self.assertEqual(item["id"], "acceptance")
        self.assertEqual(item["word"], "onboarding")
        self.assertEqual(item["text"], ONBOARDING)  # the served rule clause, in full (#mark-rule-text)
        self.assertEqual(item["name"], name_of(ONBOARDING))  # written by the general LLM (#card-name-source)

    def test_rule_miss_level_is_important_from_the_one_levels_table(self):
        # project-rules #mark-order, jev-suggestions #level-important: one MARK_LEVELS row, returned in /api/jev.
        self.seed()
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.95)})
        result = self.read(self.service(provider, approve=(ONBOARDING,)))
        self.assertEqual(result["levels"]["missed"], {"human": "important", "agent": "important"})
        self.assertEqual([(item.get("level"), item.get("agent_level")) for item in self.rules(result)], [("important", "important")])
        # the row alone sets it: changing the table changes the item
        with unittest.mock.patch.dict(jev.MARK_LEVELS, {"missed": {"human": "warning", "agent": "important"}}):
            result = self.read(self.service(provider, "state"))
        self.assertEqual((result["levels"]["missed"]["human"], self.rules(result)[0]["level"], self.rules(result)[0]["agent_level"]),
                         ("warning", "warning", "important"))
        # covered or pending notes carry no level
        self.write("b.spec.html", spec(("b-one", FEATURE), body="Covered body."))
        covered = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("covered", 0.95)})
        self.assertEqual([item.get("level") for item in self.rules(self.read(self.service(covered, "covered", approve=(ONBOARDING,))))], [None])

    def test_derive_only_every_feature_criteria_are_rules_and_no_state_names_them(self):
        self.seed()
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95), LOCAL: ("this feature", 0.95)},
                                rule={ONBOARDING: ("missed", 0.95), LOCAL: ("missed", 0.95)})
        result = self.read(self.service(provider, approve=(ONBOARDING,)))
        self.assertEqual(result["rules"], ["specs/onboarding.spec.html#acceptance-onboarding"])
        self.assertEqual([item["target"] for item in self.rules(result)], result["rules"])
        # the rule check is asked only for the rule; B's own criterion is never a rule for B
        self.assertEqual([call["state"]["rule"] for call in provider.asked("rule")], [ONBOARDING])
        self.assertNotIn(FEATURE, [call["state"]["rule"] for call in provider.asked("rule")])
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
        # only B's own changed criterion is asked, for a candidate card on B (#acceptance-new-candidate)
        self.assertEqual([call["state"]["criterion"] for call in provider.asked("scope")], [FEATURE])

    def test_reworded_or_deleted_rule_shows_no_mark_on_next_load(self):
        self.seed()
        reworded = "The report page adds its onboarding section."
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95), reworded: ("this feature", 0.95)},
                                rule={ONBOARDING: ("missed", 0.95)})
        service = self.service(provider, approve=(ONBOARDING,))
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
        service = self.service(provider, approve=(ONBOARDING,))
        first = self.read(service)
        self.assertEqual(first["rules"], ["specs/onboarding.spec.html#acceptance-onboarding"])
        self.commit()  # new head, same criterion text
        self.write("b.spec.html", spec(("b-one", FEATURE), body="Changed again."))
        self.read(service)
        self.read(service, "c.spec.html")
        onboarding_scope = [c for c in provider.asked("scope") if c["state"]["criterion"] == ONBOARDING]
        self.assertEqual(len(onboarding_scope), 1)
        shutil.rmtree(Path(self.tmp.name) / "state")
        again = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.95)})
        rebuilt = self.read(self.service(again), base=self.base)
        # deleting the records forgets the confirmation: the rule is a candidate again (#q-disposable)
        self.assertEqual(rebuilt["rules"], [])
        self.assertEqual([i["target"] for i in self.candidates(rebuilt)], first["rules"])

    def test_unsure_goes_once_to_general_llm_which_decides_and_is_reused(self):
        self.seed()
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.2)}, rule={ONBOARDING: ("covered", 0.2)},
                                general={("scope", ONBOARDING): "every feature", ("rule", ONBOARDING): "missed"})
        service = self.service(provider, approve=(ONBOARDING,))
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
        records = [json.loads(line) for line in record_lines(Path(self.tmp.name) / "state")]
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
        general = lambda: len(provider.general_asked("triggered")) + len(provider.general_asked("covered"))
        with unittest.mock.patch.object(jev, "_wall", lambda: now[0]):
            service = self.service(provider, approve=(ONBOARDING,))
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
        checks = lambda: [c for c in provider.general_calls if not is_name(c)]
        with unittest.mock.patch.object(jev, "_wall", lambda: now[0]):
            service = self.service(provider, approve=(ONBOARDING,))
            self.assertEqual(onboarding(self.read(service))["state"], "unavailable")
            asked = len(checks())
            provider.general[("rule", ONBOARDING)] = "covered"
            now[0] += jev.RETRY_PAUSE + 1
            self.assertEqual(onboarding(self.read(service, settle=False))["state"], "unavailable")
            time.sleep(0.05)
            self.assertEqual(len(checks()), asked)
            now[0] += 300
            self.assertEqual(onboarding(self.read(service))["state"], "none")
            self.assertEqual(len(checks()), asked + 2)  # triggered retry + covered escalation

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
            self.assertEqual(self.rules(self.read(service)), [])  # every feature, but no human confirmed it
            self.approve(service, ONBOARDING)
            rules = self.rules(self.read(service))
        self.assertEqual([(i["target"], i["state"]) for i in rules],
                         [("specs/onboarding.spec.html#acceptance-onboarding", "label")])

    def test_pending_says_whether_escalated(self):
        self.seed()
        gate = threading.Event()
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.2)},
                                general={("rule", ONBOARDING): "missed"}, gate=gate)
        service = self.service(provider, approve=(ONBOARDING,))
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
        service = self.service(provider, approve=(ONBOARDING,))
        thread = [{"name": "1", "actor": "human", "body": {"event": "comment", "id": "m1", "actor": "human",
                                                           "anchorId": "acceptance", "text": "Onboarding is not needed."}}]
        missed = [i for i in self.rules(self.read(service, events=thread)) if i["state"] == "label"]
        self.assertEqual(len(missed), 1)
        self.write("b.spec.html", spec(("b-one", FEATURE), ("b-reason", reason), body="The report page gains an export button."))
        self.assertFalse([i for i in self.rules(self.read(service)) if i["state"] == "label"])
        other = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("not triggered", 0.95)})
        self.assertFalse([i for i in self.rules(self.read(self.service(other, "other", approve=(ONBOARDING,)))) if i["state"] == "label"])

    def test_unchanged_spec_is_not_checked(self):
        self.seed()
        self.base = self.commit()
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.95)})
        result = self.read(self.service(provider))
        self.assertEqual((self.rules(result), result["rules"]), ([], []))
        self.assertEqual(provider.asked("rule"), [])

    def test_unchanged_spec_asks_nothing_of_the_corpus(self):
        # project-rules #bootstrap-after: only new or changed criterion text is asked; an unchanged read asks nothing
        self.seed()
        self.base = self.commit()
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)})
        service = self.service(provider)
        for _ in range(3):
            self.read(service, settle=False)
        time.sleep(0.1)
        self.assertEqual((provider.calls, provider.general_calls), ([], []))

    def test_a_candidate_off_its_home_spec_asks_no_name(self):
        # project-rules #card-name-source: a name is written for a card a page renders; a candidate listed on
        # another spec is data for the agent read only, so it starts no general LLM call
        self.seed()
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)})
        service = self.service(provider)
        self.assertEqual([(i["id"], i["text"]) for i in self.candidates(self.read(service))], [(None, ONBOARDING)])
        self.read(service)
        time.sleep(0.1)
        self.assertEqual([c for c in provider.general_calls if is_name(c)], [])

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
            first = [i for i in self.read(service, settle=False)["items"] if i["kind"] in ("rule", "candidate")]
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
        result = self.read(self.service(provider, approve=(ONBOARDING, ANY_CHANGE, EVERY_FEATURE_RULE)))
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

    def test_fake_coverage_verifies_only_declared_pairs(self):
        # The one fake serves the QA fixture too: its declared story/criterion pairs verify, others stay unrelated.
        stories = ('<section data-spec-section="user-stories" data-anchor="user-stories">'
                   '<p data-user-story data-anchor="story-a">As a manager, I read totals.</p>'
                   '<p data-user-story data-anchor="story-b">As a manager, I export.</p></section>')
        self.write("b.spec.html", stories + spec(("crit-a", LOCAL), ("crit-b", FEATURE)))
        self.base = self.commit()
        provider = FakeProvider(coverage={("story-a", "crit-a"), ("story-b", "crit-b")})
        items = [item for item in self.read(self.service(provider))["items"] if item["kind"] == "coverage"]
        labels = {tuple(item["id"].split("::")): item.get("label") for item in items}
        self.assertEqual(labels, {("story-a", "crit-a"): "verifies", ("story-a", "crit-b"): "unrelated",
                                  ("story-b", "crit-a"): "unrelated", ("story-b", "crit-b"): "verifies"})

    def test_rules_come_only_from_the_page_project(self):
        """project-rules #proof-own-project, #acceptance-own-project: mounts of two projects, each with an
        every feature criterion; /api/jev for a spec of the first lists only the first project's rule."""
        OTHER_RULE = "Every feature ships with its audit log entry."
        self.seed()
        other = Path(self.tmp.name) / "other"
        (other / "docs" / "specs").mkdir(parents=True)
        git(other, "init", "-q")
        for name in ("rules.spec.html", "shared.spec.html"):
            (other / "docs" / "specs" / name).write_text(spec(("acceptance-audit", OTHER_RULE)), encoding="utf-8")
        git(other, "add", "-A")
        git(other, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qm", "c")
        git(other, "update-ref", "refs/remotes/origin/main", "HEAD")

        def row(root, slug, project, name):
            return {"slug": slug, "project": project, "root": str(root), "spec": "docs/specs/" + name,
                    "spec_file": str(Path(root) / "docs" / "specs" / name)}

        page = row(self.root, "ann1", "alpha", "b.spec.html")
        mounts = [page, row(self.root, "ann1", "alpha", "onboarding.spec.html"),
                  row(other, "ann1", "beta", "shared.spec.html"),  # a slug shared across projects
                  row(other, "ann2", "beta", "rules.spec.html")]
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95), OTHER_RULE: ("every feature", 0.95)},
                                rule={ONBOARDING: ("missed", 0.95), OTHER_RULE: ("missed", 0.95)})
        service = self.service(provider, approve=(ONBOARDING,))
        self.approve(service, OTHER_RULE, project="beta")
        approved = len(provider.asked("scope"))
        args = (page, str(self.specs / "b.spec.html"), "ann1/docs/specs/b.spec.html", self.base, [], "", mounts)
        result = service.response(*args)
        deadline = time.time() + 5
        while any(item["state"] == "pending" for item in result["items"]) and time.time() < deadline:
            time.sleep(0.01)
            result = service.response(*args)
        self.assertEqual(result["rules"], ["ann1/docs/specs/onboarding.spec.html#acceptance-onboarding"])
        self.assertNotIn(OTHER_RULE, [call["state"]["criterion"] for call in provider.asked("scope")[approved:]])
        self.assertEqual([item["path"] for item in service._served_specs(mounts, page["spec_file"], page)],
                         ["ann1/docs/specs/onboarding.spec.html"])

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
        result = self.read(self.service(provider, approve=(ONBOARDING, texts[3])))
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
        result = self.read(self.service(provider, approve=texts[1::2]))
        # c stays its own rule; d, linking c, is c's copy
        self.assertEqual(result["rules"], ["specs/c.spec.html#c-rule", "specs/e.spec.html#e-rule"])
        self.assertEqual({item["target"]: item["text"] for item in self.rules(result)}["specs/c.spec.html#c-rule"], texts[1])

    def test_home_spec_shows_no_note_for_copies_of_its_own_rule(self):
        provider, texts = self.copies()
        service = self.service(provider, approve=(ONBOARDING, texts[3]))
        self.read(service)  # decides every scope, the home's included
        self.write("onboarding.spec.html", spec(("acceptance-onboarding", ONBOARDING), body="Changed."))
        result = self.read(service, "onboarding.spec.html")
        self.assertEqual(result["rules"], ["specs/e.spec.html#e-rule"])
        self.assertEqual([item["target"] for item in self.rules(result)], ["specs/e.spec.html#e-rule"])

    def test_copies_collapse_across_per_spec_mounts_under_two_slugs(self):
        # project-rules #q-copies, #acceptance-copies, #dismiss-rule (every copy); jev-suggestions #corpus-others:
        # hosted mounts are one spec each under a lane slug, so a copy and its home are served under different slugs.
        provider, texts = self.copies()
        git(self.root, "update-ref", "refs/remotes/origin/main", self.base)

        def row(slug, name):
            return {"slug": slug, "project": "alpha", "root": str(self.root), "spec": "docs/specs/" + name,
                    "spec_file": str(self.specs / name)}

        lanes = {"b.spec.html": "ann1", "c.spec.html": "ann1", "onboarding.spec.html": "ann2",
                 "d.spec.html": "ann2", "e.spec.html": "ann2"}
        mounts = [row(slug, name) for name, slug in lanes.items()]
        service = self.service(provider, approve=(ONBOARDING, texts[3]))

        def read(name):
            page = row(lanes[name], name)
            args = (page, page["spec_file"], lanes[name] + "/docs/specs/" + name, self.base, [], "", mounts)
            result = service.response(*args)
            deadline = time.time() + 5
            while any(item["state"] == "pending" for item in result["items"]) and time.time() < deadline:
                time.sleep(0.01)
                result = service.response(*args)
            return result

        home, own = "ann2/docs/specs/onboarding.spec.html#acceptance-onboarding", "ann2/docs/specs/e.spec.html#e-rule"
        result = read("b.spec.html")
        self.assertEqual(result["rules"], sorted([home, own]))
        self.assertEqual(sorted(item["target"] for item in self.missed(result)), sorted([home, own]))
        # the home spec is never asked to cover its own rule through a copy served in another lane
        self.write("onboarding.spec.html", spec(("acceptance-onboarding", ONBOARDING), body="Changed."))
        self.assertEqual(read("onboarding.spec.html")["rules"], [own])
        # dismissing the home removes its copies in every lane
        self.assertTrue(service.dismiss(row("ann1", "b.spec.html"), str(self.specs / "b.spec.html"), "rule", ONBOARDING))
        self.assertEqual(read("b.spec.html")["rules"], [own])

    def records(self):
        return [json.loads(line) for line in record_lines(Path(self.tmp.name) / "state")]

    def missed(self, result):
        return [item for item in self.rules(result) if item["state"] == "label"]

    def test_not_here_hides_the_rule_on_that_spec_path_until_its_text_changes(self):
        # project-rules #dismiss-here, #dismiss-scope, #acceptance-dismiss, #acceptance-dismiss-return, #proof-dismiss
        self.seed()
        self.write("c.spec.html", spec(("c-one", "Another feature criterion."), body="C body."))
        reworded = "Every feature adds its onboarding section and a tour."
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95), reworded: ("every feature", 0.95)},
                                rule={ONBOARDING: ("missed", 0.95), reworded: ("missed", 0.95)})
        service = self.service(provider, approve=(ONBOARDING,))
        self.approve(service, ONBOARDING, project="beta")
        home = "specs/onboarding.spec.html#acceptance-onboarding"
        item = self.missed(self.read(service, project="alpha"))[0]
        mount, b = self.mount("alpha"), str(self.specs / "b.spec.html")
        self.assertTrue(service.dismiss(mount, b, "here", item["text"], item["record"]))
        result = self.read(service, project="alpha")
        self.assertEqual((self.rules(result), result["rules"]), ([], [home]))
        # stored on the note's rule-check record, by project, spec path, and rule text; unconfirmed; names no text
        resolved = [r for r in self.records() if r.get("resolution") and r["kind"] != "scope"]
        self.assertEqual([(r["record_id"], r["kind"], r["resolution"]["status"], r["resolution"]["project"],
                           r["resolution"]["spec"], "confirmed" in r) for r in resolved],
                         [(item["record"], "covered", "dismissed", "alpha", "docs/specs/b.spec.html", False)])
        self.assertNotIn(ONBOARDING, json.dumps(resolved))
        # survives an edit, a new head, and a restart; another spec missing the rule still shows it
        self.write("b.spec.html", spec(("b-one", FEATURE), body="Edited export body."))
        self.commit()
        self.write("b.spec.html", spec(("b-one", FEATURE), body="Edited again."))
        self.assertEqual(self.rules(self.read(self.service(provider), project="alpha")), [])
        self.assertEqual([i["target"] for i in self.missed(self.read(service, "c.spec.html", project="alpha"))], [home])
        # another project with the same text is unaffected
        self.assertEqual(len(self.missed(self.read(service, project="beta"))), 1)
        # a changed rule text is a new candidate; once confirmed, its miss shows again (#acceptance-dismiss-return)
        self.write("onboarding.spec.html", spec(("acceptance-onboarding", reworded), ("local", LOCAL)))
        self.assertEqual([i["text"] for i in self.candidates(self.read(service, project="alpha"))], [reworded])
        self.approve(service, reworded)
        self.assertEqual([i["text"] for i in self.missed(self.read(service, project="alpha"))], [reworded])

    def test_dismiss_rule_removes_the_rule_from_every_spec_of_that_project_only(self):
        # project-rules #dismiss-rule, #acceptance-dismiss-rule, #acceptance-dismiss-return, #proof-dismiss
        self.seed()
        self.write("c.spec.html", spec(("c-one", "Another feature criterion."), body="C body."))
        reworded = "Every feature adds its onboarding section and a tour."
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95), reworded: ("every feature", 0.95)},
                                rule={ONBOARDING: ("missed", 0.95), reworded: ("missed", 0.95)})
        service = self.service(provider, approve=(ONBOARDING,))
        self.approve(service, ONBOARDING, project="beta")
        item = self.missed(self.read(service, project="alpha"))[0]
        self.assertTrue(service.dismiss(self.mount("alpha"), str(self.specs / "b.spec.html"), "rule", item["text"]))
        for name in ("b.spec.html", "c.spec.html"):
            result = self.read(self.service(provider), name, project="alpha")
            self.assertEqual((self.rules(result), result["rules"]), ([], []))
        self.assertEqual(len(self.missed(self.read(service, project="beta"))), 1)
        # a confirmed scope label (#approval-labels)
        resolved = [r for r in self.records() if r.get("resolution", {}).get("status") == "not-a-rule"]
        self.assertEqual([(r["kind"], r["resolution"]["status"], r["resolution"]["project"], "spec" in r["resolution"],
                           r.get("confirmed")) for r in resolved], [("scope", "not-a-rule", "alpha", False, True)])
        self.assertNotIn(ONBOARDING, json.dumps(resolved))
        # both projects may dismiss the one scope record; each holds after a restart
        self.assertTrue(service.dismiss(self.mount("beta"), str(self.specs / "b.spec.html"), "rule", ONBOARDING))
        for project in ("alpha", "beta"):
            self.assertEqual(self.rules(self.read(self.service(provider), project=project)), [])
        self.write("onboarding.spec.html", spec(("acceptance-onboarding", reworded), ("local", LOCAL)))
        self.assertEqual([i["text"] for i in self.candidates(self.read(service, project="alpha"))], [reworded])
        self.approve(service, reworded)
        self.assertEqual([i["text"] for i in self.missed(self.read(service, project="alpha"))], [reworded])

    def test_dismiss_rule_on_a_home_covers_its_linked_copies(self):
        # project-rules #dismiss-rule (every copy), #acceptance-copies, #proof-dismiss
        provider, texts = self.copies()
        service = self.service(provider, approve=(ONBOARDING, texts[3]))
        items = {i["target"]: i for i in self.missed(self.read(service, project="alpha"))}
        home = items["specs/onboarding.spec.html#acceptance-onboarding"]
        self.assertTrue(service.dismiss(self.mount("alpha"), str(self.specs / "b.spec.html"), "rule", home["text"]))
        result = self.read(service, project="alpha")
        self.assertEqual(result["rules"], ["specs/e.spec.html#e-rule"])
        self.assertEqual([i["target"] for i in self.rules(result)], ["specs/e.spec.html#e-rule"])

    def test_dismiss_refuses_what_it_cannot_hold(self):
        self.seed()
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95), LOCAL: ("this feature", 0.95)},
                                rule={ONBOARDING: ("missed", 0.95)})
        service = self.service(provider, approve=(ONBOARDING,))
        item = self.missed(self.read(service, project="alpha"))[0]
        b = str(self.specs / "b.spec.html")
        scope = next(r["record_id"] for r in self.records() if r["kind"] == "scope")
        self.assertFalse(service.dismiss(self.mount(), b, "rule", ONBOARDING))  # no project
        self.assertFalse(service.dismiss(self.mount("alpha"), b, "rule", LOCAL))  # not a rule
        self.assertFalse(service.dismiss(self.mount("alpha"), b, "rule", "Unknown text."))
        self.assertFalse(service.dismiss(self.mount("alpha"), b, "here", ONBOARDING, scope))  # not a rule check
        self.assertFalse(service.dismiss(self.mount("alpha"), b, "here", ONBOARDING, "judgment-none"))
        self.assertFalse(service.dismiss(self.mount("alpha"), b, "other", ONBOARDING, item["record"]))
        self.assertFalse(service.dismiss(self.mount("alpha"), b, ["rule"], ONBOARDING))
        # no scope set loaded: refused, never a KeyError out of the record route
        bare = jev.JevService(state_dir=Path(self.tmp.name) / "state", provider=provider, api_key="fake")
        bare.seam.question_sets = {kind: qset for kind, qset in bare.seam.question_sets.items() if kind != "scope"}
        self.assertFalse(bare.dismiss(self.mount("alpha"), b, "rule", ONBOARDING))
        self.assertEqual({(r["record_id"], r["resolution"]["status"]) for r in self.records() if r.get("resolution")},
                         {(r["record_id"], "rule") for r in self.records() if r["kind"] == "scope" and r.get("answer", {}).get("label") == "every feature"})
        self.assertEqual(len(self.missed(self.read(service, project="alpha"))), 1)

    def test_first_failing_approval_candidate_until_confirmed(self):
        # project-rules #proof-approval, #acceptance-unconfirmed, #acceptance-confirm, #q-rule-def, #approval-labels
        self.seed()
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.95)})
        service = self.service(provider)
        home = "specs/onboarding.spec.html#acceptance-onboarding"
        result = self.read(service)
        # B lists A's criterion as a candidate, data only: its card shows on its home spec, never on B
        self.assertEqual([(i["target"], i["id"], i["text"], i["state"], i["label"], i["level"], i["agent_level"])
                          for i in self.candidates(result)],
                         [(home, None, ONBOARDING, "label", "candidate", "warning", "warning")])
        self.assertEqual(result["levels"]["candidate"], {"human": "warning", "agent": "warning"})
        # no rule item, no rule checked, and no spec is checked against it
        self.assertEqual((self.rules(result), result["rules"], provider.asked("rule")), ([], [], []))
        self.approve(service, ONBOARDING)
        result = self.read(service)
        self.assertEqual(self.candidates(result), [])
        self.assertEqual([(i["target"], i["label"]) for i in self.rules(result)], [(home, "missed")])
        self.assertEqual(result["rules"], [home])
        scope = [r for r in self.records() if r["kind"] == "scope" and r.get("resolution")][-1]
        self.assertEqual((scope["resolution"]["status"], scope["resolution"]["project"], scope["confirmed"]),
                         ("rule", "alpha", True))
        self.assertEqual(scope["resolution"]["rule"], jev.rule_identity(ONBOARDING))
        # a confirmation holds for its project only, and survives a restart
        self.assertEqual(self.rules(self.read(service, project="beta")), [])
        self.assertEqual(self.read(self.service(provider))["rules"], [home])

    def test_candidate_card_shows_on_its_home_criterion_with_a_written_name(self):
        # project-rules #acceptance-new-candidate, #mark-candidate, #card-name-source
        self.write("onboarding.spec.html", spec(("local", LOCAL)))
        self.write("b.spec.html", spec(("b-one", FEATURE)))
        self.base = self.commit()
        self.write("onboarding.spec.html", spec(("local", LOCAL), ("acceptance-onboarding", ONBOARDING)))
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.95)})
        service = self.service(provider)
        named = lambda: [c for c in provider.general_calls if is_name(c)]
        result = self.read(service, "onboarding.spec.html")
        deadline = time.time() + 5
        while not (self.candidates(result) and self.candidates(result)[0]["name"]) and time.time() < deadline:
            time.sleep(0.01)
            result = self.read(service, "onboarding.spec.html")
        item = self.candidates(result)[0]
        self.assertEqual((item["id"], item["target"], item["text"], item["name"], item["state"]),
                         ("acceptance-onboarding", "specs/onboarding.spec.html#acceptance-onboarding", ONBOARDING,
                          name_of(ONBOARDING), "label"))
        self.assertTrue(item["record"].startswith("judgment-"))
        # of this spec, only its changed criterion was asked; B is checked against nothing
        self.assertEqual(sorted(c["state"]["criterion"] for c in provider.asked("scope")), [ONBOARDING, FEATURE])
        self.assertEqual(self.read(service)["rules"], [])
        # named once per rule text, cached on the scope record across reads and a restart
        self.read(self.service(provider), "onboarding.spec.html")
        time.sleep(0.05)
        self.assertEqual(len(named()), 1)
        self.assertEqual(json.loads(named()[0]["messages"][-1]["content"]), {"rule": ONBOARDING})
        self.assertEqual({r["name"] for r in self.records() if r["kind"] == "scope" and "name" in r}, {name_of(ONBOARDING)})

    def test_confirm_takes_a_corrected_name_that_replaces_the_written_one(self):
        # project-rules #card-name-source, #approval-key
        self.seed()
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.95)})
        service = self.service(provider)
        self.approve(service, ONBOARDING, name="  Every screen ships   onboarding ")
        self.assertEqual([i["name"] for i in self.missed(self.read(service))], ["Every screen ships onboarding"])
        self.assertEqual([i["name"] for i in self.missed(self.read(self.service(provider)))], ["Every screen ships onboarding"])
        # a failed name write leaves the name absent, never an error
        self.write("onboarding.spec.html", spec(("acceptance-onboarding", ONBOARDING), ("x", "Every feature logs.")))
        failing = FakeProvider(scope={"Every feature logs.": ("every feature", 0.95)},
                               general={("name", "Every feature logs."): RuntimeError("down")})
        result = self.read(self.service(failing, "failing"))
        self.assertEqual([(i["text"], i["name"]) for i in self.candidates(result)], [("Every feature logs.", None)])

    def test_confirm_refuses_what_it_cannot_hold(self):
        self.seed()
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95), LOCAL: ("this feature", 0.95)})
        service = self.service(provider)
        self.read(service)
        b = self.mount("alpha")
        self.assertFalse(service.confirm(self.mount(), ONBOARDING))  # no project
        self.assertFalse(service.confirm(b, LOCAL))  # no candidate
        self.assertFalse(service.confirm(b, "Unknown text."))
        self.assertFalse(service.confirm(b, ["x"]))
        self.assertTrue(service.dismiss(b, str(self.specs / "b.spec.html"), "rule", ONBOARDING))
        self.assertFalse(service.confirm(b, ONBOARDING))  # decided: not a project rule
        self.assertEqual(self.candidates(self.read(service)), [])
        self.assertTrue(service.confirm(self.mount("beta"), ONBOARDING))
        self.assertFalse(service.confirm(self.mount("beta"), ONBOARDING))  # already confirmed: the first stands

    def test_not_a_project_rule_on_a_candidate_is_a_confirmed_label_and_no_candidate(self):
        # project-rules #approval-not, #approval-labels, #dismiss-rule
        self.seed()
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.95)})
        service = self.service(provider)
        self.assertEqual(len(self.candidates(self.read(service))), 1)
        self.assertTrue(service.dismiss(self.mount("alpha"), str(self.specs / "b.spec.html"), "rule", ONBOARDING))
        result = self.read(service)
        self.assertEqual((self.candidates(result), self.rules(result), result["rules"]), ([], [], []))
        scope = [r for r in self.records() if r["kind"] == "scope" and r.get("resolution")][-1]
        self.assertEqual((scope["resolution"]["status"], scope["confirmed"]), ("not-a-rule", True))

    def test_confirm_on_a_home_covers_its_copies_and_a_copy_is_never_a_candidate(self):
        # project-rules #q-copies
        provider, texts = self.copies()
        service = self.service(provider)
        result = self.read(service)
        self.assertEqual(sorted(i["target"] for i in self.candidates(result)),
                         ["specs/e.spec.html#e-rule", "specs/onboarding.spec.html#acceptance-onboarding"])
        self.approve(service, ONBOARDING)
        result = self.read(service)
        self.assertEqual(result["rules"], ["specs/onboarding.spec.html#acceptance-onboarding"])
        self.assertEqual([i["target"] for i in self.candidates(result)], ["specs/e.spec.html#e-rule"])

    def cites(self, approve=(ONBOARDING,), state="state"):
        self.seed()
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.95)},
                                draft={ONBOARDING: "contradicts"})
        service = self.service(provider, state, approve=approve)
        return service, provider

    @staticmethod
    def marks(result):
        return [i for i in result["items"] if i["kind"] == "corpus" and i["state"] == "label"]

    def test_mark_citing_a_confirmed_rule_carries_the_rule_card(self):
        # project-rules #card-cites, #acceptance-card-cites
        service, _ = self.cites()
        home = "specs/onboarding.spec.html#acceptance-onboarding"
        marks = self.marks(self.read(service))
        self.assertTrue(marks)
        self.assertEqual({(m["label"], m["target"]) for m in marks}, {("contradicts", home)})
        rule = marks[0]["rule"]
        self.assertEqual((rule["target"], rule["text"]), (home, ONBOARDING))
        self.assertIn(rule["name"], (None, name_of(ONBOARDING)))  # None only while the name is written
        # an unconfirmed candidate is no rule: the mark is a plain draft-check note
        plain, _ = self.cites(approve=(), state="plain")
        self.assertTrue(self.marks(self.read(plain)))
        self.assertTrue(all("rule" not in m for m in self.marks(self.read(plain))))

    def test_not_for_this_spec_on_a_cited_mark_removes_it_and_not_a_project_rule_leaves_a_plain_note(self):
        # project-rules #card-cites-reject
        service, _ = self.cites()
        b = str(self.specs / "b.spec.html")
        mark = self.marks(self.read(service))[0]
        self.assertTrue(service.dismiss(self.mount("alpha"), b, "here", ONBOARDING, mark["record"]))
        self.assertEqual(self.marks(self.read(service)), [])
        resolved = [r for r in self.records() if r.get("resolution", {}).get("status") == "dismissed"]
        self.assertEqual([(r["kind"], r["resolution"]["spec"], "confirmed" in r) for r in resolved],
                         [("contradicts", "docs/specs/b.spec.html", False)])
        other, _ = self.cites(state="other")
        self.assertTrue(other.dismiss(self.mount("alpha"), b, "rule", ONBOARDING))
        marks = self.marks(self.read(other))
        self.assertTrue(marks)
        self.assertTrue(all("rule" not in m for m in marks))

    def test_off_returns_off(self):
        self.seed()
        service = jev.JevService(state_dir=Path(self.tmp.name) / "state", api_key="")
        self.assertEqual(self.read(service), {"jev": "off", "items": [], "levels": jev.MARK_LEVELS})


if __name__ == "__main__":
    unittest.main()
