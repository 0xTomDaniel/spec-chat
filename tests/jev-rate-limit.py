"""OpenRouter 429: wait per Retry-After, bounded, and retry, for Jev and the general LLM fallback
(project-rules #q-fallback, #bootstrap-table: a first-registration warm-up finishes in one start)."""

import importlib.util
import json
import threading
import unittest
import unittest.mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("jev_rate_limit_test", ROOT / "tools" / "jev.py")
jev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jev)


class FakeOpenRouter:
    """Answers each path with its queued 429s first (with that Retry-After), then 200."""

    def __init__(self, limited, retry_after="1"):
        self.limited, self.retry_after, self.hits = dict(limited), retry_after, []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.hits.append(self.path)
                if owner.limited.get(self.path, 0) > 0:
                    owner.limited[self.path] -= 1
                    self.send_response(429)
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


class RateLimit(unittest.TestCase):
    def serve(self, limited, retry_after="1"):
        server = FakeOpenRouter(limited, retry_after)
        self.addCleanup(server.close)
        waits = []
        provider = jev.OpenRouterProvider("test-key", endpoint=server.url + "/decisions",
                                          chat_endpoint=server.url + "/chat", sleep=waits.append)
        return server, provider, waits

    def test_jev_and_fallback_wait_and_retry(self):
        server, provider, waits = self.serve({"/decisions": 2, "/chat": 2}, "3")
        seam = jev.JevSeam({"scope": scope_set()}, provider=provider, api_key="test-key")
        record = seam.ask(question())
        self.assertEqual(record["outcome"], "shown")
        self.assertTrue(record["escalated"])
        self.assertEqual(record["answer"]["label"], "every feature")
        self.assertEqual(server.hits, ["/decisions"] * 3 + ["/chat"] * 3)
        self.assertEqual(waits, [3.0] * 4)

    def test_wait_is_bounded(self):
        server, provider, waits = self.serve({"/decisions": 1}, "86400")
        provider.decide({"model": jev.MODEL, "questions": {"scope": {}}, "state": {}})
        self.assertEqual(waits, [jev.RATE_LIMIT_MAX_WAIT])

    def test_missing_retry_after_waits_default(self):
        server, provider, waits = self.serve({"/chat": 1}, None)
        provider.complete({"model": "m", "messages": []})
        self.assertEqual(waits, [jev.RATE_LIMIT_DEFAULT_WAIT])

    def test_http_date(self):
        self.assertEqual(jev.retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT", now=lambda: 1445412470.0), 10.0)
        self.assertEqual(jev.retry_after_seconds("soon"), jev.RATE_LIMIT_DEFAULT_WAIT)
        self.assertEqual(jev.retry_after_seconds("-5"), 0.0)

    def test_retries_are_bounded_then_unavailable(self):
        server, provider, waits = self.serve({"/decisions": 1000}, "0")
        seam = jev.JevSeam({"scope": scope_set(False)}, provider=provider, api_key="test-key")
        record = seam.ask(question())
        self.assertEqual(record["outcome"], "unavailable")
        # The seam's one retry, each bounded by the provider's rate-limit retries.
        self.assertEqual(len(server.hits), 2 * (jev.RATE_LIMIT_RETRIES + 1))

    def test_last_retry_after_is_the_records_pause(self):
        """jev-suggestions #state-error: past the bounded retries, the provider's wait (uncapped) is the pause."""
        server, provider, waits = self.serve({"/decisions": 1000}, "600")
        seam = jev.JevSeam({"scope": scope_set(False)}, provider=provider, api_key="test-key")
        with unittest.mock.patch.object(jev, "_wall", lambda: 1000.0):
            record = seam.ask(question())
            self.assertEqual(record["outcome"], "unavailable")
            self.assertEqual(jev.pause_left(record), 600.0)
        with self.assertRaises(jev.ProviderWait) as raised:
            provider.decide({})
        self.assertEqual(raised.exception.wait, 600.0)

    def test_no_given_wait_pauses_the_default(self):
        waits = []
        provider = jev.OpenRouterProvider("k", endpoint="http://127.0.0.1:9/decisions", sleep=waits.append)
        seam = jev.JevSeam({"scope": scope_set(False)}, provider=provider, api_key="k")
        with unittest.mock.patch.object(jev, "urlopen", side_effect=jev.HTTPError(provider.endpoint, 500, "boom", {}, None)), \
                unittest.mock.patch.object(jev, "_wall", lambda: 1000.0):
            record = seam.ask(question())
            self.assertEqual(jev.pause_left(record), jev.RETRY_PAUSE)

    def test_other_errors_do_not_wait(self):
        waits = []
        provider = jev.OpenRouterProvider("k", endpoint="http://127.0.0.1:9/decisions", sleep=waits.append)
        with unittest.mock.patch.object(jev, "urlopen", side_effect=jev.HTTPError(provider.endpoint, 500, "boom", {}, None)):
            with self.assertRaises(RuntimeError):
                provider.decide({})
        self.assertEqual(waits, [])


if __name__ == "__main__":
    unittest.main()
