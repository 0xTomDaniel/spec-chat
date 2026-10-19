"""The review service keeps, places, and wakes a host page's comments (comment-layer #subjects, #scope, #scope-store,
#load; review-service #store-hosted; host-wake #wake-rule-poll, #wake-rule-message, #wake-rule-log)."""

import contextlib
import http.client
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "skill" / "review-spec" / "assets"
sys.path.insert(0, str(ASSETS))
_serve_spec = importlib.util.spec_from_file_location("review_serve_hosted_test", ASSETS / "review-serve.py")
serve = importlib.util.module_from_spec(_serve_spec)
_serve_spec.loader.exec_module(serve)
_wake_spec = importlib.util.spec_from_file_location("review_serve_hosted_wake", ROOT / "tests" / "review-host-wake.py")
_wake_module = importlib.util.module_from_spec(_wake_spec)
_wake_spec.loader.exec_module(_wake_module)
FakeWaker = _wake_module.FakeWaker

PAGE = ('<!doctype html><title>Demo</title><article class="spec">'
        '<p data-anchor="acceptance-a">Alpha criterion text.</p>'
        '<p data-anchor="acceptance-b">Beta criterion text.</p></article>\n')
LANE, SCOPE = "ann1", "operator-desk"
CRITERION = "project/spec-chat::docs/specs/demo.spec.html#acceptance-a"
TURN = "agent-turn:w1:p3:42"


def git(root, *args):
    return subprocess.check_output(("git", "-C", str(root), *args), text=True).strip()


def comment(cid, **fields):
    return {"event": "comment", "id": cid, "browser": "b1", "author": "Mango", "text": "Question: why?", **fields}


class HostedCase(unittest.TestCase):
    """A box with a lane's spec row and an operator's scope row, its registry in the service state."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="spec-chat-hosted-")
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.repo = self.dir / "repo"
        self.spec = self.repo / "docs" / "specs" / "demo.spec.html"
        self.spec.parent.mkdir(parents=True)
        self.spec.write_text(PAGE, encoding="utf-8")
        git(self.dir, "init", "-q", "-b", "main", str(self.repo))
        git(self.repo, "add", ".")
        git(self.repo, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qm", "spec")
        self.state = self.dir / "state"
        self.state.mkdir()
        self.registry = self.state / "registry.toml"
        self.spec_row = (
            "[[resource]]\n"
            f'id = "spec:{LANE}:spec-chat::docs/specs/demo.spec.html"\nslug = "{LANE}"\nproject = "spec-chat"\n'
            f'root = "{self.repo}"\nnarrow_root = "{self.repo}/docs"\nspec = "docs/specs/demo.spec.html"\n'
            f'path = "{LANE}/docs/specs/demo.spec.html"\nbase = "HEAD"\nowner = "w1:pLane"\nchecker = "w1:pLane"\n'
            'cursor_name = ".cursor-owner"\nregistered_at = "2026-10-19T10:00:00Z"\n'
        )
        self.scope_row = (
            "[[resource]]\n"
            f'id = "scope:{SCOPE}"\nslug = "{SCOPE}"\nowner = "w1:pOps"\nchecker = "w1:pOps"\n'
            'cursor_name = ".cursor-owner"\nregistered_at = "2026-10-19T11:00:00Z"\n'
        )
        self.write_registry(self.spec_row, self.scope_row)
        self.mount_state = serve.MountState(self.records(), self.registry)
        self.review = Path(str(self.spec) + ".review")

    def write_registry(self, *rows, stamp=None):
        self.registry.write_text("\n".join(rows), encoding="utf-8")
        if stamp is not None:  # a rewrite within one file-time tick still reads as changed
            os.utime(self.registry, ns=(stamp, stamp))

    def records(self):
        return serve._resource_records(serve._registry_document(str(self.registry)), trust=True)

    def rows(self):
        return self.mount_state.rows()


class PlacementTest(HostedCase):
    """comment-layer #proof-seam: placement by subject and scope as pure functions over a fixture registry."""

    def test_criterion_of_a_served_spec_goes_to_its_spool_any_other_subject_to_hosted_threads(self):
        """comment-layer #proof-first-test."""
        on_spec = serve.place_subject(self.rows(), str(self.state), LANE, CRITERION)
        self.assertEqual((on_spec["review"], on_spec["anchor"], on_spec["store"]),
                         (str(self.spec) + ".review", "acceptance-a", LANE + "/docs/specs/demo.spec.html"))
        hosted = serve.place_subject(self.rows(), str(self.state), LANE, TURN)
        self.assertEqual((hosted["review"], hosted["root"], hosted["anchor"], hosted["store"]),
                         (str(self.state / "comments" / LANE), str(self.state), None, serve.HOSTED))

    def test_a_criterion_no_row_of_the_scope_serves_is_hosted_and_compared_whole(self):
        """comment-layer #subjects-other: another scope, another project, or a stale row serves no spec here."""
        for scope, subject in ((SCOPE, CRITERION), (LANE, CRITERION.replace("spec-chat", "other")),
                               (LANE, CRITERION.replace("demo", "absent"))):
            placed = serve.place_subject(self.rows(), str(self.state), scope, subject)
            self.assertEqual((placed["store"], placed["subject"]), (serve.HOSTED, subject), subject)
        self.spec.unlink()
        stale = serve.MountState(self.records(), self.registry).rows()
        self.assertEqual(serve.place_subject(stale, str(self.state), LANE, CRITERION)["store"], serve.HOSTED)

    def test_no_row_no_subject_no_place(self):
        """comment-layer #anchor-scope: an unregistered scope gets no control, so nothing is kept for it."""
        self.assertIsNone(serve.place_subject(self.rows(), str(self.state), "unregistered", TURN))
        self.assertIsNone(serve.place_subject(self.rows(), str(self.state), LANE, ""))

    def test_kept_event_carries_anchor_on_a_spec_and_subject_elsewhere_never_a_version(self):
        """review-state #model-fields-hosted."""
        sent = comment("c1", subject=CRITERION, version="f" * 64, kind="criterion", label="Alpha", **{"from": "/x"})
        on_spec = serve.placed_event(sent, serve.place_subject(self.rows(), str(self.state), LANE, CRITERION))
        self.assertEqual(on_spec, {**comment("c1", kind="criterion", label="Alpha", **{"from": "/x"}),
                                   "anchorId": "acceptance-a"})
        sent = comment("c2", subject="ignored", anchorId="ignored", kind="agent turn")
        hosted = serve.placed_event(sent, serve.place_subject(self.rows(), str(self.state), LANE, TURN))
        self.assertEqual(hosted, {**comment("c2", kind="agent turn"), "subject": TURN})
        reply = {"event": "reply", "id": "r1", "respondsTo": "c2", "text": "ok"}
        self.assertIs(serve.placed_event(reply, serve.place_subject(self.rows(), str(self.state), LANE, TURN)), reply)

    def test_subject_events_are_its_threads_by_reference(self):
        """comment-layer #subjects-table: a thread shows on every page anchoring its subject, replies and all."""
        def event(actor, name, body):
            return {"actor": actor, "name": name, "body": body}
        events = [
            event("human", "1-comment-c1.json", comment("c1", subject=TURN)),
            event("human", "2-comment-c2.json", comment("c2", subject="agent-turn:other")),
            event("agent", "3-reply-r1.json", {"event": "reply", "id": "r1", "respondsTo": "c1"}),
            event("human", "4-edit-e1.json", {"event": "edit", "id": "e1", "supersedes": "r9"}),
            event("human", "5-reply-r9.json", {"event": "reply", "id": "r9", "respondsTo": "r1"}),
            event("agent", "6-status-s1.json", {"event": "status", "id": "s1", "threadId": "c1"}),
            event("human", "7-handoff-h1.json", {"event": "handoff", "id": "h1", "events": ["c1"]}),
            event("human", "8-reply-x.json", "not an event"),
        ]
        placed = serve.place_subject(self.rows(), str(self.state), LANE, TURN)
        self.assertEqual([e["body"]["id"] for e in serve.subject_events(events, placed)],
                         ["c1", "r1", "e1", "r9", "s1"])


class HostedServiceCase(HostedCase):
    def serve(self, bind="127.0.0.1"):
        server = serve.ReviewThreadingHTTPServer((bind, 0), serve.MountHandler)
        server.mount_state = self.mount_state
        server.state_dir = None
        server.wake_controller = types.SimpleNamespace(status=lambda _resource: None)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_port
        return server

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.request(method, path, body=None if body is None else json.dumps(body), headers=headers or {})
            response = connection.getresponse()
            data = response.read()
            return response.status, {name.lower(): value for name, value in response.getheaders()}, data
        finally:
            connection.close()

    def scoped(self, scope, *subjects, name=None):
        query = [("scope", scope)] + [("subject", subject) for subject in subjects] + ([("name", name)] if name else [])
        return "/api/events?" + urllib.parse.urlencode(query)

    def save(self, scope, subject, event, origin=None):
        name = "%d-%s-%s.json" % (len(event["id"]) * 1000 + sum(map(ord, event["id"])), event["event"], event["id"])
        headers = {"Content-Type": "application/json", **({"Origin": origin} if origin else {})}
        status, headers, body = self.request("POST", self.scoped(scope, subject, name=name), event, headers)
        return status, json.loads(body) if body else None, headers

    def read(self, scope, *subjects):
        status, _, body = self.request("GET", self.scoped(scope, *subjects))
        return status, json.loads(body)


class HostedRoutesTest(HostedServiceCase):
    def setUp(self):
        super().setUp()
        self.serve()

    def test_host_comments_are_kept_where_their_subject_places_them(self):
        """comment-layer #subjects; review-service #store-hosted: comments/<scope>/human in the service state."""
        anchor = {"kind": "criterion", "label": "Alpha", "from": "https://host.example/lanes/ann1"}
        status, answer, _ = self.save(LANE, CRITERION, comment("c1", subject=CRITERION, **anchor))
        self.assertEqual((status, answer["ok"]), (200, True))
        kept = json.loads((self.review / "human" / answer["name"]).read_text())
        self.assertEqual(kept, {**comment("c1", **anchor), "anchorId": "acceptance-a"})
        status, answer, _ = self.save(LANE, TURN, comment("c2", subject=TURN, kind="agent turn"))
        self.assertEqual(status, 200)
        kept = json.loads((self.state / "comments" / LANE / "human" / answer["name"]).read_text())
        self.assertEqual(kept, {**comment("c2", kind="agent turn"), "subject": TURN})
        self.assertFalse((self.review / "human" / answer["name"]).exists())

    def test_reading_by_scope_and_subject_gives_each_thread_with_its_store(self):
        """comment-layer #acceptance-host-reply, #acceptance-host-same-subject (service side): every page anchoring
        the subject in the scope reads one thread, the owner's reply under the comment."""
        self.save(LANE, TURN, comment("c2", subject=TURN, kind="agent turn"))
        self.save(LANE, "agent-turn:other", comment("c3", subject="agent-turn:other", kind="agent turn"))
        agent = self.state / "comments" / LANE / "agent"
        agent.mkdir()
        (agent / "9000-reply-r1.json").write_text(json.dumps(
            {"event": "reply", "id": "r1", "respondsTo": "c2", "status": "acknowledged", "text": "Because."}))
        status, events = self.read(LANE, TURN)
        self.assertEqual(status, 200)
        self.assertEqual([(e["body"]["id"], e["actor"], e["store"], e["subject"]) for e in events],
                         [("c2", "human", serve.HOSTED, TURN), ("r1", "agent", serve.HOSTED, TURN)])

    def test_one_thread_between_the_spec_page_and_a_host_page(self):
        """comment-layer #acceptance-one-thread-spec, #acceptance-one-thread-host (service side)."""
        self.save(LANE, CRITERION, comment("c1", subject=CRITERION, kind="criterion", **{"from": "https://h.example/"}))
        spool = "/api/events?dir=" + urllib.parse.quote(LANE + "/docs/specs/demo.spec.html.review")
        page = self.request("POST", spool + "&name=5000-comment-p1.json",
                            comment("p1", anchorId="acceptance-a", version="0" * 64), {"Content-Type": "application/json"})
        self.assertEqual(page[0], 200)
        self.request("POST", spool + "&name=5001-comment-p2.json", comment("p2", anchorId="acceptance-b"),
                     {"Content-Type": "application/json"})
        status, _, body = self.request("GET", spool)
        on_page = {e["body"]["id"]: e for e in json.loads(body)}
        self.assertEqual(on_page["c1"]["body"]["kind"], "criterion")
        self.assertEqual(on_page["c1"]["body"]["from"], "https://h.example/")
        self.assertEqual(on_page["c1"]["place"]["anchorId"], "acceptance-a")
        status, events = self.read(LANE, CRITERION)
        self.assertEqual(sorted(e["body"]["id"] for e in events), ["c1", "p1"])
        self.assertEqual({e["store"] for e in events}, {LANE + "/docs/specs/demo.spec.html"})

    def test_hand_off_goes_into_each_store_listing_its_drafts_and_never_accepts(self):
        """comment-layer #ui-handoff, #acceptance-host-handoff-two (service side); a host page shows no Accept spec,
        so a hand-off with no list is refused."""
        self.save(LANE, CRITERION, comment("c1", subject=CRITERION, kind="criterion"))
        self.save(LANE, TURN, comment("c2", subject=TURN, kind="agent turn"))
        for subject, listed in ((CRITERION, ["c1"]), (TURN, ["c2"])):
            status, _, _ = self.save(LANE, subject, {"event": "handoff", "id": "h1", "events": listed})
            self.assertEqual(status, 200)
        status, answer, _ = self.save(LANE, CRITERION, {"event": "handoff", "id": "h2"})
        self.assertEqual((status, answer["error"]), (400, "a host page's hand-off lists its events"))
        self.assertEqual(json.loads(next((self.review / "human").glob("*-handoff-h1.json")).read_text())["events"],
                         ["c1"])
        self.assertEqual(json.loads(next((self.state / "comments" / LANE / "human").glob("*-handoff-h1.json"))
                                    .read_text())["events"], ["c2"])
        status, events = self.read(LANE, CRITERION, TURN)
        self.assertEqual(sorted((e["body"]["id"], e["store"]) for e in events if e["body"]["event"] == "handoff"),
                         [("h1", LANE + "/docs/specs/demo.spec.html"), ("h1", serve.HOSTED)])

    def test_scope_rows_are_known_at_each_read(self):
        """comment-layer #scope-rule: a scope with a row answers, one without is unknown, and a removed row's scope
        turns unknown with no restart."""
        self.assertEqual(self.read(SCOPE), (200, []))
        self.assertEqual(self.read("unregistered", TURN)[0], 404)
        self.assertEqual(self.save("unregistered", TURN, comment("c1", subject=TURN))[0], 404)
        self.assertFalse((self.state / "comments" / "unregistered").exists())
        self.write_registry(self.spec_row, stamp=os.stat(self.registry).st_mtime_ns + 5_000_000_000)
        self.assertEqual(self.read(SCOPE)[0], 404)
        self.assertEqual(self.read(LANE)[0], 200)

    def test_review_index_lists_no_scope_row(self):
        """comment-layer #acceptance-scope-row-index; #scope-row: a scope row serves no page."""
        status, _, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"/" + LANE.encode() + b"/docs/specs/demo.spec.html", body)
        self.assertNotIn(SCOPE.encode(), body)
        self.assertEqual(self.request("GET", "/" + SCOPE + "/")[0], 404)

    def test_comment_layer_is_served_while_its_file_is_present(self):
        """comment-layer #load; review-service #published-table: /static/comment-layer.js."""
        layer = self.dir / "comment-layer.js"
        with patch.object(serve, "COMMENT_LAYER", layer):
            self.assertEqual(self.request("GET", "/static/comment-layer.js")[0], 404)
            layer.write_text("/* layer */\n", encoding="utf-8")
            status, headers, body = self.request("GET", "/static/comment-layer.js")
        self.assertEqual((status, body), (200, b"/* layer */\n"))
        self.assertTrue(headers["content-type"].startswith("text/javascript"))

    def test_comment_layer_is_the_spec_page_runtime(self):
        """comment-layer #load, #ui-same: the layer runs the spec page's comment code, not a copy of it."""
        status, _, body = self.request("GET", "/static/comment-layer.js")
        self.assertEqual((status, body), (200, (ASSETS / "viz" / "runtime.js").read_bytes()))


class HostedOtherOriginTest(HostedServiceCase):
    """comment-layer #acceptance-other-origin: a host page of another site loads the layer from a public service."""

    SITE = "https://host.example"

    def test_public_service_answers_every_layer_request_and_its_preflight(self):
        self.serve("0.0.0.0")
        layer = self.dir / "comment-layer.js"
        layer.write_text("/* layer */\n", encoding="utf-8")
        origin = {"Origin": self.SITE}
        save = self.scoped(LANE, TURN, name="1-comment-c1.json")
        with patch.object(serve, "COMMENT_LAYER", layer):
            answers = {
                "layer": self.request("GET", "/static/comment-layer.js", headers=origin),
                "read": self.request("GET", self.scoped(LANE, TURN), headers=origin),
                "unknown": self.request("GET", self.scoped("unregistered", TURN), headers=origin),
                "preflight": self.request("OPTIONS", save, headers={
                    **origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"}),
                "save": self.request("POST", save, comment("c1", subject=TURN, kind="agent turn"),
                                     {**origin, "Content-Type": "application/json"}),
            }
        self.assertEqual({name: answer[0] for name, answer in answers.items()},
                         {"layer": 200, "read": 200, "unknown": 404, "preflight": 204, "save": 200})
        for name, (_, headers, _) in answers.items():
            self.assertEqual(headers.get("access-control-allow-origin"), self.SITE, name)
            self.assertNotIn("access-control-allow-credentials", headers, name)
        self.assertIn("content-type", answers["preflight"][1]["access-control-allow-headers"].lower())

    def test_private_service_answers_the_layer_with_no_cross_origin_read(self):
        """comment-layer #acceptance-private-other-origin, #ui-absent (service side)."""
        self.serve("127.0.0.1")
        _, headers, _ = self.request("GET", self.scoped(LANE, TURN), headers={"Origin": self.SITE})
        self.assertNotIn("access-control-allow-origin", headers)
        status, _, _ = self.request("OPTIONS", self.scoped(LANE, TURN), headers={"Origin": self.SITE})
        self.assertEqual(status, 501)


class HostedWakeTest(HostedCase):
    """host-wake #wake-rule-poll: each scope's hosted threads wake the owner of the scope's row registered last."""

    def setUp(self):
        super().setUp()
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))
        self.log = sys.stderr
        self.waker = FakeWaker(self.dir, self.state)
        for owner in ("w1:pLane", "w1:pOps", "w1:pLater"):
            self.waker.agent(owner)
        self.controller = serve.WakeController(types.SimpleNamespace(mount_state=self.mount_state,
                                                                     state_dir=str(self.state)))

    def hand_off(self, scope, *names):
        human = self.state / "comments" / scope / "human"
        human.mkdir(parents=True, exist_ok=True)
        for name in names:
            (human / name).write_text("{}")
        return human.parent

    def test_hand_off_on_a_subject_naming_no_spec_wakes_the_lane_owner_naming_the_scope(self):
        """comment-layer #acceptance-host-wake; host-wake #wake-rule-message, #wake-rule-log."""
        hosted = self.hand_off(LANE, "100-comment-c1.json", "110-handoff-h1.json")
        for _ in range(3):
            self.controller.poll()
        says = self.waker.says()
        self.assertEqual(len(says), 1)
        owner, artifact, message = says[0]
        self.assertEqual((owner, artifact), ("w1:pLane", str(hosted)))
        for part in ("scope " + LANE, "hosted threads " + str(hosted), "cursor .cursor-owner", "2 events",
                     "zero-wait scan", "park"):
            self.assertIn(part, message)
        self.assertNotIn("spec " + str(self.spec), message)
        self.assertEqual(self.controller.status("comments:" + LANE), "sent")
        self.assertIn("review-serve: wake send scope=%s owner=w1:pLane exit=0" % LANE, self.log.getvalue())
        self.assertFalse((hosted / ".cursor-owner").exists(), "the host never advances the cursor")

    def test_hand_off_in_a_scope_rows_threads_wakes_its_operator(self):
        """comment-layer #acceptance-scope-row-wake."""
        self.hand_off(SCOPE, "100-comment-c1.json", "110-handoff-h1.json")
        self.controller.poll()
        self.assertEqual([say[0] for say in self.waker.says()], ["w1:pOps"])

    def test_the_scopes_row_registered_last_owns_its_hosted_threads(self):
        """comment-layer #scope-owner."""
        later = self.scope_row.replace(SCOPE, LANE).replace("w1:pOps", "w1:pLater")
        self.write_registry(self.spec_row, later, stamp=os.stat(self.registry).st_mtime_ns + 5_000_000_000)
        self.hand_off(LANE, "100-comment-c1.json", "110-handoff-h1.json")
        self.controller.poll()
        self.assertEqual([say[0] for say in self.waker.says()], ["w1:pLater"])

    def test_a_scope_with_no_hand_off_wakes_no_one(self):
        self.hand_off(SCOPE, "100-comment-c1.json")
        self.controller.poll()
        self.assertEqual(self.waker.says(), [])
        self.assertIsNone(self.controller.status("comments:" + SCOPE))


if __name__ == "__main__":
    unittest.main()
