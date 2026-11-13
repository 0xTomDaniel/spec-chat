"""OpenRouter 429 under the one retry rule (jev-contract #retry, project-rules #q-fallback): one call per
ask, never a sleep in a worker; the record's pause is the provider's given wait, else RETRY_PAUSE. A refused key
(401, 402, 403) pauses every question (jev-contract #retry-refused)."""

import importlib.util
import json
import threading
import time
import unittest
import unittest.mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("jev_rate_limit_test", ROOT / "skill" / "review-spec" / "assets" / "jev.py")
jev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jev)


def no_spec(*served):
    """The inputs of a read whose questions the test stubs: no spec of its own to read, and these served spec paths,
    so each question's cited clause has its repo path (jev.py JevService._item_where)."""
    facts = {"current": b"", "old": b"", "root": "", "rel": "", "resolved_head": None, "head": "working-tree",
             "base_commit": None, "served": [{"path": path, "spec": path, "source": b""} for path in served]}
    return lambda *args: facts


class FakeOpenRouter:
    """Answers each path with its queued failures first, 429s unless another status (with that Retry-After), then 200."""

    def __init__(self, limited, retry_after="1", status=429):
        self.limited, self.retry_after, self.status, self.hits = dict(limited), retry_after, status, []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.hits.append(self.path)
                if owner.limited.get(self.path, 0) > 0:
                    owner.limited[self.path] -= 1
                    self.send_response(owner.status)
                    if owner.retry_after is not None:
                        self.send_header("Retry-After", owner.retry_after)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path == "/decisions":
                    kind = next(iter(body["questions"]))
                    answer = {"answers": {kind: {"choice": "this feature", "confidence": 0.1}}}
                else:
                    answer = {"choices": [{"message": {"content": json.dumps({"choice": "every feature"})}}]}
                data = json.dumps(answer).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def scope_set(fallback=True):
    return jev.QuestionSet("scope", 1, "Scope?", {"every feature": "all", "this feature": "one"}, 0.4, fallback)


def question(text="Every change adds its onboarding section."):
    return {"kind": "scope", "state": {"criterion": text}, "sources": ["a.spec.html#c"], "revision": None}


PERSISTENT = 10 ** 6


class RateLimit(unittest.TestCase):
    def serve(self, limited, retry_after="30"):
        server = FakeOpenRouter(limited, retry_after)
        self.addCleanup(server.close)
        provider = jev.OpenRouterProvider("test-key", endpoint=server.url + "/decisions")
        return server, provider

    def ask(self, seam, q=None, at=1000.0):
        with unittest.mock.patch.object(jev, "_wall", lambda: at):
            started = time.monotonic()
            record = seam.ask(q or question())
            self.assertLess(time.monotonic() - started, 1.0)  # no wait in the worker
            return record, jev.pause_left(record)

    def test_persistent_429_is_one_call_and_the_given_wait_is_the_pause(self):
        server, provider = self.serve({"/decisions": PERSISTENT}, "30")
        record, pause = self.ask(jev.JevSeam({"scope": scope_set(False)}, provider=provider, api_key="test-key"))
        self.assertEqual(record["outcome"], "unavailable")
        self.assertEqual(server.hits, ["/decisions"])
        self.assertEqual(pause, 30.0)

    def test_given_wait_is_never_capped(self):
        server, provider = self.serve({"/decisions": PERSISTENT}, "600")
        record, pause = self.ask(jev.JevSeam({"scope": scope_set(False)}, provider=provider, api_key="test-key"))
        self.assertEqual((len(server.hits), pause), (1, 600.0))

    def test_429_without_a_wait_pauses_the_default(self):
        for header in (None, "soon"):
            server, provider = self.serve({"/decisions": PERSISTENT}, header)
            record, pause = self.ask(jev.JevSeam({"scope": scope_set(False)}, provider=provider, api_key="test-key"))
            self.assertEqual(record["outcome"], "unavailable")
            self.assertEqual((len(server.hits), pause), (1, jev.RETRY_PAUSE))

    def test_http_date_wait(self):
        self.assertEqual(jev.given_wait("Wed, 21 Oct 2015 07:28:00 GMT", now=lambda: 1445412470.0), 10.0)
        self.assertIsNone(jev.given_wait("soon"))
        self.assertEqual(jev.given_wait("-5"), 0.0)

    def test_escalated_rule_check_is_one_call_per_stage(self):
        """Below-threshold Jev answer escalates to general LLM; LLM failure records unavailable with pause."""
        server, provider = self.serve({}, "45")
        # The general LLM (complete) fails; the Jev decision (decide) succeeds but below threshold.
        fail_count = [0]
        orig_complete = None

        class EscalatingProvider:
            """Jev decide through the HTTP server; complete raises to simulate LLM failure."""
            def decide(self, payload):
                return provider.decide(payload)
            def complete(self, payload):
                fail_count[0] += 1
                raise jev.ProviderFailure("rate limited", 45.0)

        ep = EscalatingProvider()
        seam = jev.JevSeam({"scope": scope_set()}, provider=ep, api_key="test-key")
        record, pause = self.ask(seam)
        self.assertEqual((record["outcome"], record["escalated"]), ("unavailable", True))
        self.assertEqual(fail_count[0], 1)
        self.assertEqual(pause, 45.0)
        # asked during its pause: nothing is called (jev-contract #retry-before)
        self.ask(seam, at=1044.0)
        self.assertEqual(fail_count[0], 1)
        # asked again after its pause: only the general LLM, once
        record, pause = self.ask(seam, at=1045.0)
        self.assertEqual(fail_count[0], 2)
        self.assertEqual(pause, 45.0)
        # Now LLM succeeds
        ep.complete = lambda payload: {"choices": [{"message": {"content": json.dumps({"choice": "every feature"})}}]}
        record, _ = self.ask(seam, at=1090.0)
        self.assertEqual((record["outcome"], record["answer"]["label"]), ("shown", "every feature"))

    def test_other_errors_are_one_call_and_pause_the_default(self):
        provider = jev.OpenRouterProvider("k", endpoint="http://127.0.0.1:9/decisions")
        seam = jev.JevSeam({"scope": scope_set(False)}, provider=provider, api_key="k")
        failing = unittest.mock.Mock(side_effect=jev.HTTPError(provider.endpoint, 500, "boom", {}, None))
        with unittest.mock.patch.object(jev, "urlopen", failing):
            record, pause = self.ask(seam)
        self.assertEqual((failing.call_count, pause), (1, jev.RETRY_PAUSE))

    def test_stop_returns_promptly_with_asks_rate_limited(self):
        server, provider = self.serve({"/decisions": PERSISTENT}, "30")
        with tempfile.TemporaryDirectory() as tmp:
            service = jev.JevService(state_dir=tmp, provider=provider, api_key="test-key")
            questions = [{"kind": "type", "id": str(i), "state": {"after": str(i)}, "sources": [], "revision": "head"}
                         for i in range(20)]
            service.questions, service._read_facts = lambda *args, **kwargs: questions, no_spec()
            first = service.response({}, "", "", "base", [])
            self.assertTrue(all(item["state"] == "pending" for item in first["items"]))
            started = time.monotonic()
            service.stop()
            self.assertLess(time.monotonic() - started, 2.0)
        self.assertLessEqual(len(server.hits), 20)



class Recording(jev.JudgmentStore):
    """An in-memory store keeping every record written."""

    def __init__(self):
        super().__init__()
        self.written = []

    def append(self, record):
        self.written.append(dict(record))
        return super().append(record)


class Probed:
    """Fails each call with the next queued status, else answers; a call waits while `release` is clear."""

    def __init__(self, *statuses):
        self.statuses, self.calls = list(statuses), 0
        self.entered, self.release = threading.Event(), threading.Event()
        self.release.set()

    def decide(self, payload):
        self.calls += 1
        self.entered.set()
        self.release.wait(10)
        status = self.statuses.pop(0) if self.statuses else None
        if status:
            raise jev.ProviderFailure("failed", None, status)
        return {"answers": {next(iter(payload["questions"])): {"choice": "this feature", "confidence": 0.9}}}


class RefusedKey(unittest.TestCase):
    """jev-contract #retry-refused: a failure whose status is 401, 402, or 403 refuses the key, not one question."""

    def test_a_refused_key_pauses_every_question(self):
        """#acceptance-retry-refused, #core-status: through the real client, the first ask's record is unavailable,
        holds the provider's status and the instant 5 minutes later, or the given wait; another question asked
        before the pause ends calls nothing and writes no record."""
        for status, given, pause in ((401, None, 300.0), (402, "120", 120.0), (403, None, 300.0)):
            with self.subTest(status=status):
                server = FakeOpenRouter({"/decisions": PERSISTENT}, given, status)
                self.addCleanup(server.close)
                store = Recording()
                seam = jev.JevSeam({"scope": scope_set(False)}, provider=jev.OpenRouterProvider(
                    "test-key", endpoint=server.url + "/decisions"), api_key="test-key", record_store=store)
                with unittest.mock.patch.object(jev, "_wall", lambda: 1000.0):
                    record = seam.ask(question())
                self.assertEqual(jev.REFUSED_PAUSE, 300.0)
                self.assertEqual((record["outcome"], record["status"]), ("unavailable", status))
                self.assertEqual(record["retry_at"], jev._instant(1000.0 + pause))
                with unittest.mock.patch.object(jev, "_wall", lambda: 1000.0 + pause - 1):
                    self.assertIsNone(seam.ask(question("Every change names its owner.")))
                self.assertEqual((len(server.hits), len(store.written)), (1, 1))

    def test_other_failures_keep_their_status_and_pause_one_question(self):
        """#core-status, #retry-before: a 429 or a 500 holds its status and pauses its own key only."""
        for status in (429, 500):
            with self.subTest(status=status):
                server = FakeOpenRouter({"/decisions": 1}, None, status)
                self.addCleanup(server.close)
                seam = jev.JevSeam({"scope": scope_set(False)}, provider=jev.OpenRouterProvider(
                    "test-key", endpoint=server.url + "/decisions"), api_key="test-key")
                record, pause = self.ask(seam)
                self.assertEqual((record["status"], pause), (status, jev.RETRY_PAUSE))
                other, _ = self.ask(seam, question("Every change names its owner."))
                self.assertEqual((other["outcome"], len(server.hits)), ("unsure", 2))  # asked and answered

    def test_no_status_without_an_http_answer(self):
        """#core-status: a failure that gave no status, as a refused connection, records none."""
        seam = jev.JevSeam({"scope": scope_set(False)}, api_key="k",
                           provider=jev.OpenRouterProvider("k", endpoint="http://127.0.0.1:9/decisions"))
        record, pause = self.ask(seam)
        self.assertEqual((record["outcome"], pause), ("unavailable", jev.RETRY_PAUSE))
        self.assertNotIn("status", record)

    def ask(self, seam, q=None, at=1000.0):
        with unittest.mock.patch.object(jev, "_wall", lambda: at):
            record = seam.ask(q or question())
            return record, jev.pause_left(record)

    def test_after_the_pause_one_probe_calls_and_its_answer_restarts_or_ends_the_pause(self):
        """#retry-refused: after the pause one ask calls while every other still calls nothing; a refusal starts a
        new pause, and any other answer ends it."""
        provider, store, now = Probed(403, 403, 500), Recording(), [1000.0]
        seam = jev.JevSeam({"scope": scope_set(False)}, provider=provider, api_key="k", record_store=store)
        asks = [question("Clause %d." % index) for index in range(4)]
        with unittest.mock.patch.object(jev, "_wall", lambda: now[0]):
            self.assertEqual(seam.ask(asks[0])["status"], 403)
            self.assertEqual(seam.pause.paused(), {"paused_until": jev._instant(1300.0), "status": 403})
            now[0] = 1299.0
            self.assertIsNone(seam.ask(asks[1]))
            now[0] = 1301.0
            self.assertEqual(seam.pause.paused(), {})  # the pause has ended; its probe has yet to answer
            provider.release.clear()
            probe = threading.Thread(target=seam.ask, args=(asks[1],))
            probe.start()
            self.assertTrue(provider.entered.wait(5))
            self.assertIsNone(seam.ask(asks[2]))  # the probe calls; every other ask calls nothing
            self.assertEqual(provider.calls, 2)
            provider.release.set()
            probe.join(5)
            self.assertEqual(store.written[-1]["retry_at"], jev._instant(1601.0))  # a refusal starts a new pause
            now[0] = 1600.0
            self.assertIsNone(seam.ask(asks[2]))
            self.assertEqual(provider.calls, 2)
            now[0] = 1602.0
            self.assertEqual(seam.ask(asks[2])["status"], 500)  # any other answer ends it
            self.assertEqual(seam.pause.paused(), {})
            self.assertEqual(seam.ask(asks[3])["outcome"], "shown")
            self.assertEqual((provider.calls, len(store.written)), (4, 4))


if __name__ == "__main__":
    unittest.main()
