"""Project-wide rules behind fakes (project-rules #declare, #questions, #marks, #pending, #dismiss)."""

import importlib.util
import json
import shutil
import subprocess
import sys
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
JEV_READ = ROOT / "skill" / "review-spec" / "scripts" / "jev-read.py"
_read_spec = importlib.util.spec_from_file_location("review_serve_jev_rules_read", JEV_READ)
READ = importlib.util.module_from_spec(_read_spec)
_read_spec.loader.exec_module(READ)

ONBOARDING = "Every change to what a user sees or does adds its onboarding section."
LOCAL = "The report page shows totals per week."
FEATURE = "The export button downloads a CSV of the current table."
RULE_NAME = "Every change ships its onboarding"  # the name the onboarding criterion's mark gives (#declare)
HOME = "specs/onboarding.spec.html#acceptance-onboarding"  # as the single root serves it
CLAUSE = "docs/specs/onboarding.spec.html#acceptance-onboarding"  # its home spec and anchor (#mark-word)


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
    """Jev answers from the scope map; the general LLM from its own map, and the rule check from the rule map;
    every payload is kept. A rule map value is (answer, confidence), the answer the rule check's outcome for that
    rule, missed, covered, or not triggered, a function of the spec's text giving one, a list of them giving one per
    call, the last repeating, or an exception failing the call (project-rules #q-rule-llm); Jev is never asked it.
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
        elif kind == "coverage":
            pair = tuple((state.get(side) or {}).get("anchor") for side in ("story", "criterion"))
            label, confidence = ("verifies" if pair in self.coverage else "unrelated"), self.confidence
        elif kind == "type":
            label, confidence = self.change.get(state.get("after"), "unrelated"), self.confidence
        elif kind in ("about", "contradicts", "oversteps", "overlaps"):
            # The gate lists its two clauses in content order (#gate-set-state): the target is either side.
            targets = [state["target"]] if "target" in state else [(state.get(side) or {}).get("text") for side in ("first", "second")]
            wanted = next((self.draft[target] for target in targets if target in self.draft), None)
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
        if kind == "rule":  # the rule check: one outcome per rule asked, by number
            answer = {number: self.rule_answer(rule, state["spec"]) for number, rule in state["rules"].items()}
        else:
            answer = {"choice": self.one(self.general.get((kind, state.get("criterion"))))}
        return {"model": payload["model"], "choices": [{"message": {"content": json.dumps(answer)}}]}

    @staticmethod
    def one(answer):
        if isinstance(answer, list):  # one answer per call, the last repeating
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def rule_answer(self, rule, text):
        answer = self.one(self.rule.get(rule, ("not triggered", self.confidence))[0])
        return answer(text) if callable(answer) else answer

    def asked(self, kind):
        """Jev calls for one question."""
        return [call for call in self.calls if kind in call["questions"]]

    def rule_checks(self):
        """The rule checks asked, each the rule texts of its one general LLM call (project-rules #q-rule-llm)."""
        return [list(json.loads(call["messages"][-1]["content"])["rules"].values())
                for call in self.general_calls if general_kind(call) == "rule"]


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
        self.assertIs(item["served"], True, "the single root serves each of its specs, so the source links (#card-source)")
        # a marked criterion needs no scope answer: no scope question is asked of it, nor any name
        self.assertNotIn(ONBOARDING, [call["state"]["criterion"] for call in provider.asked("scope")])
        self.assertEqual(provider.rule_checks(), [[ONBOARDING]])
        self.seed(marked=False)
        result = self.read(service)
        self.assertEqual((self.rules(result), result["rules"]), ([], []))
        self.assertEqual(provider.rule_checks(), [[ONBOARDING]])

    def test_a_rule_check_waits_only_in_the_llm_line_while_every_jev_worker_is_busy(self):
        """jev-suggestions #ask-queue, #q-rule-llm: the rule set is the general LLM's alone, marked llm_only, so a spec's
        rule check goes straight to the LLM's line: with every Jev worker busy and Jev asks waiting, it starts at once
        on an LLM run and answers, holding no Jev worker."""
        self.assertTrue(SETS["rule"].llm_only)
        with self.assertRaises(ValueError):
            jev.QuestionSet.from_mapping({"id": "rule", "version": 1, "llm_only": "yes"}, "rule")
        self.seed()
        lines = []

        class OnLine(FakeProvider):
            def complete(self, payload):
                lines.append(jev._ON_LINE.name)
                return super().complete(payload)

        provider = OnLine(rule={ONBOARDING: ("missed", 0.95)})
        service = self.service(provider)
        service._lines["jev"].size = 0  # every Jev worker busy: Jev asks wait
        deadline = time.time() + 5
        while True:
            result = self.read(service, settle=False)
            if [item["state"] for item in self.rules(result)] == ["label"] or time.time() > deadline:
                break
            time.sleep(0.01)
        self.assertEqual([(item["state"], item["label"]) for item in self.rules(result)], [("label", "missed")])
        self.assertEqual((provider.rule_checks(), lines), ([[ONBOARDING]], ["llm"]))
        self.assertEqual(provider.calls, [])  # Jev was asked nothing: its asks still wait
        self.assertGreater(len(service._lines["jev"].waiting), 0)
        self.assertEqual(service._lines["jev"].running, 0)

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
        self.assertEqual(result["rules"], [CLAUSE])
        self.assertEqual([item["home"] for item in self.rules(result)], result["rules"])
        # the rule check is asked only for the rule: an unmarked criterion is never checked, whatever its scope
        # answer, and B's own criterion is never a rule for B
        self.assertEqual(provider.rule_checks(), [[ONBOARDING]])
        self.read(service, "onboarding.spec.html")
        self.assertNotIn(LOCAL, sum(provider.rule_checks(), []))
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
        self.assertEqual(provider.rule_checks(), [])

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
        self.assertEqual(result["rules"], [CLAUSE])

    def test_rules_rebuild_from_the_marks_after_records_deleted(self):
        """project-rules #acceptance-cache-rebuild: with the Jev records deleted, the same rules are read from the marks."""
        self.seed()
        provider = FakeProvider(rule={ONBOARDING: ("missed", 0.95)})
        first = self.read(self.service(provider))
        self.assertEqual(first["rules"], [CLAUSE])
        shutil.rmtree(Path(self.tmp.name) / "state")
        rebuilt = self.read(self.service(FakeProvider(rule={ONBOARDING: ("missed", 0.95)})))
        self.assertEqual(rebuilt["rules"], first["rules"])
        self.assertEqual([(i["target"], i["name"]) for i in self.missed(rebuilt)], [(HOME, RULE_NAME)])

    def test_a_rule_check_carries_no_unsure_count_and_asks_jev_nothing(self):
        """jev-suggestions #markers-agent-unsure, #chain-rule: a rule check, which Jev is never asked, is no unsure
        answer, whatever it decides."""
        self.seed()
        for outcome, state in (("missed", "label"), ("covered", "none"), ("not triggered", "none")):
            with self.subTest(outcome=outcome):
                provider = FakeProvider(rule={ONBOARDING: (outcome, 0.95)})
                result = self.read(self.service(provider, state="state-" + outcome))
                self.assertEqual([i["state"] for i in self.rules(result)], [state])
                self.assertEqual((provider.rule_checks(), provider.asked("rule")), ([[ONBOARDING]], []))
                self.assertNotIn("unsure", self.rules(result)[0])

    def test_the_rule_check_is_one_general_llm_run_about_every_rule_and_no_jev_ask(self):
        """project-rules #acceptance-rule-one-run, #q-rule-llm, #q-rule: a spec under shaping whose project marks
        several rules in other specs; an edit committed, then a reload, asks one general LLM run about every rule for
        the edited text and no Jev ask; its answer decides each rule, one record per rule keyed by rule text, spec
        text, and set version, reused across a restart."""
        endpoints = "When any change adds a public endpoint, it ships its rate-limit test."
        screens = "Every feature that alters what a user sees ships with its accessibility check."
        rules = [ONBOARDING, endpoints, screens]
        self.write("onboarding.spec.html", spec(("acceptance-onboarding", ONBOARDING, RULE_NAME),
                                                ("endpoints", endpoints, "Endpoints ship rate-limit tests"),
                                                ("screens", screens, "Screens ship accessibility checks")))
        self.write("b.spec.html", spec(("b-one", "Old criterion."), body="Old body."))
        self.base = self.commit()
        self.write("b.spec.html", spec(("b-one", FEATURE), body="The report page gains an export button."))
        self.commit()
        provider = FakeProvider(rule={ONBOARDING: ("missed", 0.95), endpoints: ("not triggered", 0.95),
                                      screens: ("covered", 0.95)})
        service = self.service(provider)
        result = self.read(service)
        self.assertEqual([(i["target"], i["state"]) for i in self.rules(result)],
                         [(HOME, "label"), ("specs/onboarding.spec.html#endpoints", "none"),
                          ("specs/onboarding.spec.html#screens", "none")])
        self.assertEqual(provider.rule_checks(), [rules])
        self.assertEqual([call for call in provider.calls if "rule" in call["questions"] or "rule" in call["state"]], [])
        call = provider.general_calls[0]
        self.assertEqual(call["model"], "")  # unset llm_model: the CLI's own default (#q-fallback)
        self.assertEqual(json.loads(call["messages"][-1]["content"])["spec"], jev.spec_text(self.specs.joinpath("b.spec.html").read_bytes()))
        schema = call["response_format"]["json_schema"]
        self.assertTrue(schema["strict"] and call["provider"]["require_parameters"])
        self.assertEqual({k: v["enum"] for k, v in schema["schema"]["properties"].items()},
                         {str(n): ["not triggered", "covered", "missed"] for n in (1, 2, 3)})
        records = [r for r in self.records() if r["kind"] == "rule"]
        self.assertEqual(sorted(r["answer"]["label"] for r in records), ["covered", "missed", "not triggered"])
        self.assertTrue(all(r["source"] == "llm" and r["model"] != jev.MODEL and r["outcome"] == "shown"
                            and r["question_set"] == {"id": "rule", "version": SETS["rule"].version} for r in records))
        text = jev.spec_text(self.specs.joinpath("b.spec.html").read_bytes())
        self.assertEqual({r["cache_key"] for r in records},
                         {service.seam.key({"kind": "rule", "state": {"rule": rule, "spec": text}}) for rule in rules})
        self.assertEqual(self.missed(result)[0]["record"], next(r["record_id"] for r in records if r["answer"]["label"] == "missed"))
        # a restart reuses each rule's record: nothing is asked again
        self.assertEqual(self.rules(self.read(self.service(provider))), self.rules(result))
        self.assertEqual(len(provider.general_calls), 1)
        # another edit committed: one run again, about every rule, for the edited text
        self.write("b.spec.html", spec(("b-one", FEATURE), body="The report page gains an export menu."))
        self.commit()
        self.read(service)
        self.assertEqual(provider.rule_checks(), [rules, rules])
        self.assertEqual([call for call in provider.calls if "rule" in call["questions"] or "rule" in call["state"]], [])

    def test_a_rule_marked_later_is_one_run_about_that_rule_alone(self):
        """project-rules #acceptance-rule-new-rule, #q-rule-llm: with the rule check answered, a criterion of another
        spec marked as a rule asks one general LLM run about the new rule alone; every other rule keeps its answer."""
        self.seed()
        provider = FakeProvider(rule={ONBOARDING: ("missed", 0.95), LOCAL: ("missed", 0.95)})
        service = self.service(provider)
        before = {i["target"]: i["record"] for i in self.rules(self.read(service))}
        self.assertEqual((provider.rule_checks(), list(before)), ([[ONBOARDING]], [HOME]))
        self.write("onboarding.spec.html", spec(("acceptance-onboarding", ONBOARDING, RULE_NAME),
                                                ("local", LOCAL, "Weekly totals")))
        self.commit()
        result = self.read(service)
        self.assertEqual(provider.rule_checks(), [[ONBOARDING], [LOCAL]])
        self.assertEqual(result["rules"], [CLAUSE, "docs/specs/onboarding.spec.html#local"])
        self.assertEqual({i["target"]: i["record"] for i in self.rules(result)}[HOME], before[HOME])
        self.assertEqual(provider.asked("rule"), [])

    def test_the_agent_read_lists_no_waiting_rule_as_checked_and_counts_each_pending(self):
        """project-rules #acceptance-agent-read-unchecked, #q-rule-def, #pending-rule: while the general LLM has not
        answered the rule check, the read lists none of its rules as checked and counts each as pending, escalated
        from the first read since the rule check waits on the general LLM alone; once answered, they are checked."""
        self.write("onboarding.spec.html", spec(("acceptance-onboarding", ONBOARDING, RULE_NAME),
                                                ("local", LOCAL, "Weekly totals")))
        self.write("b.spec.html", spec(("b-one", "Old criterion."), body="Old body."))
        self.base = self.commit()
        self.write("b.spec.html", spec(("b-one", FEATURE), body="The report page gains an export button."))
        gate = threading.Event()
        provider = FakeProvider(rule={ONBOARDING: ("missed", 0.95)}, gate=gate)
        service = self.service(provider)
        try:
            result = self.read(service, settle=False)
            self.assertEqual([(i["state"], i["escalated"]) for i in self.rules(result)], [("pending", True)] * 2)
            self.assertEqual(result["rules"], [])
            lines = [line.split() for line in READ.summary(result)]
            self.assertIn(["rules", "none"], lines)
            self.assertIn(["pending", str(READ.pending(result))], lines)
            self.assertEqual(READ.pending({"items": self.rules(result)}), 2)  # each waiting rule counts
        finally:
            gate.set()
        result = self.read(service)
        self.assertEqual(result["rules"], [CLAUSE, "docs/specs/onboarding.spec.html#local"])
        self.assertEqual([(i["state"], i["label"]) for i in self.rules(result)], [("label", "missed"), ("none", None)])
        self.assertFalse([i for i in self.rules(result) if "escalated" in i])
        self.assertEqual(provider.rule_checks(), [[ONBOARDING, LOCAL]])

    def test_failed_rule_check_waits_its_pause_then_is_asked_again(self):
        """project-rules #q-rule-llm, #q-rule-def cite jev-suggestions #state-error: the one retry rule, never every
        load; a rule whose call failed is never listed as checked."""
        self.seed()
        now = [1000.0]
        provider = FakeProvider(rule={ONBOARDING: (RuntimeError("down"), 0.95)})
        onboarding = lambda result: self.rules(result)[0]
        general = lambda: len(provider.rule_checks())
        with unittest.mock.patch.object(jev, "_wall", lambda: now[0]):
            service = self.service(provider)
            result = self.read(service)
            self.assertEqual((onboarding(result)["state"], result["rules"]), ("unavailable", []))
            asked = general()
            provider.rule[ONBOARDING] = ("not triggered", 0.95)
            now[0] += jev.RETRY_PAUSE - 1
            for _ in range(2):
                self.assertEqual(onboarding(self.read(service, settle=False))["state"], "unavailable")
            time.sleep(0.05)
            self.assertEqual(general(), asked)
            now[0] += 2
            started = time.time()
            self.assertEqual(onboarding(self.read(service, settle=False))["state"], "pending")
            self.assertLess(time.time() - started, 0.5)
            result = self.read(service)
            self.assertEqual((onboarding(result)["state"], onboarding(result)["label"], result["rules"]), ("none", None, [CLAUSE]))
            self.assertEqual(general(), asked + 1)
            self.assertEqual(provider.asked("rule"), [])  # Jev is never asked, only the LLM

    def test_failed_rule_check_honors_the_provider_wait(self):
        self.seed()
        now = [1000.0]
        provider = FakeProvider(rule={ONBOARDING: (jev.ProviderFailure("rate limited", 300.0), 0.95)})
        onboarding = lambda result: self.rules(result)[0]
        with unittest.mock.patch.object(jev, "_wall", lambda: now[0]):
            service = self.service(provider)
            self.assertEqual(onboarding(self.read(service))["state"], "unavailable")
            asked = len(provider.general_calls)
            provider.rule[ONBOARDING] = ("covered", 0.95)
            now[0] += jev.RETRY_PAUSE + 1
            self.assertEqual(onboarding(self.read(service, settle=False))["state"], "unavailable")
            time.sleep(0.05)
            self.assertEqual(len(provider.general_calls), asked)
            now[0] += 300
            self.assertEqual(onboarding(self.read(service))["state"], "none")
            self.assertEqual(len(provider.general_calls), asked + 1)  # the one rule check, asked again

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
        """project-rules #pending, #pending-rule: a waiting rule check is escalated from the start, since only the
        general LLM answers it; the wheel becomes the rule card."""
        self.seed()
        gate = threading.Event()
        provider = FakeProvider(rule={ONBOARDING: ("missed", 0.95)}, gate=gate)
        service = self.service(provider)
        try:
            first = self.rules(self.read(service, settle=False))
            self.assertEqual([(i["state"], i["escalated"]) for i in first], [("pending", True)])
        finally:
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
        self.assertEqual(provider.rule_checks(), [])

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
        self.assertEqual(result["rules"], sorted([CLAUSE, "docs/specs/onboarding.spec.html#endpoints",
                                                  "docs/specs/onboarding.spec.html#accessibility"]))
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
        """project-rules #proof own-project; jev-suggestions #acceptance-own-project, #acceptance-own-project-read: mounts of two projects, each marking a rule;
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
        self.assertEqual(result["rules"], [CLAUSE])  # its home spec and anchor, never a served path (#mark-word)
        self.assertNotIn(OTHER_RULE, sum(provider.rule_checks(), []))
        self.assertEqual([item["path"] for item in service._served_specs(mounts, page["spec_file"], page)],
                         ["ann1/docs/specs/onboarding.spec.html"])

    # Lanes of one project (project-rules #q-rule-source): its main, origin/main, the page's lane, and another lane,
    # each a worktree of one repository, as a box's lanes are.

    ENDPOINTS = "When any change adds a public endpoint, it ships its rate-limit test."
    ENDPOINTS_HOME = "docs/specs/endpoints.spec.html#acceptance-endpoints"

    def lanes(self, main, own=None, other=None):
        """The project's main holding main, files by repo path; the page's lane ann1 and another lane ann2, each a
        worktree off it changing own and other in its root (None deletes); B, the page, gains a feature in ann1.
        Returns the two lane roots; self.base is main's commit, where both lanes left it."""
        for relative, text in {"docs/specs/b.spec.html": spec(("b-one", "Old criterion."), body="Old body."),
                               **main}.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.base = self.commit()
        git(self.root, "update-ref", "refs/remotes/origin/main", self.base)
        roots = []
        for slug, files in (("ann1", {"docs/specs/b.spec.html": spec(("b-one", FEATURE), body="Export button."),
                                      **(own or {})}), ("ann2", other or {})):
            root = Path(self.tmp.name) / slug
            git(self.root, "worktree", "add", "-q", "-b", slug, str(root), self.base)
            self.change(root, files)
            roots.append(root)
        return roots

    @staticmethod
    def change(root, files):
        for relative, text in files.items():
            path = Path(root) / relative
            if text is None:
                path.unlink()
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def move_main(self, files):
        """Main moves on after the lanes left it: files land on it and origin/main follows, as a fetch moves it."""
        self.change(self.root, files)
        git(self.root, "add", "-A")
        git(self.root, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qm", "main")
        git(self.root, "update-ref", "refs/remotes/origin/main", "HEAD")

    @staticmethod
    def row(root, slug, name, project="alpha"):
        return {"slug": slug, "project": project, "root": str(root), "narrow_root": str(Path(root) / "docs"),
                "spec": "docs/specs/" + name, "spec_file": str(Path(root) / "docs" / "specs" / name)}

    def lane_read(self, service, mounts, settle=True):
        """The agent read's /api/jev on the page, the first row's spec, waited until no item is pending."""
        page = mounts[0]
        args = (page, page["spec_file"], page["slug"] + "/" + page["spec"], self.base, [], "", mounts)
        result = service.response(*args)
        deadline = time.time() + 5
        while settle and any(item["state"] == "pending" for item in result["items"]) and time.time() < deadline:
            time.sleep(0.01)
            result = service.response(*args)
        return result

    def test_first_failing_rule_source_is_main_and_the_page_lane_own_root(self):
        """project-rules #proof-rule-source: with the project's main marking a rule in a spec no row serves, and
        another lane serving a copy of a second home spec that rewords its rule, /api/jev for a lane's spec lists both
        rules as checked by home spec and anchor, and its one rule-check ask quotes main's text of each; with the lane
        rewording the second rule in its own root, the ask quotes the lane's text."""
        endpoints = lambda text: spec(("acceptance-endpoints", text, "Endpoints ship rate-limit tests"))
        reworded = "When any change adds a public endpoint, it ships its rate-limit and load tests."
        own = "When any change adds an endpoint, it ships its rate-limit test."
        lane, other = self.lanes({"docs/specs/onboarding.spec.html": spec(("acceptance-onboarding", ONBOARDING, RULE_NAME)),
                                  "docs/specs/endpoints.spec.html": endpoints(self.ENDPOINTS)},
                                 other={"docs/specs/endpoints.spec.html": endpoints(reworded)})
        mounts = [self.row(lane, "ann1", "b.spec.html"), self.row(other, "ann2", "endpoints.spec.html")]
        provider = FakeProvider()
        service = self.service(provider)
        result = self.lane_read(service, mounts)
        self.assertEqual(result["rules"], [self.ENDPOINTS_HOME, CLAUSE])
        self.assertEqual(provider.rule_checks(), [[self.ENDPOINTS, ONBOARDING]])
        self.change(lane, {"docs/specs/endpoints.spec.html": endpoints(own)})
        result = self.lane_read(service, mounts)
        self.assertEqual(result["rules"], [self.ENDPOINTS_HOME, CLAUSE])
        self.assertEqual(provider.rule_checks(), [[self.ENDPOINTS, ONBOARDING], [own]])

    def test_the_rules_checked_are_every_rule_main_marks_each_once_by_home(self):
        """project-rules #acceptance-rule-every, #q-rule-source, #mark-word: the project's main marks rules in specs no
        lane serves, one in a folder the review index walks into, one landing on main after the lane left it; the rules
        checked name each once by its home spec and anchor, its home served in the lane served twice, and none from the
        page's own spec, a folder the index skips, or outside the row's collection; a repeat read runs git only to
        resolve refs, and main moving on reads its tree and only the copy that changed."""
        screens = "Every feature that alters what a user sees ships with its accessibility check."
        late = "Every feature names its owner team."
        skipped = "Every feature ships its fixture data."
        lane, other = self.lanes({
            "docs/specs/onboarding.spec.html": spec(("acceptance-onboarding", ONBOARDING, RULE_NAME)),
            "docs/specs/ui/screens.spec.html": spec(("acceptance-screens", screens, "Screens ship accessibility checks")),
            "docs/specs/fixtures/data.spec.html": spec(("acceptance-data", skipped, "Fixture data")),
            "docs/.hidden/secret.spec.html": spec(("acceptance-secret", skipped, "Hidden")),
            "notes/outside.spec.html": spec(("acceptance-outside", skipped, "Outside")),
        }, own={"docs/specs/b.spec.html": spec(("b-one", FEATURE), ("b-rule", LOCAL, "Weekly totals"), body="Export.")})
        self.move_main({"docs/specs/late.spec.html": spec(("acceptance-late", late, "Owner team"))})
        mounts = [self.row(lane, "ann1", "b.spec.html"), self.row(lane, "ann1", "onboarding.spec.html"),
                  self.row(other, "ann2", "onboarding.spec.html")]
        provider = FakeProvider()
        service = self.service(provider)
        result = self.lane_read(service, mounts)
        homes = ["docs/specs/late.spec.html#acceptance-late", CLAUSE, "docs/specs/ui/screens.spec.html#acceptance-screens"]
        self.assertEqual(result["rules"], homes)
        self.assertEqual(sorted(item["home"] for item in self.rules(result)), homes)
        self.assertEqual(provider.rule_checks(), [[late, ONBOARDING, screens]])

        gits = []
        run = subprocess.run

        def counted(argv, *args, **kwargs):
            if argv and argv[0] == "git":
                gits.append(tuple(argv))
            return run(argv, *args, **kwargs)

        with unittest.mock.patch.object(jev.subprocess, "run", counted):
            self.assertEqual(self.lane_read(service, mounts, settle=False), result)
        self.assertTrue(gits and all("rev-parse" in argv for argv in gits), gits)
        moved = "Every feature names its owner team and its pager."
        self.move_main({"docs/specs/late.spec.html": spec(("acceptance-late", moved, "Owner team"))})
        gits.clear()
        with unittest.mock.patch.object(jev.subprocess, "run", counted):
            result = self.lane_read(service, mounts)
        reads = [argv[argv.index("-C") + 2] for argv in gits if "rev-parse" not in argv]
        self.assertEqual(sorted(reads), ["ls-tree", "merge-base", "show"], gits)
        self.assertEqual(result["rules"], homes)
        self.assertEqual(provider.rule_checks()[-1], [moved])

    def three_rules(self):
        """A spec of the project whose main copy marks two rules and leaves a third criterion unmarked, and its copy
        that rewords the first, unmarks the second, and marks the third."""
        first = "Every change keeps each tab's place until reload."
        second = "Every feature ships with its audit log entry."
        third = "Every feature names its owner team."
        reworded = "Every change keeps each tab's place and scroll until reload."
        main = spec(("acceptance-place", first, "Tabs keep their place"), ("acceptance-audit", second, "Audit log"),
                    ("acceptance-owner", third))
        changed = spec(("acceptance-place", reworded, "Tabs keep their place"), ("acceptance-audit", second),
                       ("acceptance-owner", third, "Owner team"))
        return {"docs/specs/shared.spec.html": main}, {"docs/specs/shared.spec.html": changed}, (first, second, third, reworded)

    def test_another_lane_copy_of_a_spec_never_changes_the_rules(self):
        """project-rules #acceptance-rule-other-lane, #q-rule-source: another lane serves a spec of the project whose
        copy rewords one rule main marks, unmarks another, and marks a new one; the rules checked name both rules main
        marks and not the other lane's new one, and the rule check asks about the reworded rule as main states it."""
        main, changed, (first, second, _, _) = self.three_rules()
        lane, other = self.lanes(main, other=changed)
        mounts = [self.row(lane, "ann1", "b.spec.html"), self.row(other, "ann2", "shared.spec.html")]
        provider = FakeProvider()
        result = self.lane_read(self.service(provider), mounts)
        self.assertEqual(result["rules"], ["docs/specs/shared.spec.html#acceptance-audit",
                                           "docs/specs/shared.spec.html#acceptance-place"])
        self.assertEqual(provider.rule_checks(), [[first, second]])

    def test_the_page_lane_change_to_a_spec_is_the_rules_as_the_lane_has_them(self):
        """project-rules #acceptance-rule-own-lane, #q-rule-source, #card-mark-as-rule: the page's lane changes another
        spec of the project, rewording one rule main marks, unmarking another, and marking a new one, committed or in its
        working copy; the rules checked name the reworded rule and the new one and not the unmarked one, the rule check
        asks about the reworded rule as the lane states it; a spec the lane adds holds its rules and one it deletes none,
        while another lane serving that spec's main copy changes nothing."""
        main, changed, (_, _, third, reworded) = self.three_rules()
        added = "Every feature ships its changelog line."
        lane, other = self.lanes({**main, "docs/specs/onboarding.spec.html": spec(
            ("acceptance-onboarding", ONBOARDING, RULE_NAME))}, own=changed)
        git(lane, "add", "-A")
        git(lane, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qm", "lane")
        self.change(lane, {"docs/specs/onboarding.spec.html": None,
                           "docs/specs/added.spec.html": spec(("acceptance-changelog", added, "Changelog line"))})
        mounts = [self.row(lane, "ann1", "b.spec.html"), self.row(other, "ann2", "onboarding.spec.html"),
                  self.row(other, "ann2", "shared.spec.html")]
        provider = FakeProvider()
        result = self.lane_read(self.service(provider), mounts)
        self.assertEqual(result["rules"], ["docs/specs/added.spec.html#acceptance-changelog",
                                           "docs/specs/shared.spec.html#acceptance-owner",
                                           "docs/specs/shared.spec.html#acceptance-place"])
        self.assertEqual(provider.rule_checks(), [[added, reworded, third]])

    def test_the_rule_card_source_opens_the_home_spec_on_the_page_own_lane(self):
        """project-rules #acceptance-source-own-lane, #acceptance-source-main-only, #card-source: a missed rule whose
        home spec both the page's lane and another lane serve, and a mark citing it read from the other lane's row:
        each card's source is the home spec's path on the page's own lane, its home the repo path, served, so it links;
        with the home served only by another lane's row, a stale row of the page's lane, or no row, main alone holding
        it, the page's lane serves no page of it, so each card's source is plain text."""
        lane, other = self.lanes({"docs/specs/onboarding.spec.html": spec(("acceptance-onboarding", ONBOARDING, RULE_NAME))})
        own = "ann1/docs/specs/onboarding.spec.html#acceptance-onboarding"
        page = self.row(lane, "ann1", "b.spec.html")
        for name, mounts, served in (
                ("both", [page, self.row(lane, "ann1", "onboarding.spec.html"),
                          self.row(other, "ann2", "onboarding.spec.html")], True),
                ("other", [page, self.row(other, "ann2", "onboarding.spec.html")], False),
                ("stale", [page, {**self.row(lane, "ann1", "onboarding.spec.html"), "stale": "gone"},
                           self.row(other, "ann2", "onboarding.spec.html")], False),
                ("main", [page], False)):
            with self.subTest(served=name):
                provider = FakeProvider(rule={ONBOARDING: ("missed", 0.95)}, draft={ONBOARDING: "contradicts"})
                result = self.lane_read(self.service(provider, "state-" + name), mounts)
                self.assertEqual([(i["target"], i["home"], i["served"]) for i in self.missed(result)],
                                 [(own, CLAUSE, served)])
                cited = [i["rule"] for i in result["items"] if i["kind"] == "corpus" and "rule" in i]
                if name != "main":  # a draft check reads only served specs, so no mark cites a rule only main holds
                    self.assertTrue(cited)
                self.assertEqual({(rule["target"], rule["home"], rule["served"]) for rule in cited},
                                 {(own, CLAUSE, served)} if cited else set())

    def test_the_rule_card_source_is_the_serving_row_own_path(self):
        """project-rules #card-source, #acceptance-source-own-lane: once another project holds the lane's slug, the
        host gives later rows slug/<project>/<spec>, so one lane's rows sit under different prefixes. The card links the
        home at the path of the page lane's row serving it, whichever prefix each has; a row of another project at the
        lane's slug and the home's repo path serves the page's lane nothing, so the source is plain text."""
        lane, other = self.lanes({"docs/specs/onboarding.spec.html": spec(("acceptance-onboarding", ONBOARDING, RULE_NAME))})

        def at(row, path):
            return {**row, "path": path + row["spec"]}

        plain, prefixed = at(self.row(lane, "ann1", "b.spec.html"), "ann1/"), \
            at(self.row(lane, "ann1", "b.spec.html"), "ann1/alpha/")
        beta = at(self.row(other, "ann1", "onboarding.spec.html", project="beta"), "ann1/beta/")
        for name, mounts, target, served in (
                ("own row plain, page prefixed",
                 [prefixed, at(self.row(lane, "ann1", "onboarding.spec.html"), "ann1/"), beta],
                 "ann1/docs/specs/onboarding.spec.html", True),
                ("own row prefixed, page plain",
                 [plain, at(self.row(lane, "ann1", "onboarding.spec.html"), "ann1/alpha/"), beta],
                 "ann1/alpha/docs/specs/onboarding.spec.html", True),
                ("another project's row at the slug", [prefixed, beta],
                 "ann1/alpha/docs/specs/onboarding.spec.html", False)):
            with self.subTest(name):
                provider = FakeProvider(rule={ONBOARDING: ("missed", 0.95)}, draft={ONBOARDING: "contradicts"})
                result = self.lane_read(self.service(provider, "state-" + name.replace(" ", "-").replace("'", "")),
                                        mounts)
                self.assertEqual([(i["target"], i["home"], i["served"]) for i in self.missed(result)],
                                 [(target + "#acceptance-onboarding", CLAUSE, served)])
                cited = [i["rule"] for i in result["items"] if i["kind"] == "corpus" and "rule" in i]
                self.assertEqual({(rule["target"], rule["served"]) for rule in cited},
                                 {(target + "#acceptance-onboarding", served)} if served else set())

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
        # a second marked rule B also misses: dismissing one rule leaves the other asked and shown
        screens = "Every feature that alters what a user sees ships with its accessibility check."
        screens_home = "specs/onboarding.spec.html#screens"
        marked_screens = ("screens", screens, "Screens ship accessibility checks")
        self.seed()
        self.write("onboarding.spec.html", spec(("acceptance-onboarding", ONBOARDING, RULE_NAME), marked_screens))
        self.write("c.spec.html", spec(("c-one", "Another feature criterion."), body="C body."))
        provider = FakeProvider(rule={ONBOARDING: ("missed", 0.95), screens: ("missed", 0.95)})
        service = self.service(provider)
        missed = self.missed(self.read(service, project="alpha"))
        self.assertEqual(sorted(i["target"] for i in missed), [HOME, screens_home])
        item = next(i for i in missed if i["target"] == HOME)
        self.assertTrue(service.dismiss(self.mount("alpha"), self.b(), "here", item["home"], item["record"]))
        result = self.read(service, project="alpha")
        self.assertEqual(([(i["target"], i["label"]) for i in self.rules(result)], result["rules"]),
                         ([(screens_home, "missed")], [CLAUSE, "docs/" + screens_home]))
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
        checks = provider.rule_checks()
        self.assertEqual(checks, [[ONBOARDING, screens]])
        self.write("b.spec.html", spec(("b-one", FEATURE), body="Edited export body."))
        self.commit()
        self.write("b.spec.html", spec(("b-one", FEATURE), body="Edited again."))
        reworded = "Every change to a screen adds its onboarding section."
        provider.rule[reworded] = ("missed", 0.95)
        self.write("onboarding.spec.html", spec(("acceptance-onboarding", reworded, RULE_NAME), marked_screens))
        restarted = self.service(provider)
        edited = self.rules(self.read(restarted, project="alpha"))
        # nor asked: the edited text's read sends the dismissed rule to no rule check, and the other rule alone to
        # one (#q-rule-llm), whose missed label lands on that rule, from the record for the edited text
        deadline = time.time() + 5
        while restarted._asking and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(provider.rule_checks(), checks + [[screens]])
        text = jev.spec_text(self.specs.joinpath("b.spec.html").read_bytes())
        record = next(r["record_id"] for r in self.records()
                      if r.get("cache_key") == restarted.seam.key({"kind": "rule", "state": {"rule": screens, "spec": text}}))
        self.assertEqual([(i["target"], i["state"], i["label"], i["record"]) for i in edited],
                         [(screens_home, "label", "missed", record)])
        self.assertEqual(sorted(i["target"] for i in self.missed(self.read(service, "c.spec.html", project="alpha"))),
                         [HOME, screens_home])
        # another project is unaffected
        self.assertEqual(sorted(i["target"] for i in self.missed(self.read(service, project="beta"))),
                         [HOME, screens_home])

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
        self.assertEqual(self.read(service, "c.spec.html")["rules"], [CLAUSE])
        self.assertEqual(set(sum(provider.rule_checks(), [])), {ONBOARDING})
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

    def test_not_for_this_spec_on_a_cited_mark_leaves_a_later_partial_overlap(self):
        """project-rules #card-cites-reject, #card-variants; jev-suggestions #level-partial, #ledger-subject: Not for
        this spec on a mark citing a rule is the rule card's, at that clause; a Partial overlap Jev later answers at
        that clause cites no rule, has no card and no button, and stays shown."""
        service, provider = self.cites()
        mark = next(m for m in self.marks(self.read(service)) if m["id"] == "body")
        self.assertTrue(service.dismiss(self.mount("alpha"), self.b(), "here", mark["rule"]["home"], mark["record"],
                                        anchor="body"))
        provider.draft[ONBOARDING] = "partial-overlap"
        self.write("b.spec.html", spec(("b-one", FEATURE), body="The report page gains an export button and a print."))
        self.assertEqual([(m["id"], m["label"], m["target"], "rule" in m) for m in self.marks(self.read(service))],
                         [("b-one", "contradicts", HOME, True), ("body", "partial-overlap", HOME, False)])

    def test_an_owner_dismissal_of_a_contradicts_leaves_a_later_overlaps(self):
        """jev-suggestions #ledger-subject, #ledger-resolved: the where holds the mark kind, so an owner's dismissal of
        a Contradicts leaves an Overlaps Jev later answers at the same pair shown."""
        service, provider = self.cites(marked=False)
        mark = next(m for m in self.marks(self.read(service)) if m["id"] == "body")
        self.assertEqual((mark["label"], mark["agent_level"]), ("contradicts", "important"))
        self.assertIsNone(self.resolve(service, mark, "dismissed", "The export is no screen."))
        self.assertEqual([m["id"] for m in self.marks(self.read(service))], ["b-one"])
        provider.draft[ONBOARDING] = "overlaps"
        self.write("b.spec.html", spec(("b-one", FEATURE), body="The report page gains an export button and a print."))
        self.assertEqual([(m["id"], m["label"], m["target"]) for m in self.marks(self.read(service))],
                         [("b-one", "contradicts", HOME), ("body", "overlaps", HOME)])
        self.assertEqual([e["kind"] for e in self.ledger()], ["contradicts"])

    def test_an_owner_dismissal_of_a_contradicts_project_rule_leaves_a_later_overlaps_project_rule(self):
        """jev-suggestions #ledger-subject, #ledger-resolved; project-rules #agent-once-warnings, #card-variants: the
        owner's dismissal of a mark citing a rule resolves that mark by its kind, never the rule at that clause, so an
        Overlaps project rule Jev later answers at that clause, citing that rule, is still listed."""
        service, provider = self.cites()
        mark = next(m for m in self.marks(self.read(service)) if m["id"] == "body")
        self.assertEqual((mark["label"], mark["agent_level"], "rule" in mark), ("contradicts", "important", True))
        self.assertIsNone(self.resolve(service, mark, "dismissed", "The export is no screen."))
        self.assertEqual([m["id"] for m in self.marks(self.read(service))], ["b-one"])
        provider.draft[ONBOARDING] = "overlaps"
        self.write("b.spec.html", spec(("b-one", FEATURE), body="The report page gains an export button and a print."))
        self.assertEqual([(m["id"], m["label"], m["target"], "rule" in m) for m in self.marks(self.read(service))],
                         [("b-one", "contradicts", HOME, True), ("body", "overlaps", HOME, True)])
        self.assertEqual([(e["anchor"], e["kind"], e["target"], e["by"]) for e in self.ledger()],
                         [("body", "contradicts", mark["rule"]["home"], "owner")])

    def test_not_for_this_spec_on_a_cited_mark_hides_a_later_mark_citing_that_rule_at_that_clause(self):
        """project-rules #card-cites-reject, #dismiss-here: the human's Not for this spec on a mark citing a rule says
        the rule does not apply at that clause, so a later mark of another kind there, citing that rule, is not listed
        and takes no resolution."""
        service, provider = self.cites()
        mark = next(m for m in self.marks(self.read(service)) if m["id"] == "body")
        self.assertTrue(service.dismiss(self.mount("alpha"), self.b(), "here", mark["rule"]["home"], mark["record"],
                                        anchor="body"))
        provider.draft[ONBOARDING] = "oversteps"
        self.write("b.spec.html", spec(("b-one", FEATURE), body="The report page gains an export button and a print."))
        self.assertEqual([(m["id"], m["label"]) for m in self.marks(self.read(service))], [("b-one", "contradicts")])
        mount = self.mount("alpha")
        found = service._read(mount, self.b(), "specs/b.spec.html", self.base, [], "", [mount], None)[0]
        later = next(item for item, _ in found if item["id"] == "body" and item.get("label") == "oversteps")
        self.assertEqual((later["agent_level"], later["rule"]["home"]), ("important", mark["rule"]["home"]))
        # named as the server names a mark, though no read lists it
        self.assertEqual(self.resolve(service, {**later, "cited": jev.JevService._cited(later)}, "dismissed",
                                      "Not a screen."), "the mark is resolved already")
        self.assertEqual([(e["anchor"], e["kind"], e["target"], e["by"]) for e in self.ledger()],
                         [("body", "rule", mark["rule"]["home"], "human")])

    def test_every_rule_card_names_a_held_record_and_its_own_fields_dismiss_it(self):
        # project-rules #dismiss-where, #dismiss-here, #card-cites-reject; jev-suggestions #ledger-judgment: every rule
        # card shown, the missed rule's and each mark citing it, names the record its deciding answer is, one the store
        # holds, so Not for this spec from the card's own fields is recorded, never refused for want of one. The rule's
        # own card dismissed leaves each mark citing it; each mark dismissed then goes too (tests/runtime-jev-rules.mjs
        # serves these answers).
        service, _ = self.cites()
        result = self.read(service)
        rule, marks = self.missed(result), [m for m in self.marks(result) if "rule" in m]
        self.assertEqual((len(rule), sorted(m["id"] for m in marks)), (1, ["b-one", "body"]))
        held = {record["record_id"] for record in self.records()}
        for card in rule + marks:
            self.assertIn(card["record"], held, card)
        self.assertTrue(service.dismiss(self.mount("alpha"), self.b(), "here", rule[0]["home"], rule[0]["record"]))
        result = self.read(service)
        self.assertEqual(self.missed(result), [])
        self.assertEqual([(m["id"], m["record"]) for m in self.marks(result) if "rule" in m],
                         [(m["id"], m["record"]) for m in marks])
        for mark in marks:
            self.assertTrue(service.dismiss(self.mount("alpha"), self.b(), "here", mark["rule"]["home"], mark["record"],
                                            anchor=mark["id"]))
        self.assertEqual(self.marks(self.read(service)), [])

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
        """A rule miss's where in the warning ledger: its rule by its home alone, where its card's Not for this spec
        writes it (jev-suggestions #ledger-subject, project-rules #dismiss-here)."""
        return (project, "docs/specs/b.spec.html", None, "rule", item["home"])

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
        before = record_lines(Path(self.tmp.name) / "state")
        # an owner dismissal carries its reason; unknown marks and resolutions are refused
        self.assertEqual(self.resolve(service, item, "dismissed"), "a dismissal names no reason")
        self.assertIsNotNone(self.resolve(service, item, "rule", "no"))
        self.assertIsNotNone(self.resolve(service, item, "thread"), "a thread is no owner resolution")
        self.assertIsNotNone(self.resolve(service, item, "fixed", record="judgment-nope"))
        self.assertIsNone(self.resolve(service, item, "dismissed", "The export is a download, no screen."))
        outcome = service.seam.ledger.latest(*self.where(item))
        self.assertEqual((outcome["outcome"], outcome["reason"], outcome["judgment"], outcome["by"], outcome["confirmed"]),
                         ("dismissed", "The export is a download, no screen.", item["record"], "owner", False))
        self.assertEqual(record_lines(Path(self.tmp.name) / "state"), before, "the record is unchanged")
        labels = jev.eval_read(service.seam.store.records(), service.seam.ledger, confirmed=True)
        self.assertNotIn(item["record"], [record["record_id"] for record, _ in labels])
        # one resolution per mark: a later one is refused and the first stands
        self.assertEqual(self.resolve(service, item, "reason", "Onboarding: not needed"), "the mark is resolved already")
        self.assertEqual(self.outcome(service, item["record"])["outcome"], "dismissed")
        self.assertEqual(len(self.outcomes(service, item["record"])), 1)

    def test_an_owner_dismissal_survives_deleting_the_records(self):
        # jev-suggestions #acceptance-ledger-wipe, #ledger-survives, #record-disposable
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.95)})
        service = self.service(provider)
        item = self.shown(service)
        self.assertIsNone(self.resolve(service, item, "dismissed", "Not a screen."))
        service.stop()
        shutil.rmtree(Path(self.tmp.name) / "state" / "records")
        again = jev.JevService(state_dir=Path(self.tmp.name) / "state", provider=provider, api_key="fake")
        self.addCleanup(again.stop)
        self.assertEqual(self.missed(self.read(again)), [], "the mark stays dismissed")
        asked = [r["record_id"] for r in self.records() if r["kind"] == "rule"]
        self.assertTrue(asked and item["record"] not in asked, "the answer was asked again")
        self.assertEqual(again.seam.ledger.latest(*self.where(item))["outcome"], "dismissed")
        self.assertEqual([e["outcome"] for e in self.ledger()], ["dismissed"])

    def test_reason_and_fixed_resolutions_are_recorded_unconfirmed(self):
        provider = FakeProvider(scope={ONBOARDING: ("every feature", 0.95)}, rule={ONBOARDING: ("missed", 0.95)})
        service = self.service(provider)
        item = self.shown(service)
        self.assertIsNone(self.resolve(service, item, "reason", "Onboarding: not needed"))
        outcome = self.outcome(service, item["record"])
        self.assertEqual((outcome["outcome"], outcome["reason"], outcome["confirmed"]),
                         ("reason", "Onboarding: not needed", False))
        service2 = self.service(provider, state="state2")
        item = self.missed(self.read(service2))[0]
        self.assertIsNone(self.resolve(service2, item, "fixed"))
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
        self.assertIsNone(self.resolve(service, item, "reason", "Onboarding: not needed"))
        self.write("b.spec.html", spec(("b-one", FEATURE + " It adds its onboarding section."), body="Body."))
        self.commit()
        self.assertEqual(self.missed(self.read(service)), [])
        self.assertEqual([e["outcome"] for e in self.outcomes(service, item["record"])], ["reason"])
        # the human's Not for this spec hides the mark at once; it stays dismissed, never fixed, though the rule is
        # never asked on the next head: a hidden mark is never read as cleared
        service = self.service(provider, state="state2")
        self.write("b.spec.html", spec(("b-one", FEATURE), body="Body again."))
        item = self.missed(self.read(service))[0]
        self.assertTrue(service.dismiss(self.mount("alpha"), str(self.specs / "b.spec.html"), "here", item["home"], item["record"]))
        self.assertEqual(self.missed(self.read(service)), [])
        self.write("b.spec.html", spec(("b-one", FEATURE + " It adds its onboarding section."), body="Body again."))
        self.commit()
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
        rel = "docs/specs/b.spec.html"
        marks = [(item, service._item_where("alpha", rel, item, {})) for item in items]
        service._fixed(service.seam, self.mount("alpha"), {"rel": rel, "head": head, "base_commit": base}, marks,
                       {"docs/specs/onboarding.spec.html#acceptance-onboarding"})

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
        self.assertIsNone(self.resolve(service, item, "dismissed", "Jev is wrong here."))

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
            with self.assertRaises(RuntimeError):  # the read's inputs unread fail the whole read: GET /api/jev 503
                self.read(service, settle=False)
        self.assertEqual(len(calls), 1, "read once")
        self.assertIsNone(self.outcome(service, item["record"]))
        # rules that fail to build on a new head decide nothing either
        self.commit()
        with unittest.mock.patch.object(service, "rule_items", side_effect=RuntimeError("busy")):
            self.assertEqual(self.missed(self.read(service, settle=False)), [])
        self.assertIsNone(self.outcome(service, item["record"]))

    # The owner resolves an Important mark through the agent read, named as its line lists it (jev-suggestions
    # #ledger-resolve, #ledger-resolved, #story-resolve).

    def resolve(self, service, item, status, reason=None, record=None):
        """The owner's resolution of the mark item, named as its agent read line lists it: its anchor, the cited the
        server puts on it (a rule by its home, #mark-word), and record (or record)."""
        mount, name = self.mount("alpha"), "b.spec.html"
        return service.resolve(mount, str(self.specs / name), "specs/" + name, self.base, [], "", [mount],
                               mark=(item["id"], item["cited"], record or item["record"]), status=status, reason=reason)

    def review_server(self, service):
        """A review server on the repo's docs whose Jev reads come from service: the spec's review link, and a close."""
        mount = serve._single_mount(str(self.root / "docs"))
        server = serve.ReviewThreadingHTTPServer(("127.0.0.1", 0), serve.MountHandler)
        server.mount_state, server.jev, server.state_dir = serve.MountState([mount]), service, None
        threading.Thread(target=server.serve_forever, daemon=True).start()
        closed = []

        def close():
            if not closed:
                closed.append(True)
                server.shutdown()
                server.server_close()
        self.addCleanup(close)
        return "http://127.0.0.1:%d/specs/b.spec.html?base=%s" % (server.server_port, self.base), close

    @staticmethod
    def agent_read(link, *args):
        out = subprocess.run((sys.executable, str(JEV_READ), link, *args), capture_output=True, text=True, timeout=60)
        return out

    def important_lines(self, link):
        """The agent read's Important mark lines, each split into its fields, once nothing is pending."""
        out = self.agent_read(link, "--wait", "10")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("pending     0", out.stdout)
        return [line.split() for line in out.stdout.splitlines() if line.startswith("important ")]

    def test_acceptance_resolve_leaves(self):
        """jev-suggestions #acceptance-resolve-leaves: a reason line recorded through the agent read, naming the mark
        as its line lists it, takes the mark out of the next read. project-rules #mark-word: the line names the rule by
        its home, as the rules line does, and the resolution takes that name, not the rule's served target."""
        self.seed()
        link, _ = self.review_server(self.service(FakeProvider(rule={ONBOARDING: ("missed", 0.95)})))
        out = self.agent_read(link, "--wait", "10")
        self.assertIn("rules       " + CLAUSE, out.stdout.splitlines())
        lines = self.important_lines(link)
        self.assertEqual([line[:5] for line in lines], [["important", "acceptance", "rule", "important", CLAUSE]])
        _, anchor, _, _, cited, record = lines[0]
        out = self.agent_read(link, "--resolve", anchor, HOME, record, "reason", "--reason", "Onboarding: not needed")
        self.assertEqual(out.returncode, 1)
        self.assertIn("refused: the read lists no Important mark at acceptance citing " + HOME, out.stderr)
        out = self.agent_read(link, "--resolve", anchor, cited, record, "reason", "--reason", "Onboarding: not needed")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.important_lines(link), [])
        self.assertEqual([(e["outcome"], e["judgment"], e["by"], e["target"]) for e in self.ledger()],
                         [("reason", record, "owner", "docs/specs/onboarding.spec.html#acceptance-onboarding")])

    def test_a_mark_citing_a_rule_is_listed_and_resolved_by_the_rule_home(self):
        """project-rules #mark-word, #agent-read-rules; jev-suggestions #ledger-resolve, #ledger-subject: the agent
        read names a rule a draft-check mark cites by its home, as the rules line and the rule miss's line do, and the
        owner's resolution takes that name, not the rule's served target; the outcome sits at the mark's where."""
        service, _ = self.cites()
        # the server names each Important mark, its cited, the one name the line prints and the resolution takes
        self.assertEqual({(item["kind"], item.get("cited")) for item in self.read(service)["items"]
                          if item.get("agent_level") == "important"}, {("rule", CLAUSE), ("corpus", CLAUSE)})
        link, _ = self.review_server(service)
        lines = self.important_lines(link)
        self.assertEqual(sorted(line[1:5] for line in lines),
                         [["acceptance", "rule", "important", CLAUSE], ["b-one", "contradicts", "important", CLAUSE],
                          ["body", "contradicts", "important", CLAUSE]])
        _, anchor, _, _, cited, record = next(line for line in lines if line[1] == "body")
        out = self.agent_read(link, "--resolve", anchor, HOME, record, "dismissed", "--reason", "Not a screen.")
        self.assertEqual(out.returncode, 1)
        self.assertIn("refused: the read lists no Important mark at body citing " + HOME, out.stderr)
        out = self.agent_read(link, "--resolve", anchor, cited, record, "dismissed", "--reason", "Not a screen.")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(sorted(line[1] for line in self.important_lines(link)), ["acceptance", "b-one"])
        self.assertEqual([(e["anchor"], e["kind"], e["target"], e["outcome"], e["by"]) for e in self.ledger()],
                         [("body", "contradicts", CLAUSE, "dismissed", "owner")])

    def test_acceptance_resolve_restart(self):
        """jev-suggestions #acceptance-resolve-restart: a mark an agent read listed before the review service
        restarted takes the owner's dismissal through the agent read after it, and leaves the read."""
        self.seed()
        provider = FakeProvider(rule={ONBOARDING: ("missed", 0.95)})
        first = self.service(provider)
        link, close = self.review_server(first)
        [line] = self.important_lines(link)
        close()
        first.stop()
        link, _ = self.review_server(self.service(provider))
        out = self.agent_read(link, "--resolve", line[1], line[4], line[5], "dismissed", "--reason", "Not a screen.")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.important_lines(link), [])
        self.assertEqual([(e["outcome"], e["reason"], e["judgment"]) for e in self.ledger()],
                         [("dismissed", "Not a screen.", line[5])])

    def test_acceptance_resolve_later_commit(self):
        """jev-suggestions #acceptance-resolve-later-commit: a rule miss given a reason line through the agent read
        stays out of the read after a commit changes another clause, though the miss is asked again on it."""
        provider = FakeProvider(rule={ONBOARDING: ("missed", 0.95)})
        service = self.service(provider)
        item = self.shown(service)
        self.assertIsNone(self.resolve(service, item, "reason", "Onboarding: not needed"))
        self.write("b.spec.html", spec(("b-one", FEATURE), body="The report page gains an export button and a print."))
        self.commit()
        self.assertEqual(self.missed(self.read(service)), [])
        again = self.missed(self.read(self.service(provider, state="fresh")))
        self.assertEqual(len(again), 1, "the commit still misses the rule")
        self.assertNotEqual(again[0]["record"], item["record"])

    def test_an_owner_resolved_miss_asked_again_after_a_commit_is_not_listed_pending(self):
        """jev-suggestions #ledger-resolved: no mark at a resolved where is listed through any later answer Jev gives
        it, so an owner-resolved miss asked again on a new head is not listed pending meanwhile."""
        provider = FakeProvider(rule={ONBOARDING: ("missed", 0.95)})
        service = self.service(provider)
        item = self.shown(service)
        self.assertIsNone(self.resolve(service, item, "reason", "Onboarding: not needed"))
        self.write("b.spec.html", spec(("b-one", FEATURE), body="The report page gains an export button and a print."))
        self.commit()
        held, decide = threading.Event(), provider.decide
        self.addCleanup(held.set)
        mount = self.mount("alpha")
        with unittest.mock.patch.object(provider, "decide", lambda payload: held.wait(5) and decide(payload)):
            found = service._read(mount, self.b(), "specs/b.spec.html", self.base, [], "", [mount], None)[0]
            self.assertEqual([i["state"] for i, _ in found if i["kind"] == "rule"], ["pending"], "asked again")
            self.assertEqual(self.rules(self.read(service, settle=False)), [])
            held.set()

    def test_acceptance_resolve_revert(self):
        """jev-suggestions #acceptance-resolve-revert, #ledger-resolved: an edit that clears a mark records the
        server's fixed, which resolves nothing: reverting the edit lists the mark again."""
        covered = lambda text: "covered" if "onboarding section" in text else "missed"
        service = self.service(FakeProvider(rule={ONBOARDING: (covered, 0.95)}))
        item = self.shown(service)
        self.write("b.spec.html", spec(("b-one", FEATURE + " It adds its onboarding section."), body="Body."))
        self.commit()
        self.assertEqual(self.missed(self.read(service)), [])
        self.assertEqual([(e["outcome"], e["by"]) for e in self.ledger()], [("fixed", "server")])
        self.write("b.spec.html", spec(("b-one", FEATURE), body="The report page gains an export button."))
        self.commit()
        self.assertEqual([m["record"] for m in self.missed(self.read(service))], [item["record"]])

    def test_acceptance_resolve_stale(self):
        """jev-suggestions #acceptance-resolve-stale: a mark resolved by the record an earlier read gave it, while the
        read lists it under another, is refused, naming the record the read lists it under."""
        service = self.service(FakeProvider(rule={ONBOARDING: ("missed", 0.95)}))
        earlier = self.shown(service)
        self.write("b.spec.html", spec(("b-one", FEATURE), body="The report page gains an export button and a print."))
        [now] = self.missed(self.read(service))
        self.assertNotEqual(now["record"], earlier["record"])
        refusal = self.resolve(service, now, "dismissed", "Not a screen.", record=earlier["record"])
        self.assertEqual(refusal, "the read lists that mark under " + now["record"])
        self.assertEqual(self.ledger(), [])
        # the one it lists takes it
        self.assertIsNone(self.resolve(service, now, "dismissed", "Not a screen."))

    def test_acceptance_resolve_twice(self):
        """jev-suggestions #acceptance-resolve-twice: a reason line for a mark the owner dismissed is refused: the mark
        is resolved already, and the dismissal stands."""
        service = self.service(FakeProvider(rule={ONBOARDING: ("missed", 0.95)}))
        item = self.shown(service)
        self.assertIsNone(self.resolve(service, item, "dismissed", "Not a screen."))
        self.assertEqual(self.resolve(service, item, "reason", "Onboarding: not needed"), "the mark is resolved already")
        self.assertEqual([e["outcome"] for e in self.ledger()], ["dismissed"])

    def test_two_resolutions_at_once_write_one_outcome(self):
        """jev-suggestions #ledger-rule, #acceptance-resolve-twice: two owner resolutions of one mark at once write one
        outcome; the other is refused, the mark resolved already. Both read the mark at once, then each waits, about to
        write, for the other to write too, as far as the ledger lets it."""
        service = self.service(FakeProvider(rule={ONBOARDING: ("missed", 0.95)}))
        item = self.shown(service)
        ledger, read, write = service.seam.ledger, service._read, service.seam.ledger.append
        both_read, both_write = threading.Barrier(2), threading.Barrier(2)

        def wait(barrier):
            try:
                barrier.wait(1)
            except threading.BrokenBarrierError:
                pass

        def reading(*args):
            found = read(*args)
            wait(both_read)
            return found

        def writing(entry):
            wait(both_write)
            return write(entry)

        results = []
        with unittest.mock.patch.object(service, "_read", reading), unittest.mock.patch.object(ledger, "append", writing):
            threads = [threading.Thread(target=lambda reason=reason: results.append(
                self.resolve(service, item, "dismissed", reason))) for reason in ("Not a screen.", "No screen here.")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(10)
        self.assertEqual(sorted(results, key=str), [None, "the mark is resolved already"])
        self.assertEqual([e["outcome"] for e in self.ledger()], ["dismissed"])

    def test_each_refusal_says_why(self):
        """jev-suggestions #ledger-resolve: one reason per refusal: none of the owner outcomes, a dismissal with no
        reason, and no Important mark the read lists at that anchor citing that clause."""
        service = self.service(FakeProvider(rule={ONBOARDING: ("missed", 0.95)}))
        item = self.shown(service)
        self.assertEqual(self.resolve(service, item, "thread"), "thread is none of the owner outcomes: fixed, reason, dismissed")
        self.assertEqual(self.resolve(service, item, "dismissed"), "a dismissal names no reason")
        self.assertEqual(self.resolve(service, {**item, "id": "b-one"}, "fixed"),
                         "the read lists no Important mark at b-one citing " + CLAUSE)
        self.assertEqual(self.resolve(service, {**item, "cited": "docs/specs/other.spec.html#x"}, "fixed"),
                         "the read lists no Important mark at acceptance citing docs/specs/other.spec.html#x")
        # project-rules #mark-word: a rule is named by its home alone, never its served target
        self.assertEqual(self.resolve(service, {**item, "cited": HOME}, "fixed"),
                         "the read lists no Important mark at acceptance citing " + HOME)
        self.assertEqual(self.ledger(), [])


if __name__ == "__main__":
    unittest.main()
