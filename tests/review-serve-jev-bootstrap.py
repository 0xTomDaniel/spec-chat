"""Bootstrap warm-up, onboarding status, and offer data behind fakes (project-rules #bootstrap)."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import unittest
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "skill" / "review-spec" / "assets"
_spec = importlib.util.spec_from_file_location("review_serve_jev_bootstrap_test", ROOT / "tools" / "jev.py")
jev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jev)
sys.path.insert(0, str(ASSETS))
_serve_spec = importlib.util.spec_from_file_location("review_serve_bootstrap_test", ASSETS / "review-serve.py")
serve = importlib.util.module_from_spec(_serve_spec)
_serve_spec.loader.exec_module(serve)

ONBOARDING = "Every change to what a user sees or does adds its onboarding section."
LOCAL = "The report page shows totals per week."
FEATURE = "The export button downloads a CSV of the current table."
OTHER = "The import dialog accepts a CSV file."


def git(root, *args):
    return subprocess.check_output(("git", "-C", str(root), *args), text=True).strip()


def spec(*criteria, body=""):
    items = "".join(f'<p data-acceptance-criterion data-anchor="{anchor}">{text}</p>' for anchor, text in criteria)
    return ('<header data-anchor="header"><h1 data-anchor="title">Spec</h1></header>'
            f'<section data-spec-section="acceptance" data-anchor="acceptance"><h2>Acceptance criteria</h2>{items}</section>'
            f'<section data-anchor="behavior"><p data-anchor="body">{body}</p></section>')


class FakeProvider:
    """Scope from a map; rule checks missed for specs whose text holds a marker; every call kept."""

    def __init__(self, scope=None, missed=("export", "import"), gate=None):
        self.scope = dict(scope or {ONBOARDING: "every feature"})
        self.missed = missed
        self.gate = gate
        self.calls = []
        self.active = self.peak = 0
        self.lock = threading.Lock()

    def decide(self, payload):
        with self.lock:
            self.calls.append(payload)
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            if self.gate is not None:
                self.gate.wait(5)
            time.sleep(0.02)
            kind = next(iter(payload["questions"]))
            state = payload["state"]
            if kind == "scope":
                label = self.scope.get(state["criterion"], "this feature")
            elif kind == "rule":
                label = "missed" if any(word in state["spec"] for word in self.missed) else "covered"
            else:
                label = "unrelated"
            return {"answers": {kind: {"choice": label, "confidence": 0.95}}}
        finally:
            with self.lock:
                self.active -= 1


class BootstrapTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.root = self.dir / "repo"
        self.specs = self.root / "docs" / "specs"
        self.specs.mkdir(parents=True)
        git(self.root, "init", "-q", "-b", "main")
        self.write("onboarding.spec.html", spec(("acceptance-onboarding", ONBOARDING), ("local", LOCAL)))
        self.write("export.spec.html", spec(("export-one", FEATURE), body="The report page gains an export button."))
        self.write("import.spec.html", spec(("import-one", OTHER), body="The import dialog is new."))
        self.write("plain.spec.html", spec(("plain-one", "The footer year is current."), body="Footer."))
        fixtures = self.root / "docs" / "fixtures"
        fixtures.mkdir()
        (fixtures / "x.spec.html").write_text(spec(("x-one", "Fixture export."), body="export"), encoding="utf-8")
        self.main = self.commit()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        (self.specs / name).write_text(text, encoding="utf-8")

    def commit(self):
        git(self.root, "add", "-A")
        git(self.root, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qm", "c", "--allow-empty")
        return git(self.root, "rev-parse", "HEAD")

    def service(self, provider, api_key="fake"):
        return jev.JevService(state_dir=self.dir / "state", provider=provider, api_key=api_key)

    def row(self, spec_name="export.spec.html", project="proj"):
        return {"id": "spec:%s:%s::docs/specs/%s" % (project, project, spec_name), "slug": project,
                "project": project, "root": str(self.root), "narrow_root": str(self.root / "docs"),
                "spec": "docs/specs/" + spec_name, "base": self.main, "owner": "w1:p1", "checker": "w1:p1",
                "cursor_name": "cursor", "spec_file": str(self.specs / spec_name),
                "path": project + "/docs/specs/" + spec_name}

    def settle(self, service, project="proj"):
        deadline = time.time() + 10
        while time.time() < deadline:
            status = service.onboarding_status(project)
            if status and status["state"] != "running":
                return status
            time.sleep(0.01)
        self.fail("warm-up did not finish")

    def page(self, service, rows, name="export.spec.html", base=None):
        mount = rows[0]
        return service.response(mount, str(self.specs / name), "docs/specs/" + name, base or self.main, [], "", rows)

    def test_first_registration_warms_in_parallel_without_waiting_and_publishes_status(self):
        gate = threading.Event()
        provider = FakeProvider(gate=gate)
        service = self.service(provider)
        started = time.monotonic()
        self.assertEqual(service.warm([self.row()]), ["proj"])
        self.assertLess(time.monotonic() - started, 0.5)  # register returns without waiting
        self.assertEqual(service.onboarding_status("proj")["state"], "running")
        gate.set()
        status = self.settle(service)
        self.assertGreater(provider.peak, 1)
        self.assertEqual(status["state"], "done")
        self.assertEqual(status["main"], self.main)
        self.assertEqual(status["criteria_classified"], 5)  # main's criteria, fixtures excluded
        self.assertEqual(status["rules"], ["docs/specs/onboarding.spec.html#acceptance-onboarding"])
        self.assertEqual(status["specs_to_reconcile"], 2)
        rule = {"target": "docs/specs/onboarding.spec.html#acceptance-onboarding", "word": "onboarding"}
        self.assertEqual(status["reconcile"], [{"spec": "docs/specs/export.spec.html", "rules": [rule]},
                                               {"spec": "docs/specs/import.spec.html", "rules": [rule]}])
        self.assertIsNone(status.get("offer"))
        # the home spec is never checked against its own rule; every other spec is
        checked = sorted(c["state"]["spec"].split("\n")[-1] for c in provider.calls if "rule" in c["questions"])
        self.assertEqual(len(checked), 3)
        # one table per project in Spec Chat's one onboarding.toml; no separate status file
        text = service.onboarding_path.read_text(encoding="utf-8")
        self.assertNotIn("w1:p1", text)  # names no peer
        self.assertEqual(tomllib.loads(text)["project"]["proj"], status)
        self.assertEqual(sorted(p.name for p in service.state_dir.iterdir()), ["onboarding.toml", "records.jsonl"])

    def test_warm_up_table_keeps_install_status_and_other_projects(self):
        path = self.dir / "state" / "onboarding.toml"
        path.parent.mkdir(parents=True)
        path.write_text('status = "done"\ndoc = "file:///README.md#install-and-onboarding"\n\n[project.other]\nstate = "done"\n')
        service = self.service(FakeProvider())
        service.warm([self.row()])
        self.settle(service)
        value = tomllib.loads(path.read_text(encoding="utf-8"))
        self.assertEqual((value["status"], value["doc"]), ("done", "file:///README.md#install-and-onboarding"))
        self.assertEqual(sorted(value["project"]), ["other", "proj"])
        self.assertTrue(service.record_offer("proj", "sent"))
        self.assertEqual(tomllib.loads(path.read_text(encoding="utf-8"))["project"]["proj"]["offer"], "sent")

    def test_later_pages_reuse_warm_records_and_show_the_offer_once(self):
        provider = FakeProvider()
        service = self.service(provider)
        rows = [self.row(), self.row("onboarding.spec.html")]
        service.warm(rows)
        self.settle(service)
        calls = len(provider.calls)
        # the page spec differs from its compared base but equals main: its checks are the warm-up's records
        self.write("export.spec.html", spec(("export-one", "Old."), body="Old."))
        old = self.commit()
        self.write("export.spec.html", spec(("export-one", FEATURE), body="The report page gains an export button."))
        deadline = time.time() + 5
        while True:
            result = self.page(service, rows, base=old)
            rules = [i for i in result["items"] if i["kind"] == "rule"]
            if not any(i["state"] == "pending" for i in rules) or time.time() > deadline:
                break
            time.sleep(0.01)
        self.assertEqual([(i["state"], i["word"]) for i in rules], [("label", "onboarding")])
        self.assertEqual([c for c in provider.calls[calls:] if set(c["questions"]) & {"scope", "rule"}], [])
        offer = result["offer"]
        self.assertEqual(offer["count"], 2)
        self.assertEqual([entry["spec"] for entry in offer["specs"]],
                         ["docs/specs/export.spec.html", "docs/specs/import.spec.html"])
        self.assertTrue(service.record_offer("proj", "dismissed"))
        self.assertIsNone(self.page(service, rows)["offer"])
        self.assertEqual(service.onboarding_status("proj")["offer"], "dismissed")
        # a new server with the same project never warms or offers again
        again = self.service(FakeProvider())
        self.assertEqual(again.warm(rows), [])
        self.assertIsNone(self.page(again, rows)["offer"])
        self.assertTrue(again.record_offer("proj", "sent"))
        self.assertEqual(again.onboarding_status("proj")["offer"], "dismissed")

    def test_no_misses_or_jev_off_shows_no_offer(self):
        service = self.service(FakeProvider(missed=()))
        service.warm([self.row()])
        status = self.settle(service)
        self.assertEqual((status["state"], status["specs_to_reconcile"]), ("done", 0))
        self.assertIsNone(self.page(service, [self.row()])["offer"])
        off = jev.JevService(state_dir=self.dir / "off", api_key="")
        self.assertEqual(off.warm([self.row()]), [])
        self.assertIsNone(off.onboarding_status("proj"))
        self.assertEqual(self.page(off, [self.row()]), {"jev": "off", "items": [], "levels": jev.MARK_LEVELS})
        # nothing was warmed: a later server with Jev on warms the project
        on = jev.JevService(state_dir=self.dir / "off", provider=FakeProvider(), api_key="fake")
        self.assertEqual(on.warm([self.row()]), ["proj"])
        self.assertEqual(self.settle(on)["state"], "done")

    def test_outage_leaves_project_not_done_and_next_start_warms(self):
        class Down:
            def decide(self, payload):
                raise RuntimeError("down")

        service = self.service(Down())
        self.assertEqual(service.warm([self.row()]), ["proj"])
        self.assertNotEqual(self.settle(service)["state"], "done")
        self.assertIsNone(self.page(service, [self.row()])["offer"])
        again = self.service(FakeProvider())
        self.assertEqual(again.warm([self.row()]), ["proj"])
        status = self.settle(again)
        self.assertEqual((status["state"], status["specs_to_reconcile"]), ("done", 2))

    def test_rate_limited_warm_up_asks_pause_and_never_sleep(self):
        """jev-suggestions #state-error: a warm-up ask that 429s is one call, gets the given wait as its pause,
        and is asked again later; no worker waits (old code slept 30 s per 429)."""
        import unittest.mock
        provider = jev.OpenRouterProvider("fake", endpoint="http://127.0.0.1:9/decisions")
        limited = unittest.mock.Mock(side_effect=lambda request, timeout: (_ for _ in ()).throw(
            jev.HTTPError(request.full_url, 429, "limited", {"Retry-After": "45"}, None)))
        with unittest.mock.patch.object(jev, "urlopen", limited), \
                unittest.mock.patch.object(jev, "_wall", lambda: 1000.0):
            service = self.service(provider)
            started = time.monotonic()
            self.assertEqual(service.warm([self.row()]), ["proj"])
            deadline = time.monotonic() + 5
            while (service.onboarding_status("proj") or {}).get("state") == "running" and time.monotonic() < deadline:
                threading.Event().wait(0.01)
            self.assertLess(time.monotonic() - started, 2.0)
            self.assertEqual(service.onboarding_status("proj")["state"], "failed")
            records = [json.loads(line) for line in (self.dir / "state" / "records.jsonl").read_text().splitlines()]
            self.assertEqual(limited.call_count, len(records))
            self.assertTrue(records and all(r["outcome"] == "unavailable" and jev.pause_left(r) == 45.0 for r in records))
        again = self.service(FakeProvider())
        self.assertEqual(again.warm([self.row()]), ["proj"])
        self.assertEqual(self.settle(again)["state"], "done")

    def test_unreadable_onboarding_is_never_overwritten(self):
        path = self.dir / "state" / "onboarding.toml"
        path.parent.mkdir(parents=True)
        broken = 'status = "done"\n[project.other\nstate = "done"\n'
        path.write_text(broken)
        service = self.service(FakeProvider())
        self.assertEqual(service.warm([self.row()]), [])
        self.assertFalse(service.record_offer("proj", "sent"))
        self.assertEqual(path.read_text(), broken)
        path.write_text('status = "done"\n')  # repaired: the next change warms and keeps install's status
        self.assertEqual(service.warm([self.row()]), ["proj"])
        self.assertEqual(self.settle(service)["state"], "done")
        self.assertEqual(tomllib.loads(path.read_text())["status"], "done")

    def test_failed_warm_start_never_fails_server_start(self):
        state = serve.MountState((), None)

        def fail(rows):
            raise OSError("state dir not writable")

        state.on_change = fail
        state.changed()  # main runs the startup warm-up through this same guarded hook

    def test_registration_in_the_running_server_starts_warm_up_and_offer_is_recorded(self):
        registry = self.dir / "registry.toml"

        def write_rows(rows):
            keys = ("id", "slug", "project", "root", "narrow_root", "spec", "base", "owner", "checker", "cursor_name")
            registry.write_text("".join("[[resource]]\n" + "".join("%s = %s\n" % (k, json.dumps(r[k])) for k in keys)
                                        for r in rows), encoding="utf-8")

        write_rows([])
        service = self.service(FakeProvider())
        state = serve.MountState(serve._read_registry(str(registry), trust=True), str(registry))
        state.on_change = service.warm
        server = serve.ReviewThreadingHTTPServer(("127.0.0.1", 0), serve.MountHandler)
        server.mount_state = state
        server.jev = service
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        url = "http://127.0.0.1:%d" % server.server_port
        try:
            time.sleep(0.02)  # a distinct registry mtime
            write_rows([self.row()])
            with opener.open(url + "/proj/docs/specs/export.spec.html", timeout=2) as response:
                self.assertEqual(response.status, 200)  # the register proof request
            self.assertEqual(self.settle(service)["specs_to_reconcile"], 2)
            query = "?path=/proj/docs/specs/export.spec.html&base=" + self.main
            with opener.open(url + "/api/jev" + query, timeout=2) as response:
                self.assertEqual(json.loads(response.read())["offer"]["count"], 2)
            request = urllib.request.Request(url + "/api/jev/offer" + query, data=b'{"offer": "sent"}', method="POST")
            with opener.open(request, timeout=2) as response:
                self.assertEqual(json.loads(response.read()), {"ok": True})
            with opener.open(url + "/api/jev" + query, timeout=2) as response:
                self.assertIsNone(json.loads(response.read())["offer"])
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(service.onboarding_status("proj")["offer"], "sent")


if __name__ == "__main__":
    unittest.main()
