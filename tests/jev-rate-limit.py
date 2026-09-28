"""OpenRouter 429 under the one retry rule (jev-suggestions #state-error, project-rules #q-fallback): one call per
ask, never a sleep in a worker; the record's pause is the provider's given wait, else RETRY_PAUSE."""

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


PERSISTENT = 10 ** 6


class RateLimit(unittest.TestCase):
    def serve(self, limited, retry_after="30"):
        server = FakeOpenRouter(limited, retry_after)
        self.addCleanup(server.close)
        provider = jev.OpenRouterProvider("test-key", endpoint=server.url + "/decisions",
                                          chat_endpoint=server.url + "/chat")
        return server, provider

    def ask(self, seam, q=None):
        with unittest.mock.patch.object(jev, "_wall", lambda: 1000.0):
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
        server, provider = self.serve({"/chat": PERSISTENT}, "45")
        seam = jev.JevSeam({"scope": scope_set()}, provider=provider, api_key="test-key")
        record, pause = self.ask(seam)
        self.assertEqual((record["outcome"], record["escalated"]), ("unavailable", True))
        self.assertEqual(server.hits, ["/decisions", "/chat"])
        self.assertEqual(pause, 45.0)
        # asked again after its pause: only the general LLM, once
        record, pause = self.ask(seam)
        self.assertEqual(server.hits, ["/decisions", "/chat", "/chat"])
        self.assertEqual(pause, 45.0)
        server.limited["/chat"] = 0
        record, _ = self.ask(seam)
        self.assertEqual((record["outcome"], record["answer"]["label"]), ("shown", "every feature"))
        self.assertEqual(server.hits, ["/decisions"] + ["/chat"] * 3)

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
            service.questions = lambda *args, **kwargs: questions
            first = service.response({}, "", "", "base", [])
            self.assertTrue(all(item["state"] == "pending" for item in first["items"]))
            started = time.monotonic()
            service.stop()
            self.assertLess(time.monotonic() - started, 2.0)
        self.assertLessEqual(len(server.hits), 20)


if __name__ == "__main__":
    unittest.main()
