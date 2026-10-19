"""Project-wide rules behind fakes (project-rules #declare, #questions, #marks, #pending, #dismiss)."""

import importlib.util
import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import unittest.mock
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("review_serve_jev_rules_test", ROOT / "skill" / "review-spec" / "assets" / "jev.py")
jev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jev)
_serve_spec = importlib.util.spec_from_file_location("review_serve_jev_rules_serve", ROOT / "skill" / "review-spec" / "assets" / "review-serve.py")
serve = importlib.util.module_from_spec(_serve_spec)
_serve_spec.loader.exec_module(serve)

ONBOARDING = "Every change to what a user sees or does adds its onboarding section."
LOCAL = "The report page shows totals per week."
FEATURE = "The export button downloads a CSV of the current table."
RULE_NAME = "Every change ships its onboarding"  # the name the onboarding criterion's mark gives (#declare)
HOME = "specs/onboarding.spec.html#acceptance-onboarding"


def git(root, *args):
    return subprocess.check_output(("git", "-C", str(root), *args), text=True).strip()


def spec(*criteria, body=""):
    """A spec of these criteria, (anchor, text) or (anchor, text, rule name) for one its spec marks a project rule."""
    items = "".join(f'<p data-acceptance-criterion data-anchor="{anchor}"'
                    + (f' data-project-rule="{rest[0]}"' if rest else "") + f'>{text}</p>'
                    for anchor, text, *rest in criteria)
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



def record_lines(state):
    """Every line of the store's record files (jev-suggestions #record-store); one key's lines keep their order."""
    return [line for f in sorted((state / "records").glob("*/*.jsonl")) for line in f.read_text().splitlines()]


def ledger_lines(state):
    """Every warning ledger outcome (jev-suggestions #ledger), in order."""
    path = state / "ledger.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

class FakeProvider:
    """Jev answers from scope and rule maps; the general LLM from its own map; every payload is kept.
    A rule map value is the rule check's outcome, missed, covered, or not triggered, answered per chain question.
    coverage holds the (story anchor, criterion anchor) pairs that verify; every other pair is unrelated.
    draft maps a target clause's text to the draft-check label every changed clause gets against it.
    change maps a changed root section's current text to its change-type answer, yes or no; any other answers unrelated."""

    def __init__(self, scope=None, rule=None, general=None, confidence=0.95, gate=None, coverage=(), draft=None,
                 change=None):
        self.scope, self.rule, self.general = dict(scope or {}), dict(rule or {}), dict(general or {})
        self.draft, self.change = dict(draft or {}), dict(change or {})
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
        elif kind == "type":
            label, confidence = self.change.get(state.get("after"), "unrelated"), self.confidence
        elif kind in ("about", "contradicts", "oversteps", "overlaps"):
            target = state.get("target", (state.get("second") or {}).get("text"))
            wanted = self.draft.get(target)
            if wanted == "partial-overlap":  # overlaps below show_cutoff: the verifier answers partial (#level-partial)
                wanted = "overlaps"
                confidence = 0.8 if kind == "overlaps" else 0.95
            else:
                confidence = 0.95
            label = "yes" if wanted and kind in ("about", wanted) else "no"
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
        if kind == "overlaps":  # the overlaps verifier: partial, quoting both clauses
            answer = {"answer": "partial", "clause_span": state["clause"], "target_span": state["target"]}
            return {"model": payload["model"], "choices": [{"message": {"content": json.dumps(answer)}}]}
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
        service = jev.JevService(state_dir=Path(self.tmp.name) / state, provider=provider, api_key="fake")
        self.addCleanup(service.stop)
        return service

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

    def seed(self, b_body="The report page gains an export button.", marked=True):
        """onboarding marks its onboarding criterion a project rule (#declare); B, under shaping, adds a feature."""
        rule = ("acceptance-onboarding", ONBOARDING, RULE_NAME) if marked else ("acceptance-onboarding", ONBOARDING)
        self.write("onboarding.spec.html", spec(rule, ("local", LOCAL)))
        self.write("b.spec.html", spec(("b-one", "Old criterion."), body="Old body."))
        self.base = self.commit()
        self.write("b.spec.html", spec(("b-one", FEATURE), body=b_body))

    def test_first_failing_rule_item_targets_the_marked_criterion(self):
        """project-rules #proof: A marks a criterion; a fake answering missed for B gives one rule item targeting A's
        criterion under the mark's name; with the mark removed it gives none and asks nothing (#declare)."""
        self.seed()
        provider = FakeProvider(rule={ONBOARDING: ("missed", 0.95)})
        service = self.service(provider)
        rules = self.rules(self.read(service))
        self.assertEqual(len(rules), 1)
        item = rules[0]
        self.assertEqual((item["state"], item["label"], item["target"], item["id"], item["word"]),
                         ("label", "missed", HOME, "acceptance", "onboarding"))
        self.assertEqual(item["text"], ONBOARDING)  # the served rule clause, in full (#card)
        self.assertEqual(item["name"], RULE_NAME)  # the mark's name, never generated (#card-parts)
        self.assertEqual(item["home"], "docs/specs/onboarding.spec.html#acceptance-onboarding")
        # a marked criterion needs no scope answer: no scope question is asked of it, nor any name
        self.assertNotIn(ONBOARDING, [call["state"]["criterion"] for call in provider.asked("scope")])
        self.assertEqual(provider.general_calls, [])
        self.seed(marked=False)
        asked = len(provider.asked("rule"))
        result = self.read(service)
        self.assertEqual((self.rules(result), result["rules"]), ([], []))
        self.assertEqual(len(provider.asked("rule")), asked)

    def test_rule_miss_level_is_important_from_the_one_levels_table(self):
        # project-rules #mark-order, jev-suggestions #level-important: one MARK_LEVELS row, returned in /api/jev.
        self.seed()
        provider = FakeProvider(rule={ONBOARDING: ("missed", 0.95)})
        result = self.read(self.service(provider))
        self.assertEqual(result["levels"]["missed"], {"human": "important", "agent": "important"})
        self.assertEqual([(item.get("level"), item.get("agent_level")) for item in self.rules(result)], [("important", "important")])
        # the row alone sets it: changing the table changes the item
        with unittest.mock.patch.dict(jev.MARK_LEVELS, {"missed": {"human": "warning", "agent": "important"}}):
            result = self.read(self.service(provider, "state"))
        self.assertEqual((result["levels"]["missed"]["human"], self.rules(result)[0]["level"], self.rules(result)[0]["agent_level"]),
                         ("warning", "warning", "important"))
        # covered or pending notes carry no level
        self.write("b.spec.html", spec(("b-one", FEATURE), body="Covered body."))
        covered = FakeProvider(rule={ONBOARDING: ("covered", 0.95)})
        self.assertEqual([item.get("level") for item in self.rules(self.read(self.service(covered, "covered")))], [None])

    def test_derive_only_the_marked_criterion_is_a_rule_and_no_state_names_it(self):
        """project-rules #acceptance-derive: only the marked criterion is a rule; no list or state names it."""
        self.seed()
        provider = FakeProvider(scope={LOCAL: ("every feature", 0.95)},
                                rule={ONBOARDING: ("missed", 0.95), LOCAL: ("missed", 0.95)})
        service = self.service(provider)
        result = self.read(service)
        self.assertEqual(result["rules"], [HOME])
        self.assertEqual([item["target"] for item in self.rules(result)], result["rules"])
        # the rule check is asked only for the rule: an unmarked criterion is never checked, whatever its scope
        # answer, and B's own criterion is never a rule for B
        self.assertEqual([call["state"]["rule"] for call in provider.asked("rule")], [ONBOARDING])
        self.read(service, "onboarding.spec.html")
        self.assertNotIn(LOCAL, [call["state"]["rule"] for call in provider.asked("rule")])
        for path in (Path(self.tmp.name) / "state").rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                self.assertNotIn(ONBOARDING, text)
                self.assertNotIn(RULE_NAME, text)

    def test_spec_without_criteria_holds_no_rules(self):
        # a mark on anything but an acceptance criterion declares nothing (#declare-text)
        self.write("onboarding.spec.html", '<section data-anchor="s"><p data-anchor="p" data-project-rule="Onboarding">'
                   + ONBOARDING + '</p></section>')
        self.write("b.spec.html", spec(("b-one", "Old.")))
        self.base = self.commit()
        self.write("b.spec.html", spec(("b-one", FEATURE)))
        provider = FakeProvider(rule={ONBOARDING: ("missed", 0.95)})
        result = self.read(self.service(provider))
        self.assertEqual((self.rules(result), result["rules"]), ([], []))
        self.assertEqual(provider.asked("rule"), [])

    def test_unmarked_or_deleted_rule_shows_no_mark_on_next_load(self):
        # project-rules #acceptance-reword, #acceptance-reword-delete, #acceptance-reword-all, #acceptance-reword-delete-all,
        # #declare-change
        self.seed()
        provider = FakeProvider(rule={ONBOARDING: ("missed", 0.95)})
        service = self.service(provider)
        self.assertEqual(len(self.missed(self.read(service))), 1)
        self.write("onboarding.spec.html", spec(("acceptance-onboarding", ONBOARDING), ("local", LOCAL)))
        result = self.read(service)
        self.assertEqual((self.rules(result), result["rules"]), ([], []))
        self.write("onboarding.spec.html", spec(("local", LOCAL)))
        result = self.read(service)
        self.assertEqual((self.rules(result), result["rules"]), ([], []))
        # rewording a marked criterion changes the rule: its new text is checked
        reworded = "Every change to a screen adds its onboarding section."
        provider.rule[reworded] = ("missed", 0.95)
        self.write("onboarding.spec.html", spec(("acceptance-onboarding", reworded, RULE_NAME)))
        self.assertEqual([i["text"] for i in self.missed(self.read(service))], [reworded])

    def test_marking_a_missed_criterion_shows_its_rule_card(self):
        # project-rules #acceptance-mark-new, #acceptance-mark-new-checked: an unmarked every-feature criterion becomes a rule once marked
        self.seed(marked=False)
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.95)})
        service = self.service(provider)
        result = self.read(service)
        self.assertEqual((self.rules(result), result["rules"]), ([], []))
        self.seed()
        result = self.read(service)
        self.assertEqual([i["target"] for i in self.missed(result)], [HOME])
        self.assertEqual(result["rules"], [HOME])

    def test_rules_rebuild_from_the_marks_after_records_deleted(self):
        """project-rules #acceptance-cache-rebuild: with the Jev records deleted, the same rules are read from the marks."""
        self.seed()
        provider = FakeProvider(rule={ONBOARDING: ("missed", 0.95)})
        first = self.read(self.service(provider))
        self.assertEqual(first["rules"], [HOME])
        shutil.rmtree(Path(self.tmp.name) / "state")
        rebuilt = self.read(self.service(FakeProvider(rule={ONBOARDING: ("missed", 0.95)})))
        self.assertEqual(rebuilt["rules"], first["rules"])
        self.assertEqual([(i["target"], i["name"]) for i in self.missed(rebuilt)], [(HOME, RULE_NAME)])

    def test_unsure_settled_to_nothing_shown_is_not_counted(self):
        """jev-suggestions #markers-agent-unsure: an unsure answer the LLM settled to no mark carries no unsure count."""
        self.seed()
        for settled in ("covered", "not triggered"):
            with self.subTest(settled=settled):
                provider = FakeProvider(rule={ONBOARDING: ("covered", 0.2)}, general={("rule", ONBOARDING): settled})
                result = self.read(self.service(provider, state="state-" + settled))
                self.assertEqual([i["state"] for i in self.rules(result)], ["none"])
                self.assertTrue(provider.general_asked("triggered"))
                self.assertNotIn("unsure", self.rules(result)[0])

    def test_unsure_goes_once_to_general_llm_which_decides_and_is_reused(self):
        # project-rules #acceptance-fallback-miss, #acceptance-fallback-miss-recorded, #q-fallback
        self.seed()
        provider = FakeProvider(rule={ONBOARDING: ("covered", 0.2)}, general={("rule", ONBOARDING): "missed"})
        service = self.service(provider)
        result = self.read(service)
        self.assertEqual([(i["state"], i["label"]) for i in self.rules(result)], [("label", "missed")])
        self.assertFalse([i for i in result["items"] if i["state"] == "unsure"])
        # once per chain question: triggered? then covered?
        self.assertEqual((len(provider.general_asked("triggered")), len(provider.general_asked("covered"))), (1, 1))
        self.assertEqual(self.rules(result)[0]["unsure"], 2)
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
        provider = FakeProvider(rule={ONBOARDING: ("missed", 0.2)}, general={("rule", ONBOARDING): RuntimeError("down")})
        onboarding = lambda result: self.rules(result)[0]
        general = lambda: len(provider.general_asked("triggered")) + len(provider.general_asked("covered"))
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
        provider = FakeProvider(rule={ONBOARDING: ("missed", 0.2)},
                                general={("rule", ONBOARDING): jev.ProviderWait("rate limited", 300.0)})
        onboarding = lambda result: self.rules(result)[0]
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

    def test_unavailable_scope_shows_no_candidate_and_is_asked_again(self):
        # project-rules #q-fallback, #mark-none: an unsure scope shows no scope mark; after its pause it is asked again
        self.seed(marked=False)
        self.write("b.spec.html", spec(("b-one", FEATURE), ("b-every", ONBOARDING)))
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.2)},
                                general={("scope", ONBOARDING): RuntimeError("down")})
        now = [1000.0]
        shown = lambda result: [i for i in self.candidates(result) if i["state"] == "label" and i["id"]]
        with unittest.mock.patch.object(jev, "_wall", lambda: now[0]):
            service = self.service(provider)
            self.assertEqual(shown(self.read(service)), [])
            provider.general[("scope", ONBOARDING)] = "every feature"
            self.assertEqual(shown(self.read(service)), [])  # during its pause: nothing shown, nothing asked
            now[0] += jev.RETRY_PAUSE + 1
            self.assertEqual([i["id"] for i in shown(self.read(service))], ["b-every"])

    def test_pending_says_whether_escalated(self):
        self.seed()
        gate = threading.Event()
        provider = FakeProvider(rule={ONBOARDING: ("missed", 0.2)}, general={("rule", ONBOARDING): "missed"}, gate=gate)
        service = self.service(provider)
        first = self.rules(self.read(service, settle=False))
        self.assertTrue(first and all(i["state"] == "pending" for i in first))
        self.assertFalse(first[0]["escalated"])
        deadline = time.time() + 5
        while time.time() < deadline:
            item = self.rules(self.read(service, settle=False))[0]
            if item.get("escalated"):
                break
            time.sleep(0.01)
        self.assertEqual((item["state"], item["escalated"]), ("pending", True))
        gate.set()
        item = self.rules(self.read(service))[0]
        self.assertEqual((item["state"], item["label"]), ("label", "missed"))
        self.assertNotIn("escalated", item)

    def test_cover_or_reason_line_clears_a_thread_alone_does_not_and_untriggered_never_shows(self):
        # project-rules #acceptance-clear, #acceptance-clear-reason, #acceptance-clear-thread, #acceptance-clear-untriggered,
        # #mark-clear
        self.seed()
        reason = "Onboarding: not needed, this changes no screen."
        provider = FakeProvider(rule={ONBOARDING: (lambda text: "covered" if reason in text else "missed", 0.95)})
        service = self.service(provider)
        thread = [{"name": "1", "actor": "human", "body": {"event": "comment", "id": "m1", "actor": "human",
                                                           "anchorId": "acceptance", "text": "Onboarding is not needed."}}]
        self.assertEqual(len(self.missed(self.read(service, events=thread))), 1)
        self.write("b.spec.html", spec(("b-one", FEATURE), ("b-reason", reason), body="The report page gains an export button."))
        self.assertFalse(self.missed(self.read(service)))
        other = FakeProvider(rule={ONBOARDING: ("not triggered", 0.95)})
        self.assertFalse(self.missed(self.read(self.service(other, "other"))))

    def test_unchanged_spec_is_not_checked(self):
        self.seed()
        self.base = self.commit()
        provider = FakeProvider(rule={ONBOARDING: ("missed", 0.95)})
        result = self.read(self.service(provider))
        self.assertEqual((self.rules(result), result["rules"]), ([], []))
        self.assertEqual(provider.asked("rule"), [])

    def test_a_read_asks_scope_only_of_its_own_unmarked_criteria(self):
        # project-rules #q-scope: the spec under review's unmarked criteria; other specs' criteria are never asked
        self.seed()
        self.base = self.commit()
        provider = FakeProvider()
        service = self.service(provider)
        for _ in range(3):
            self.read(service)
        time.sleep(0.05)
        self.assertEqual([call["state"]["criterion"] for call in provider.asked("scope")], [FEATURE])
        self.assertEqual(provider.general_calls, [])

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
            stopper = threading.Thread(target=service.stop)
            stopper.start()
            deadline = time.time() + 10
            while not all(f.cancelled() or f.running() for f in queued) and time.time() < deadline:
                time.sleep(0.005)
        finally:
            gate.set()
        stopper.join(10)
        self.assertFalse(stopper.is_alive())
        self.assertEqual(len(provider.calls), 1)  # the in-flight ask; queued ones were dropped
        self.read(service, settle=False)
        time.sleep(0.05)
        self.assertEqual(len(provider.calls), 1)  # nothing asks after stop

    def test_every_marked_criterion_is_checked_and_only_misses_show(self):
        ANY_CHANGE = "When any change adds a public endpoint, it ships its rate-limit test."
        EVERY_FEATURE_RULE = "Every feature that alters what a user sees ships with its accessibility check."
        self.write("onboarding.spec.html", spec(
            ("acceptance-onboarding", ONBOARDING, RULE_NAME),
            ("endpoints", ANY_CHANGE, "Endpoints ship rate-limit tests"),
            ("accessibility", EVERY_FEATURE_RULE, "Screens ship accessibility checks")))
        self.write("b.spec.html", spec(("b-one", "Old criterion."), body="Old body."))
        self.base = self.commit()
        self.write("b.spec.html", spec(("b-one", FEATURE), body="The report page gains an export button."))
        provider = FakeProvider(rule={ONBOARDING: ("missed", 0.95), ANY_CHANGE: ("covered", 0.95),
                                      EVERY_FEATURE_RULE: ("missed", 0.95)})
        result = self.read(self.service(provider))
        self.assertEqual(result["rules"], sorted([HOME, "specs/onboarding.spec.html#endpoints",
                                                  "specs/onboarding.spec.html#accessibility"]))
        self.assertEqual(sorted((i["target"], i["name"]) for i in self.missed(result)),
                         [(HOME, RULE_NAME),
                          ("specs/onboarding.spec.html#accessibility", "Screens ship accessibility checks")])

    def test_fake_coverage_verifies_only_declared_pairs(self):
        # The one fake serves the QA fixture too: its verifying pairs verify, others stay unrelated. Jev asks only the
        # pairs each criterion's data-story names (jev-suggestions #coverage-declared): story-a with crit-b never.
        stories = ('<section data-spec-section="user-stories" data-anchor="user-stories">'
                   '<p data-user-story data-anchor="story-a">As a manager, I read totals.</p>'
                   '<p data-user-story data-anchor="story-b">As a manager, I export.</p></section>')
        self.write("b.spec.html", stories + spec(("crit-a", LOCAL), ("crit-b", FEATURE))
                   .replace('data-anchor="crit-a"', 'data-anchor="crit-a" data-story="story-a story-b"')
                   .replace('data-anchor="crit-b"', 'data-anchor="crit-b" data-story="story-b"'))
        self.base = self.commit()
        provider = FakeProvider(coverage={("story-a", "crit-a"), ("story-b", "crit-b")})
        items = [item for item in self.read(self.service(provider))["items"] if item["kind"] == "coverage"]
        labels = {tuple(item["id"].split("::")): item.get("label") for item in items}
        self.assertEqual(labels, {("story-a", "crit-a"): "verifies", ("story-b", "crit-a"): "unrelated",
                                  ("story-b", "crit-b"): "verifies"})
        self.assertEqual(len(provider.asked("coverage")), 3)

    def test_undeclared_spec_shows_change_types_and_no_coverage(self):
        """jev-suggestions #acceptance-coverage-undeclared: a changed spec with stories and criteria and no data-story
        reads its change types and asks and shows no coverage, so the page shows no coverage note."""
        stories = ('<section data-spec-section="user-stories" data-anchor="user-stories">'
                   '<p data-user-story data-anchor="story-a">As a manager, I read totals.</p></section>')
        self.write("b.spec.html", stories + spec(("crit-a", "Old criterion."), body="Old body."))
        self.base = self.commit()
        self.write("b.spec.html", stories + spec(("crit-a", FEATURE), body="The report page gains an export button."))

        class ChangesBehavior(FakeProvider):
            def decide(self, payload):
                if "type" in payload["questions"]:
                    with self.lock:
                        self.calls.append(payload)
                    return {"answers": {"type": {"choice": "yes", "confidence": 0.95}}}
                return super().decide(payload)

        provider = ChangesBehavior()
        items = self.read(self.service(provider))["items"]
        self.assertTrue([item for item in items if item["kind"] == "type" and item["label"] == "behavior"])
        self.assertEqual([item for item in items if item["kind"] == "coverage"], [])
        self.assertEqual(provider.asked("coverage"), [])

    def test_rules_come_only_from_the_page_project(self):
        """project-rules #proof own-project, #acceptance-own-project, #acceptance-own-project-read: mounts of two projects, each marking a rule;
        /api/jev for a spec of the first lists only the first project's rule."""
        OTHER_RULE = "Every feature ships with its audit log entry."
        self.seed()
        other = Path(self.tmp.name) / "other"
        (other / "docs" / "specs").mkdir(parents=True)
        git(other, "init", "-q")
        for name in ("rules.spec.html", "shared.spec.html"):
            (other / "docs" / "specs" / name).write_text(spec(("acceptance-audit", OTHER_RULE, "Audit log")), encoding="utf-8")
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
        provider = FakeProvider(rule={ONBOARDING: ("missed", 0.95), OTHER_RULE: ("missed", 0.95)})
        service = self.service(provider)
        args = (page, str(self.specs / "b.spec.html"), "ann1/docs/specs/b.spec.html", self.base, [], "", mounts)
        result = service.response(*args)
        deadline = time.time() + 5
        while any(item["state"] == "pending" for item in result["items"]) and time.time() < deadline:
            time.sleep(0.01)
            result = service.response(*args)
        self.assertEqual(result["rules"], ["ann1/docs/specs/onboarding.spec.html#acceptance-onboarding"])
        self.assertNotIn(OTHER_RULE, [call["state"]["rule"] for call in provider.asked("rule")])
        self.assertEqual([item["path"] for item in service._served_specs(mounts, page["spec_file"], page)],
                         ["ann1/docs/specs/onboarding.spec.html"])

    def records(self):
        return [json.loads(line) for line in record_lines(Path(self.tmp.name) / "state")]

    def ledger(self, state="state"):
        return ledger_lines(Path(self.tmp.name) / state)

    def missed(self, result):
        return [item for item in self.rules(result) if item["state"] == "label"]

    def b(self):
        return str(self.specs / "b.spec.html")

    def test_not_for_this_spec_hides_the_rule_on_that_spec_path_for_good(self):
        """project-rules #proof dismiss, #acceptance-dismiss, #acceptance-dismiss-read, #acceptance-dismiss-recorded,
        #acceptance-dismiss-other, #acceptance-dismiss-head, #dismiss-where."""
        self.seed()
        self.write("c.spec.html", spec(("c-one", "Another feature criterion."), body="C body."))
        provider = FakeProvider(rule={ONBOARDING: ("missed", 0.95)})
        service = self.service(provider)
        item = self.missed(self.read(service, project="alpha"))[0]
        self.assertTrue(service.dismiss(self.mount("alpha"), self.b(), "here", item["home"], item["record"]))
        result = self.read(service, project="alpha")
        self.assertEqual((self.rules(result), result["rules"]), ([], [HOME]))
        # one ledger outcome citing the note's rule-check record, by project, spec path, and the rule's home;
        # unconfirmed; names no text; no record amended
        resolved = [e for e in self.ledger() if e["outcome"] == "dismissed"]
        self.assertEqual([(e["judgment"], e["kind"], e["project"], e["spec"], e.get("anchor"), e["target"], e["by"],
                           e["confirmed"]) for e in resolved],
                         [(item["record"], "rule", "alpha", "docs/specs/b.spec.html", None,
                           "docs/specs/onboarding.spec.html#acceptance-onboarding", "human", False)])
        self.assertNotIn(ONBOARDING, json.dumps(resolved))
        self.assertFalse([r for r in self.records() if "resolution" in r or "confirmed" in r])
        # survives an edit, a new head, a reworded rule, and a restart; another spec missing the rule still shows it
        self.write("b.spec.html", spec(("b-one", FEATURE), body="Edited export body."))
        self.commit()
        self.write("b.spec.html", spec(("b-one", FEATURE), body="Edited again."))
        reworded = "Every change to a screen adds its onboarding section."
        provider.rule[reworded] = ("missed", 0.95)
        self.write("onboarding.spec.html", spec(("acceptance-onboarding", reworded, RULE_NAME)))
        self.assertEqual(self.rules(self.read(self.service(provider), project="alpha")), [])
        self.assertEqual([i["target"] for i in self.missed(self.read(service, "c.spec.html", project="alpha"))], [HOME])
        # another project is unaffected
        self.assertEqual(len(self.missed(self.read(service, project="beta"))), 1)

    def test_dismiss_refuses_what_it_cannot_hold(self):
        self.seed()
        self.write("b.spec.html", spec(("b-one", FEATURE), ("b-every", "Every feature logs its errors.")))
        provider = FakeProvider(scope={"Every feature logs its errors.": ("every feature", 0.95)},
                                rule={ONBOARDING: ("missed", 0.95)})
        service = self.service(provider)
        result = self.read(service, project="alpha")
        item = self.missed(result)[0]
        b, alpha = self.b(), self.mount("alpha")
        self.assertFalse(service.dismiss(self.mount(), b, "here", item["home"], item["record"]))  # no project
        self.assertFalse(service.dismiss(alpha, b, "here", item["home"]))  # names no record
        self.assertFalse(service.dismiss(alpha, b, "here", "no-anchor", item["record"]))  # names no rule clause
        self.assertFalse(service.dismiss(alpha, b, "other", item["home"], item["record"]))
        self.assertFalse(service.dismiss(alpha, b, ["here"], item["home"], item["record"]))
        self.assertFalse(service.dismiss(alpha, b, "rule", anchor="b-one"))  # no candidate: answered this feature
        self.assertFalse(service.dismiss(alpha, b, "rule", anchor="nope"))  # no such criterion
        self.assertFalse(service.dismiss(self.mount(), b, "rule", anchor="b-every"))  # no project
        self.assertEqual(self.ledger(), [])
        self.assertEqual(len(self.missed(self.read(service, project="alpha"))), 1)
        # no scope set loaded: refused, never a KeyError out of the record route
        bare = self.service(provider)
        bare.seam.question_sets = {kind: qset for kind, qset in bare.seam.question_sets.items() if kind != "scope"}
        self.assertFalse(bare.dismiss(alpha, b, "rule", anchor="b-every"))

    def candidate_spec(self, criterion="Every feature logs its errors.", label="every feature"):
        """B adds an unmarked criterion Jev reads at label (#q-scope)."""
        self.seed()
        self.write("b.spec.html", spec(("b-one", FEATURE), ("b-every", criterion), body="Body."))
        return FakeProvider(scope={criterion: (label, 0.95)}, rule={ONBOARDING: ("covered", 0.95), criterion: ("missed", 0.95)})

    def test_first_failing_candidate_until_settled(self):
        """project-rules #proof candidate, #acceptance-candidates, #acceptance-candidates-unchecked, #q-candidate: a candidate is flagged, never checked."""
        provider = self.candidate_spec()
        service = self.service(provider)
        result = self.read(service)
        items = self.candidates(result)
        self.assertEqual([(i["id"], i["target"], i["text"], i["state"], i["label"], i["level"], i["agent_level"])
                          for i in items],
                         [("b-every", "specs/b.spec.html#b-every", "Every feature logs its errors.", "label", "candidate",
                           "warning", "warning")])
        self.assertTrue(items[0]["record"].startswith("judgment-"))
        self.assertEqual(result["levels"]["candidate"], {"human": "warning", "agent": "warning"})
        # no spec is checked against a candidate: C, changed, checks only the marked rule
        self.write("c.spec.html", spec(("c-one", "Old.")))
        self.commit()
        self.write("c.spec.html", spec(("c-one", "C changed.")))
        self.base = git(self.root, "rev-parse", "HEAD")
        self.write("c.spec.html", spec(("c-one", "C changed again.")))
        self.assertEqual(self.read(service, "c.spec.html")["rules"], [HOME])
        self.assertEqual({call["state"]["rule"] for call in provider.asked("rule")}, {ONBOARDING})
        # another spec lists it for the agent read only: no id, so no page renders it
        self.assertEqual([(i["id"], i["target"]) for i in self.candidates(self.read(service, "c.spec.html"))],
                         [(None, "specs/b.spec.html#b-every")])

    def test_not_a_project_rule_settles_the_criterion_through_rewording(self):
        """project-rules #proof candidate, #acceptance-not-a-rule, #acceptance-not-a-rule-read, #acceptance-not-a-rule-recorded,
        #acceptance-not-a-rule-reworded, #dismiss-candidate."""
        provider = self.candidate_spec()
        service = self.service(provider)
        item = self.candidates(self.read(service))[0]
        self.assertTrue(service.dismiss(self.mount("alpha"), self.b(), "rule", anchor="b-every"))
        self.assertEqual(self.candidates(self.read(service)), [])
        self.assertEqual([(e["project"], e["spec"], e["anchor"], e["kind"], e["outcome"], e["judgment"], e["by"],
                           e["confirmed"]) for e in self.ledger()],
                         [("alpha", "docs/specs/b.spec.html", "b-every", "candidate", "not-a-rule", item["record"],
                           "human", True)])
        # another project's same spec path is unaffected
        self.assertEqual(len(self.candidates(self.read(service, project="beta"))), 1)
        # reworded and committed: never flagged again, and its new text is never asked
        reworded = "Every feature logs each of its errors."
        self.write("b.spec.html", spec(("b-one", FEATURE), ("b-every", reworded), body="Body."))
        self.commit()
        self.assertEqual(self.candidates(self.read(service)), [])
        self.assertNotIn(reworded, [call["state"]["criterion"] for call in provider.asked("scope")])
        self.assertEqual(len(self.ledger()), 1)

    def test_a_criterion_answered_not_a_rule_still_becomes_a_rule_once_marked(self):
        """project-rules #acceptance-not-a-rule-mark, #acceptance-scope-trace-marked."""
        provider = self.candidate_spec()
        service = self.service(provider)
        scope = self.candidates(self.read(service))[0]["record"]
        self.assertTrue(service.dismiss(self.mount("alpha"), self.b(), "rule", anchor="b-every"))
        self.write("b.spec.html", spec(("b-one", FEATURE), ("b-every", "Every feature logs its errors.", "Errors logged"),
                                       body="Body."))
        self.write("c.spec.html", spec(("c-one", "Old.")))
        self.base = self.commit()
        self.write("c.spec.html", spec(("c-one", "C changed.")))
        result = self.read(service, "c.spec.html")
        self.assertIn("specs/b.spec.html#b-every", [i["target"] for i in self.missed(result)])
        marked = [e for e in self.ledger() if e["outcome"] == "marked"]
        self.assertEqual([(e["spec"], e["anchor"], e["kind"], e["judgment"], e["by"], e["confirmed"]) for e in marked],
                         [("docs/specs/b.spec.html", "b-every", "candidate", scope, "server", True)])

    def test_marking_a_this_feature_criterion_records_one_marked_outcome_citing_its_scope_record(self):
        """project-rules #acceptance-scope-trace-marked, #dismiss-trace: a mark on a this feature answer is a miss."""
        provider = self.candidate_spec(label="this feature")
        service = self.service(provider)
        self.assertEqual(self.candidates(self.read(service)), [])
        self.write("b.spec.html", spec(("b-one", FEATURE), ("b-every", "Every feature logs its errors.", "Errors logged"),
                                       body="Body."))
        for _ in range(2):
            self.read(service)
            self.read(service, "onboarding.spec.html")
        marked = [e for e in self.ledger() if e["outcome"] == "marked"]
        self.assertEqual(len(marked), 1)
        records = {r["record_id"]: r for r in self.records()}
        self.assertEqual((marked[0]["anchor"], records[marked[0]["judgment"]]["kind"],
                          records[marked[0]["judgment"]]["answer"]["label"]), ("b-every", "scope", "this feature"))
        # a marked criterion with no scope answer cites nothing and records nothing
        self.assertFalse([e for e in self.ledger() if e["anchor"] == "acceptance-onboarding"])

    def test_accept_spec_with_an_open_candidate_records_it_ignored(self):
        """project-rules #acceptance-scope-trace-ignored."""
        provider = self.candidate_spec()
        service = self.service(provider)
        item = self.candidates(self.read(service))[0]
        service.accepted(self.mount("alpha"), self.b())
        service.accepted(self.mount("alpha"), self.b())  # the same outcome is never written twice
        self.assertEqual([(e["spec"], e["anchor"], e["kind"], e["outcome"], e["judgment"], e["confirmed"])
                          for e in self.ledger()],
                         [("docs/specs/b.spec.html", "b-every", "candidate", "ignored", item["record"], False)])
        # ignored settles nothing: the candidate still shows; a settled one is never ignored
        self.assertEqual(len(self.candidates(self.read(service))), 1)
        self.assertTrue(service.dismiss(self.mount("alpha"), self.b(), "rule", anchor="b-every"))
        service.accepted(self.mount("alpha"), self.b())
        self.assertEqual([e["outcome"] for e in self.ledger()], ["ignored", "not-a-rule"])
        # Jev off or no project: nothing is recorded, never an error
        jev.JevService(state_dir=Path(self.tmp.name) / "off", api_key="").accepted(self.mount("alpha"), self.b())
        service.accepted(self.mount(), self.b())
        self.assertEqual(len(self.ledger()), 2)

    def test_catch_up_skip_records_the_candidate_ignored_and_proposes_it_again(self):
        """repair-specs #catch-up-table, #acceptance-catch-up-skipped; project-rules #q-trace: catch-up passes one over."""
        provider = self.candidate_spec()
        service = self.service(provider)
        item = self.candidates(self.read(service))[0]
        alpha, b = self.mount("alpha"), self.b()
        self.assertFalse(service.dismiss(alpha, b, "ignored", anchor="b-one"))  # no candidate: answered this feature
        self.assertFalse(service.dismiss(alpha, b, "ignored", anchor="nope"))  # no such criterion
        self.assertFalse(service.dismiss(self.mount(), b, "ignored", anchor="b-every"))  # no project
        self.assertTrue(service.dismiss(alpha, b, "ignored", anchor="b-every"))
        self.assertTrue(service.dismiss(alpha, b, "ignored", anchor="b-every"))  # a second run writes nothing new
        self.assertEqual([(e["spec"], e["anchor"], e["kind"], e["outcome"], e["judgment"], e["by"], e["confirmed"])
                          for e in self.ledger()],
                         [("docs/specs/b.spec.html", "b-every", "candidate", "ignored", item["record"], "owner", False)])
        # ignored settles nothing: the next catch-up proposes it again
        self.assertEqual(len(self.candidates(self.read(service))), 1)
        # a settled candidate is never ignored: not-a-rule stays the latest, so it is never proposed again
        self.assertTrue(service.dismiss(alpha, b, "rule", anchor="b-every"))
        self.assertFalse(service.dismiss(alpha, b, "ignored", anchor="b-every"))
        self.assertEqual([e["outcome"] for e in self.ledger()], ["ignored", "not-a-rule"])
        self.assertEqual(self.candidates(self.read(service)), [])

    def test_scope_eval_reports_precision_and_recall_against_candidate_outcomes(self):
        """project-rules #acceptance-scope-eval: each outcome joined to its scope record."""
        criteria = {"b-tp": ("Every feature logs its errors.", "every feature", "marked"),
                    "b-fp": ("Every feature ships a tour.", "every feature", "not-a-rule"),
                    "b-fn": ("Every feature has a changelog line.", "this feature", "marked"),
                    "b-tn": ("The export names its file by date.", "this feature", None)}
        self.seed()
        self.write("b.spec.html", spec(("b-one", FEATURE), *[(a, t) for a, (t, _, _) in criteria.items()]))
        provider = FakeProvider(scope={t: (label, 0.95) for t, label, _ in criteria.values()})
        service = self.service(provider)
        self.read(service)
        self.assertTrue(service.dismiss(self.mount("alpha"), self.b(), "rule", anchor="b-fp"))
        self.write("b.spec.html", spec(("b-one", FEATURE), *[(a, t, "Name") if outcome == "marked" else (a, t)
                                                              for a, (t, _, outcome) in criteria.items()]))
        self.read(service)
        report = jev.scope_eval(service.seam.store.records(), service.seam.ledger)
        self.assertEqual((report["tp"], report["fp"], report["fn"]), (1, 1, 1))
        self.assertEqual((report["precision"], report["recall"]), (0.5, 0.5))
        joined = {outcome["anchor"]: record["answer"]["label"] for record, outcome in report["joined"]}
        self.assertEqual(joined, {"b-tp": "every feature", "b-fp": "every feature", "b-fn": "this feature"})

    def cites(self, marked=True, state="state", draft="contradicts"):
        self.seed(marked=marked)
        provider = FakeProvider(rule={ONBOARDING: ("missed", 0.95)}, draft={ONBOARDING: draft})
        return self.service(provider, state), provider

    @staticmethod
    def marks(result):
        return [i for i in result["items"] if i["kind"] == "corpus" and i["state"] == "label"]

    def test_mark_citing_a_marked_rule_carries_the_rule_card(self):
        # project-rules #card-variants, #acceptance-card-cites
        service, _ = self.cites()
        marks = self.marks(self.read(service))
        self.assertTrue(marks)
        self.assertEqual({(m["label"], m["target"]) for m in marks}, {("contradicts", HOME)})
        rule = marks[0]["rule"]
        self.assertEqual((rule["target"], rule["text"], rule["name"], rule["home"]),
                         (HOME, ONBOARDING, RULE_NAME, "docs/specs/onboarding.spec.html#acceptance-onboarding"))
        # an unmarked criterion is no rule: the mark is a plain draft-check note
        plain, _ = self.cites(marked=False, state="plain")
        self.assertTrue(self.marks(self.read(plain)))
        self.assertTrue(all("rule" not in m for m in self.marks(self.read(plain))))

    def test_partial_overlap_on_a_marked_rule_cites_no_rule(self):
        # project-rules #card-variants lists Contradicts, Oversteps, Overlaps only; jev-suggestions #level-partial
        service, _ = self.cites(draft="partial-overlap", state="partial")
        marks = self.marks(self.read(service))
        self.assertEqual({(m["label"], m["target"]) for m in marks}, {("partial-overlap", HOME)})
        self.assertTrue(all("rule" not in m for m in marks))

    def test_not_for_this_spec_on_a_cited_mark_removes_it(self):
        # project-rules #card-cites-reject: recorded for that spec path, the flagged clause, and the rule
        service, _ = self.cites()
        mark = self.marks(self.read(service))[0]
        self.assertTrue(service.dismiss(self.mount("alpha"), self.b(), "here", mark["rule"]["home"], mark["record"],
                                        anchor=mark["id"]))
        # gone on that clause; the other changed clause's mark is its own
        self.assertEqual([m["id"] for m in self.marks(self.read(service))], [m for m in ("b-one", "body") if m != mark["id"]])
        resolved = [e for e in self.ledger() if e["outcome"] == "dismissed"]
        self.assertEqual([(e["judgment"], e["spec"], e["anchor"], e["target"], e["confirmed"]) for e in resolved],
                         [(mark["record"], "docs/specs/b.spec.html", mark["id"], mark["rule"]["home"], False)])
        # the rule card on the heading is its own note: it still shows
        self.assertEqual(len(self.missed(self.read(service))), 1)

    def test_the_record_route_takes_both_card_answers_and_accept_spec_records_open_candidates_ignored(self):
        """project-rules #boundaries: the existing Jev record route takes Not for this spec and Not a project rule;
        Accept spec, the hand-off without a list, records each open candidate ignored (#dismiss-trace)."""
        provider = self.candidate_spec()
        mount = serve._single_mount(str(self.root / "docs"))
        service = serve.JevService(state_dir=Path(self.tmp.name) / "state", provider=provider, api_key="fake")
        self.addCleanup(service.stop)
        server = serve.ReviewThreadingHTTPServer(("127.0.0.1", 0), serve.MountHandler)
        server.mount_state, server.jev, server.state_dir = serve.MountState([mount]), service, None
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        url = "http://127.0.0.1:%d" % server.server_port

        def call(path, body=None):
            request = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(),
                                             method="GET" if body is None else "POST")
            try:
                with opener.open(request, timeout=5) as response:
                    return response.status, json.loads(response.read())
            except urllib.error.HTTPError as error:
                return error.code, json.loads(error.read())

        page = "?path=specs/b.spec.html&base=" + self.base
        deadline, items = time.time() + 5, []
        while not (items and all(i["state"] != "pending" for i in items)) and time.time() < deadline:
            items = [i for i in call("/api/jev" + page)[1]["items"] if i["kind"] in ("rule", "candidate")]
        self.assertEqual(sorted((i["kind"], i["id"]) for i in items), [("candidate", "b-every"), ("rule", "acceptance")])
        rule = next(i for i in items if i["kind"] == "rule")
        self.assertEqual(call("/api/jev/offer" + page, {"dismiss": "here", "home": rule["home"]})[0], 400)  # no record
        self.assertEqual(call("/api/jev/offer" + page, {"dismiss": "rule", "anchor": "b-one"})[0], 400)  # no candidate
        self.assertEqual(call("/api/jev/offer" + page, {"dismiss": "ignored", "anchor": "b-one"})[0], 400)  # no candidate
        self.assertEqual(call("/api/jev/offer" + page, {"offer": "dismissed"})[0], 400)  # no offers
        self.assertEqual(call("/api/jev/offer" + page, {"confirm": True, "rule": ONBOARDING})[0], 400)  # no Confirm rule
        self.assertEqual(call("/api/jev/offer" + page, {"dismiss": "here", "home": rule["home"], "record": rule["record"]}),
                         (200, {"ok": True}))
        # Accept spec, from the page: one ignored outcome for the open candidate
        review = "/specs/b.spec.html.review"
        accept = {"id": "h1", "event": "handoff", "anchorId": "", "target": None, "quote": None, "text": "batch",
                  "actor": "human", "createdAt": "2026-10-01T00:00:00Z", "schemaVersion": 1}
        self.assertEqual(call("/api/events?dir=" + review + "&actor=human", accept)[0], 200)
        # a hand-off listing drafts is no Accept spec
        self.assertEqual(call("/api/events?dir=" + review + "&actor=human", {**accept, "id": "h2", "events": []})[0], 200)
        self.assertEqual(call("/api/jev/offer" + page, {"dismiss": "rule", "anchor": "b-every"}), (200, {"ok": True}))
        self.assertEqual([(e["kind"], e.get("anchor"), e["outcome"]) for e in self.ledger()],
                         [("rule", None, "dismissed"), ("candidate", "b-every", "ignored"),
                          ("candidate", "b-every", "not-a-rule")])
        self.assertEqual([i["kind"] for i in call("/api/jev" + page)[1]["items"] if i["kind"] in ("rule", "candidate")], [])

    def test_off_returns_off(self):
        self.seed()
        service = jev.JevService(state_dir=Path(self.tmp.name) / "state", api_key="")
        self.assertEqual(self.read(service), {"jev": "off", "items": [], "levels": jev.MARK_LEVELS})

    # An Important mark's resolution on its record (jev-suggestions #record-resolution, #acceptance-resolution,
    # #acceptance-resolution-fixed; project-rules #acceptance-fixed-unconfirmed).

    def outcomes(self, service, record_id):
        """The warning ledger outcomes citing this record (jev-suggestions #ledger)."""
        return [e for e in service.seam.ledger.entries() if e["judgment"] == record_id]

    def outcome(self, service, record_id):
        found = self.outcomes(service, record_id)
        return found[-1] if found else None

    def where(self, item, project="alpha"):
        return (project, "docs/specs/b.spec.html", item["id"], item["kind"], item.get("target"))

    def shown(self, service):
        self.seed()
        item = self.missed(self.read(service))[0]
        self.assertEqual(item["agent_level"], "important")
        return item

    def test_owner_dismissal_with_reason_is_dismissed_unconfirmed_and_not_a_replay_label(self):
        # jev-suggestions #acceptance-resolution, #proof-resolution
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.95)})
        service = self.service(provider)
        item = self.shown(service)
        mount, b = self.mount("alpha"), str(self.specs / "b.spec.html")
        before = record_lines(Path(self.tmp.name) / "state")
        # an owner dismissal carries its reason; unknown marks and resolutions are refused
        self.assertFalse(service.resolve(mount, b, item["record"], "dismissed"))
        self.assertFalse(service.resolve(mount, b, item["record"], "rule", "no"))
        self.assertFalse(service.resolve(mount, b, item["record"], "thread"), "a thread is no owner resolution")
        self.assertFalse(service.resolve(mount, b, "judgment-nope", "fixed"))
        self.assertFalse(service.resolve(mount, str(self.specs / "c.spec.html"), item["record"], "fixed"))
        self.assertTrue(service.resolve(mount, b, item["record"], "dismissed", "The export is a download, no screen."))
        outcome = service.seam.ledger.latest(*self.where(item))
        self.assertEqual((outcome["outcome"], outcome["reason"], outcome["judgment"], outcome["by"], outcome["confirmed"]),
                         ("dismissed", "The export is a download, no screen.", item["record"], "owner", False))
        self.assertEqual(record_lines(Path(self.tmp.name) / "state"), before, "the record is unchanged")
        labels = jev.eval_read(service.seam.store.records(), service.seam.ledger, confirmed=True)
        self.assertNotIn(item["record"], [record["record_id"] for record, _ in labels])
        # one resolution per mark: a later one is refused and the first stands
        self.assertFalse(service.resolve(mount, b, item["record"], "reason", "Onboarding: not needed"))
        self.assertEqual(self.outcome(service, item["record"])["outcome"], "dismissed")
        self.assertEqual(len(self.outcomes(service, item["record"])), 1)

    def test_an_owner_dismissal_survives_deleting_the_records(self):
        # jev-suggestions #acceptance-ledger-wipe, #ledger-survives, #record-disposable
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.95)})
        service = self.service(provider)
        item = self.shown(service)
        b = str(self.specs / "b.spec.html")
        self.assertTrue(service.resolve(self.mount("alpha"), b, item["record"], "dismissed", "Not a screen."))
        service.stop()
        shutil.rmtree(Path(self.tmp.name) / "state" / "records")
        again = jev.JevService(state_dir=Path(self.tmp.name) / "state", provider=provider, api_key="fake")
        self.addCleanup(again.stop)
        shown = self.missed(self.read(again))[0]
        self.assertNotEqual(shown["record"], item["record"], "the answer was asked again")
        self.assertEqual(again.seam.ledger.latest(*self.where(shown))["outcome"], "dismissed")
        self.assertFalse(again.resolve(self.mount("alpha"), b, shown["record"], "fixed"), "the mark stays dismissed")
        self.assertEqual([e["outcome"] for e in self.ledger()], ["dismissed"])

    def test_reason_and_fixed_resolutions_are_recorded_unconfirmed(self):
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.95)})
        service = self.service(provider)
        item = self.shown(service)
        b = str(self.specs / "b.spec.html")
        self.assertTrue(service.resolve(self.mount("alpha"), b, item["record"], "reason", "Onboarding: not needed"))
        outcome = self.outcome(service, item["record"])
        self.assertEqual((outcome["outcome"], outcome["reason"], outcome["confirmed"]),
                         ("reason", "Onboarding: not needed", False))
        service2 = self.service(provider, state="state2")
        item = self.missed(self.read(service2))[0]
        self.assertTrue(service2.resolve(self.mount("alpha"), b, item["record"], "fixed"))
        outcome = self.outcome(service2, item["record"])
        self.assertEqual((outcome["outcome"], outcome["by"], outcome["confirmed"]), ("fixed", "owner", False))

    def test_a_spec_fix_that_clears_the_mark_on_the_next_head_records_fixed_unconfirmed(self):
        # jev-suggestions #acceptance-resolution-fixed, #proof-resolution
        covered = lambda text: "covered" if "onboarding section" in text else "missed"
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: (covered, 0.95)})
        service = self.service(provider)
        item = self.shown(service)
        # a mark still shown on the next read is not fixed
        self.assertEqual(self.missed(self.read(service))[0]["record"], item["record"])
        self.assertIsNone(self.outcome(service, item["record"]))
        self.write("b.spec.html", spec(("b-one", FEATURE + " It adds its onboarding section."), body="Body."))
        self.commit()
        self.assertEqual(self.missed(self.read(service)), [])
        outcome = service.seam.ledger.latest(*self.where(item))
        self.assertEqual((outcome["outcome"], outcome["judgment"], outcome["by"], outcome["confirmed"]),
                         ("fixed", item["record"], "server", False))
        # the eval read scores only confirmed labels: an audit's label counts
        labels = lambda: [r["record_id"] for r, _ in jev.eval_read(service.seam.store.records(), service.seam.ledger,
                                                                    confirmed=True)]
        self.assertNotIn(item["record"], labels())
        service.seam.ledger.append({**outcome, "by": "audit"})
        self.assertIn(item["record"], labels())

    def test_an_owner_resolved_or_human_dismissed_mark_is_not_overwritten_as_fixed(self):
        covered = lambda text: "covered" if "onboarding section" in text else "missed"
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: (covered, 0.95)})
        service = self.service(provider)
        item = self.shown(service)
        self.assertTrue(service.resolve(self.mount("alpha"), str(self.specs / "b.spec.html"), item["record"], "reason",
                                        "Onboarding: not needed"))
        self.write("b.spec.html", spec(("b-one", FEATURE + " It adds its onboarding section."), body="Body."))
        self.assertEqual(self.missed(self.read(service)), [])
        self.assertEqual([e["outcome"] for e in self.outcomes(service, item["record"])], ["reason"])
        # the human's Not for this spec hides the mark at once; it stays dismissed, never fixed
        service = self.service(provider, state="state2")
        self.write("b.spec.html", spec(("b-one", FEATURE), body="Body again."))
        item = self.missed(self.read(service))[0]
        self.assertTrue(service.dismiss(self.mount("alpha"), str(self.specs / "b.spec.html"), "here", item["home"], item["record"]))
        self.assertEqual(self.missed(self.read(service)), [])
        self.assertEqual([e["outcome"] for e in self.outcomes(service, item["record"])], ["dismissed"])

    def test_a_miss_gone_because_its_rule_was_unmarked_is_not_fixed(self):
        # jev-suggestions #record-resolution: a mark gone because its rule is no project rule now is not fixed
        provider = FakeProvider(rule={ONBOARDING: ("missed", 0.95)})
        service = self.service(provider)
        item = self.shown(service)
        self.write("onboarding.spec.html", spec(("acceptance-onboarding", ONBOARDING), ("local", LOCAL)))
        self.commit()
        self.assertEqual(self.missed(self.read(service)), [])
        self.assertIsNone(self.outcome(service, item["record"]))

    def fixed(self, service, items, head, base="b1"):
        service._fixed(service.seam, self.mount("alpha"), "specs/b.spec.html", {"head": head, "base_commit": base}, items,
                       {HOME})

    def test_a_mark_gone_while_its_question_is_undecided_is_not_fixed(self):
        # a read with a question pending, unavailable, or oversize decides nothing
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.95)})
        service = self.service(provider)
        item = self.shown(service)
        self.fixed(service, [item], "h1")
        for state in ("unavailable", "pending", "oversize"):
            undecided = {**item, "state": state, "label": None, "record": None, "level": None, "agent_level": None}
            self.fixed(service, [undecided], "h2")
            self.assertIsNone(self.outcome(service, item["record"]), state)
        self.fixed(service, [], "h2")
        self.assertEqual(self.outcome(service, item["record"])["outcome"], "fixed")

    def test_a_mark_gone_on_the_same_head_or_another_base_is_not_fixed(self):
        # review B1 (a): no edit, so no fix; the owner's later dismissal still records
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.95)})
        service = self.service(provider)
        item = self.shown(service)
        self.fixed(service, [item], "h1")
        self.fixed(service, [], "h1")
        self.fixed(service, [], "h2", base="b2")
        self.assertIsNone(self.outcome(service, item["record"]))
        self.assertTrue(service.resolve(self.mount("alpha"), str(self.specs / "b.spec.html"), item["record"], "dismissed", "Jev is wrong here."))

    def test_a_mark_cleared_in_the_working_tree_is_fixed_on_the_next_head(self):
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.95)})
        service = self.service(provider)
        item = self.shown(service)
        self.fixed(service, [item], "h1")
        self.fixed(service, [], "h1")
        self.assertIsNone(self.outcome(service, item["record"]))
        self.fixed(service, [], "h2")
        self.assertEqual(self.outcome(service, item["record"])["outcome"], "fixed")

    def test_a_mark_still_shown_on_a_new_record_is_not_fixed(self):
        # review B1 (b): a reworded clause asks again; the same mark on its new record is no fix
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.95)})
        service = self.service(provider)
        item = self.shown(service)
        self.fixed(service, [item], "h1")
        self.fixed(service, [{**item, "record": "judgment-reworded"}], "h2")
        self.assertIsNone(self.outcome(service, item["record"]))

    def test_an_oversize_question_leaves_the_mark_undecided_through_the_read(self):
        # review B1 (c): an oversize question is skipped from the page, never fixed
        covered = lambda text: "covered" if "onboarding section" in text else "missed"
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: (covered, 0.95)})
        service = self.service(provider)
        item = self.shown(service)
        self.commit()
        oversize = lambda chain, final: {"outcome": "oversize", "unsure": 0}
        with unittest.mock.patch.object(jev, "chain_result", side_effect=oversize):
            self.assertEqual(self.missed(self.read(service, settle=False)), [])
        self.assertIsNone(self.outcome(service, item["record"]))

    def test_a_fixed_write_that_fails_does_not_fail_the_read(self):
        covered = lambda text: "covered" if "onboarding section" in text else "missed"
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: (covered, 0.95)})
        service = self.service(provider)
        self.shown(service)
        self.write("b.spec.html", spec(("b-one", FEATURE + " It adds its onboarding section."), body="Body."))
        self.commit()
        with unittest.mock.patch.object(service.seam.ledger, "append", side_effect=OSError("disk")):
            self.assertEqual(self.missed(self.read(service)), [])

    def test_a_mark_gone_while_rules_cannot_be_read_is_not_fixed(self):
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.95)})
        service = self.service(provider)
        item = self.shown(service)
        real, calls = service._read_facts, []

        def busy_once(*args):
            calls.append(args)
            if len(calls) == 1:
                raise RuntimeError("busy")
            return real(*args)

        with unittest.mock.patch.object(service, "_read_facts", side_effect=busy_once):
            self.assertEqual(self.missed(self.read(service, settle=False)), [])  # rules unread, questions read
        self.assertIsNone(self.outcome(service, item["record"]))
        # rules that fail to build on a new head decide nothing either
        self.commit()
        with unittest.mock.patch.object(service, "rule_items", side_effect=RuntimeError("busy")):
            self.assertEqual(self.missed(self.read(service, settle=False)), [])
        self.assertIsNone(self.outcome(service, item["record"]))


if __name__ == "__main__":
    unittest.main()
