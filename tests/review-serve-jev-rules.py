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
_spec = importlib.util.spec_from_file_location("review_serve_jev_rules_test", ROOT / "tools" / "jev.py")
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


class FakeProvider:
    """Jev answers from scope and rule maps; the general LLM from its own map; every payload is kept."""

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
        elif kind == "rule":
            label, confidence = self.rule.get(state["rule"], ("not triggered", self.confidence))
            if callable(label):
                label = label(state["spec"])
        else:
            label, confidence = "unrelated", 0.95
        return {"answers": {kind: {"choice": label, "confidence": confidence}}}

    def complete(self, payload):
        if self.gate is not None:
            self.gate.wait(5)
        with self.lock:
            self.general_calls.append(payload)
        state = json.loads(payload["messages"][-1]["content"])
        kind = "scope" if "criterion" in state else "rule"
        answer = self.general.get((kind, state.get("criterion", state.get("rule"))))
        if isinstance(answer, list):  # one answer per call, the last repeating
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if isinstance(answer, BaseException):
            raise answer
        return {"model": payload["model"], "choices": [{"message": {"content": json.dumps({"choice": answer})}}]}

    def asked(self, kind):
        return [call for call in self.calls if kind in call["questions"]]


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

    def test_rule_miss_level_is_important_from_the_one_levels_table(self):
        # project-rules #mark-order, jev-suggestions #level-important: one MARK_LEVELS row, returned in /api/jev.
        self.seed()
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.95)})
        result = self.read(self.service(provider))
        self.assertEqual(result["levels"]["missed"], "important")
        self.assertEqual([item.get("level") for item in self.rules(result)], ["important"])
        # the row alone sets it: changing the table changes the item
        with unittest.mock.patch.dict(jev.MARK_LEVELS, {"missed": "warning"}):
            result = self.read(self.service(provider))
        self.assertEqual((result["levels"]["missed"], self.rules(result)[0]["level"]), ("warning", "warning"))
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
        self.assertEqual(len([c for c in provider.general_calls if json.loads(c["messages"][-1]["content"]).get("rule") == ONBOARDING]), 1)
        self.assertEqual({c["model"] for c in provider.general_calls}, {jev.DEFAULT_LLM_MODEL})
        rule_call = next(c for c in provider.general_calls if "rule" in json.loads(c["messages"][-1]["content"]))
        schema = rule_call["response_format"]["json_schema"]
        self.assertTrue(schema["strict"] and rule_call["provider"]["require_parameters"])
        self.assertEqual(schema["schema"]["properties"]["choice"]["enum"], ["missed", "covered", "not triggered"])
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
            self.assertEqual(len(provider.general_calls), asked + 1)

    def test_general_failure_is_retried_once_like_jev(self):
        self.seed()
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.2)},
                                general={("rule", ONBOARDING): [RuntimeError("timeout"), "covered"]})
        item = next(i for i in self.rules(self.read(self.service(provider))) if i["target"].endswith("#acceptance-onboarding"))
        self.assertEqual(item["state"], "none")
        self.assertEqual(len([c for c in provider.general_calls if json.loads(c["messages"][-1]["content"]).get("rule") == ONBOARDING]), 2)

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

    def test_off_returns_off(self):
        self.seed()
        service = jev.JevService(state_dir=Path(self.tmp.name) / "state", api_key="")
        self.assertEqual(self.read(service), {"jev": "off", "items": [], "levels": jev.MARK_LEVELS})


if __name__ == "__main__":
    unittest.main()
