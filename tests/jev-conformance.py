"""Spec Chat's Jev seam runs every Jev contract conformance case (jev-contract #cases, #acceptance-cases-pass):
the request it sends, the key it computes, and the record it writes match each case, through the real
OpenRouter client against a local server that plays the case's recorded reply. Question sets are whole-number
versioned (#acceptance-qset-invalid)."""

import importlib.util
import json
import re
import threading
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SETS = ROOT / "skill" / "review-spec" / "assets" / "jev"
CASES = SETS / "cases"
CONTRACT = ROOT / "docs" / "specs" / "jev-contract.spec.html"
_spec = importlib.util.spec_from_file_location("jev_conformance_test", ROOT / "skill" / "review-spec" / "assets" / "jev.py")
jev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jev)

KEY = "sk-conformance-secret"
OUTCOMES = {"shown", "unsure", "silent-no", "escalated", "verified", "verifier-rejected", "oversize", "off", "unavailable"}
CASE_FIELDS = {"about", "question_set", "question", "reply", "expect"}


class Jev:
    """A local Jev decisions endpoint that answers with the current case's reply and keeps what it was sent."""

    def __init__(self):
        self.reply, self.sent, self.auth = None, [], []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                owner.sent.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                owner.auth.append(self.headers.get("Authorization"))
                failure = owner.reply.get("failure")
                data = b"" if failure else json.dumps(owner.reply["body"]).encode("utf-8")
                self.send_response(failure["status"] if failure else 200)
                if failure and failure.get("retry_after") is not None:
                    self.send_header("Retry-After", failure["retry_after"])
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d/decisions" % self.httpd.server_port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class Recording(jev.JudgmentStore):
    """The seam's store, keeping every record the ask writes."""

    def __init__(self):
        super().__init__()
        self.written = []

    def append(self, record):
        self.written.append(dict(record))
        return super().append(record)


def differences(expected, actual, field, exact=False):
    """(field, expected, actual) for every listed value that differs; record_id matches by its prefix.
    A request, a state, and probabilities match exactly, key for key, all the way down."""
    exact = exact or field.endswith(("request", "state", "probabilities"))
    if isinstance(expected, dict) and isinstance(actual, dict):
        for name in sorted(set(expected) | set(actual)) if exact else expected:
            path = field + "." + name
            if name not in actual:
                yield path, expected[name], "<missing>"
            elif name not in expected:
                yield path, "<missing>", actual[name]
            elif name == "record_id" and not exact:
                if not (isinstance(actual[name], str) and actual[name].startswith(expected[name])):
                    yield path, expected[name] + "*", actual[name]
            else:
                yield from differences(expected[name], actual[name], path, exact)
        return
    if expected != actual:
        yield field, expected, actual


def run_case(jev_server, name, case):
    """Run one case against the seam; returns the failures, each naming the case and the field that differs."""
    qset = jev.QuestionSet.from_mapping(case["question_set"], case["question_set"]["id"])
    asked = case["question"]
    question = {"kind": asked["id"], "state": asked["state"], "sources": asked["sources"]["addresses"],
                "revision": asked["sources"]["revision"]}
    if "labels" in asked:
        question["criteria"] = asked["labels"]
    store = Recording()
    if case.get("held"):
        jev.JudgmentStore.append(store, case["held"])
    reply = case["reply"]
    jev_server.reply, jev_server.sent, jev_server.auth = reply, [], []
    seam = jev.JevSeam({qset.id: qset}, provider=jev.OpenRouterProvider(KEY, endpoint=jev_server.url),
                       api_key="" if reply.get("no_key") else KEY, record_store=store, clock=lambda: asked["now"])
    now = datetime.fromisoformat(asked["now"].replace("Z", "+00:00")).timestamp()
    with patch.object(jev, "_wall", lambda: now):
        key = seam.key(question)
        seam.ask(question)
    # The Jev ask's own record is the first written; a general LLM's after an escalated one is the program's own.
    sent = jev_server.sent[0] if jev_server.sent else None
    record = store.written[0] if store.written else None
    expect = case["expect"]
    found = list(differences(expect["request"], sent, "expect.request"))
    found += differences(expect["key"], key, "expect.key")
    found += differences(expect["record"], record, "expect.record")
    if len(jev_server.sent) > 1:
        found.append(("calls", 1, len(jev_server.sent)))
    for auth in jev_server.auth:
        if auth != "Bearer " + KEY:
            found.append(("authorization", "Bearer <key>", auth))
    for each in store.written:
        if each.get("outcome") not in OUTCOMES:
            found.append(("record.outcome", "an outcome name", each.get("outcome")))
        if KEY in json.dumps(each):
            found.append(("record", "no provider key", "the provider key"))
    if sent is not None and KEY in json.dumps(sent):
        found.append(("request", "no provider key", "the provider key"))
    return ["%s: %s differs: expected %r, got %r" % (name, field, want, got) for field, want, got in found]


def _payload(case):
    """The request the seam builds for a case's question, captured before any size limit or send."""
    qset = jev.QuestionSet.from_mapping(case["question_set"], case["question_set"]["id"])
    built = []

    class Capture:
        def decide(self, payload):
            built.append(payload)
            raise RuntimeError("captured")

    seam = jev.JevSeam({qset.id: qset}, provider=Capture(), api_key=KEY, record_store=jev.JudgmentStore(),
                       clock=lambda: case["question"]["now"], max_input_tokens=10 ** 9)
    asked = case["question"]
    question = {"kind": asked["id"], "state": asked["state"], "sources": [], "revision": ""}
    if "labels" in asked:
        question["criteria"] = asked["labels"]
    seam.ask(question)
    return jev._canonical(built[0])


def request_size(case):
    return len(_payload(case).encode("utf-8"))


def len_chars(case):
    return len(_payload(case))


def cases():
    return {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in sorted(CASES.glob("*.json"))}


class ConformanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jev = Jev()

    @classmethod
    def tearDownClass(cls):
        cls.jev.close()

    def test_every_case_passes(self):
        """#acceptance-cases-pass, #acceptance-key-order, #acceptance-key-version, #acceptance-retry-given,
        #acceptance-retry-default, #acceptance-retry-after, #acceptance-outcome-names."""
        found = cases()
        self.assertTrue(found, "no conformance cases in %s" % CASES)
        for name, case in found.items():
            with self.subTest(case=name):
                self.assertEqual(run_case(self.jev, name, case), [])

    def test_a_break_names_the_case_and_field(self):
        """#acceptance-case-break: a request a case does not expect fails naming that case and the field."""
        case = cases()["shown"]
        case["expect"]["request"]["questions"]["change-type"]["type"] = "multiple"
        self.assertEqual(run_case(self.jev, "shown", case),
                         ["shown: expect.request.questions.change-type.type differs: expected 'multiple', got 'choice'"])
        case = cases()["shown"]
        del case["expect"]["request"]["questions"]["change-type"]["criteria"]["cosmetic"]["description"]
        del case["expect"]["record"]["answer"]["probabilities"]["cosmetic"]
        self.assertEqual(run_case(self.jev, "shown", case), [
            "shown: expect.request.questions.change-type.criteria.cosmetic.description differs: "
            "expected '<missing>', got 'The edit changes wording only.'",
            "shown: expect.record.answer.probabilities.cosmetic differs: expected '<missing>', got 0.08"])
        case = cases()["during-pause"]
        case["question"]["now"] = "2026-10-13T12:00:31.000Z"
        failures = run_case(self.jev, "during-pause", case)
        self.assertTrue(failures and all(f.startswith("during-pause: expect.") for f in failures), failures)

    def test_cases_are_plain_json(self):
        """#acceptance-cases-plain, #case-table: each case holds its own set, question, reply, and expectations,
        and cites a rule of the contract."""
        anchors = set(re.findall(r'data-anchor="([^"]+)"', CONTRACT.read_text(encoding="utf-8")))
        for name, case in cases().items():
            with self.subTest(case=name):
                self.assertLessEqual(CASE_FIELDS, set(case))
                self.assertLessEqual(set(case), CASE_FIELDS | {"held"})
                self.assertLessEqual({"id", "state", "sources", "now"}, set(case["question"]))
                self.assertEqual(case["question"]["id"], case["question_set"]["id"])
                cited = re.findall(r"#([a-z0-9-]+)", case["about"])
                self.assertTrue(cited and set(cited) <= anchors, cited)

    def test_cases_cover_the_contract(self):
        """#cases-cover."""
        found = cases()
        outcomes = {c["expect"]["record"]["outcome"] for c in found.values() if c["expect"]["record"]}
        self.assertLessEqual({"shown", "unsure", "escalated", "oversize", "off", "unavailable"}, outcomes)
        self.assertTrue(any("labels" in c["question"] for c in found.values()))
        self.assertTrue(any(c.get("held") and c["expect"]["request"] is None for c in found.values()))
        self.assertTrue(any(c.get("held") and c["expect"]["request"] for c in found.values()))
        keys = [c["expect"]["key"] for c in found.values()]
        self.assertTrue(any(keys.count(k) > 1 and len({json.dumps(c["question"]["state"]) for c in found.values()
                                                        if c["expect"]["key"] == k}) > 1 for k in keys))

    def test_cases_cover_the_size_estimate_edge(self):
        """#cases-cover, #request-oversize, #acceptance-oversize-estimate: one case is just over the limit by
        UTF-8 bytes / 2 though characters / 4 would send it, and is oversize; one is exactly at it, and is sent."""
        limit = jev.DEFAULT_MAX_INPUT_TOKENS
        edges = {}
        for name, case in cases().items():
            size = request_size(case)
            edges.setdefault((size + 1) // 2 - limit, []).append((name, size, case))
        over = [(n, c) for n, size, c in edges.get(1, []) if (len_chars(c) + 3) // 4 <= limit]
        self.assertTrue(over, "no case just over the limit that characters / 4 would send")
        self.assertTrue(all(c["expect"]["request"] is None and c["expect"]["record"]["outcome"] == "oversize"
                            for _, c in over), over and [n for n, _ in over])
        under = edges.get(0, [])
        self.assertTrue(under, "no case exactly at the limit")
        self.assertTrue(all(c["expect"]["request"] for _, _, c in under), [n for n, _, _ in under])


class QuestionSetTest(unittest.TestCase):
    def test_every_question_set_is_valid(self):
        """#acceptance-qset-invalid, #qset-version: a set whose version is not a whole number fails, naming its file."""
        for path in sorted(SETS.glob("*.json")):
            with self.subTest(path=path.name):
                try:
                    jev.QuestionSet.from_mapping(json.loads(path.read_text(encoding="utf-8")), path.stem)
                except ValueError as exc:
                    self.fail("%s: %s" % (path.name, exc))

    def test_a_version_not_whole_is_rejected(self):
        for version in (1.5, 2.0, "2", True, None, -1):
            with self.subTest(version=version):
                raw = {"id": "s", "instructions": "?", "labels": [], "threshold": 0.4}
                if version is not None:
                    raw["version"] = version
                with self.assertRaises(ValueError):
                    jev.QuestionSet.from_mapping(raw, "s")


if __name__ == "__main__":
    unittest.main()
