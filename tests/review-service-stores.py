"""Every file or folder Spec Chat writes on the box has one store row (review-service #stores, #acceptance-stores).

Each writer runs for real into a temporary state root and spool; every path it leaves must match exactly one row of
the store table, which names one lifetime, Log or Cache, and the writer that ends it."""
import html
import importlib.util
import os
import re
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs/specs/review-service.spec.html"
SKILL = ROOT / "skill/review-spec/SKILL.md"
HOST = ROOT / "skill/review-spec/scripts/review-host.py"
EMIT_REPLY = ROOT / "skill/review-spec/scripts/emit-reply.sh"
INSTALL = ROOT / "scripts/install-spec-chat"
SPOOL = "<spec>.review/"

_spec = importlib.util.spec_from_file_location("review_service_stores_host", HOST)
review_host = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(review_host)
serve = review_host._serve_module

# Each store row: code its Store cell names, and the written paths it owns, relative to $XDG_STATE_HOME/spec-chat or
# the spool. registry.toml is split by its top-level table, since its rows and [process] are two stores.
ROWS = {
    "store-registry-rows": (("registry.toml", "[[resource]]"), r"hosting/[^/]+/registry\.toml\[resource\]"),
    "store-registry-process": (("registry.toml", "[process]"), r"hosting/[^/]+/registry\.toml\[process\]"),
    "store-server-log": (("server.log",), r"hosting/[^/]+/server\.log"),
    "store-spool-events": (("human/", "agent/"), re.escape(SPOOL) + r"(human|agent)/[^/]+\.json"),
    "store-spool-versions": (("versions/",), re.escape(SPOOL) + r"versions/[0-9a-f]{64}\.html"),
    "store-spool-cursor": ((".cursor-<name>", "context.md"), re.escape(SPOOL) + r"(\.cursor-[^/]+|context\.md)"),
    # A test row's records and ledger are the same stores in the test namespace (jev-suggestions #test-host-rule).
    "store-jev-records": (("jev/records/", "jev/test/records/"), r"jev/(test/)?records/.+"),
    "store-jev-ledger": (("jev/ledger.jsonl",), r"jev/(test/)?ledger\.jsonl"),
    "store-onboarding": (("$XDG_STATE_HOME/spec-chat/onboarding.toml",), r"onboarding\.toml"),
}
# Written, yet no store: the key says the lock beside the registry holds nothing.
NOT_STORES = {r"hosting/[^/]+/\.state\.lock": "the lock beside the registry holds nothing"}


def text(fragment):
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def store_table():
    """Each row of the store table by anchor: Store cell codes, lifetime, and ended-by."""
    page = SPEC.read_text(encoding="utf-8")
    table = re.search(r'<table data-anchor="stores-table">(.*?)</table>', page, re.S).group(1)
    rows = {}
    for anchor, body in re.findall(r'<tr data-anchor="([^"]+)">(.*?)</tr>', table, re.S):
        cells = re.findall(r"<td>(.*?)</td>", body, re.S)
        rows[anchor] = {"codes": {text(code) for code in re.findall(r"<code>(.*?)</code>", cells[0], re.S)},
                        "lifetime": text(cells[1]), "ended_by": text(cells[2])}
    key = text(re.search(r'<p data-anchor="stores-key">(.*?)</p>', page, re.S).group(1))
    return rows, key


class StoreTableTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows, cls.key = store_table()
        cls.temp = tempfile.TemporaryDirectory(prefix="spec-chat-stores-")
        cls.written = write_every_store(Path(cls.temp.name))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_each_row_names_one_lifetime_and_the_writer_that_ends_it(self):
        self.assertEqual(set(self.rows), set(ROWS), "each store row has its written paths here, and only those")
        for anchor, row in self.rows.items():
            with self.subTest(row=anchor):
                self.assertIn(row["lifetime"], ("Log", "Cache"))
                self.assertTrue(row["ended_by"])
                self.assertLessEqual(set(ROWS[anchor][0]), row["codes"])

    def test_every_written_path_has_exactly_one_row(self):
        for path in self.written:
            with self.subTest(path=path):
                owners = [anchor for anchor, (_, pattern) in ROWS.items() if re.fullmatch(pattern, path)]
                exempt = [reason for pattern, reason in NOT_STORES.items() if re.fullmatch(pattern, path)]
                if exempt:
                    self.assertEqual(owners, [])
                    self.assertIn(exempt[0], self.key)
                else:
                    self.assertEqual(len(owners), 1, f"{path} needs exactly one store row, has {owners}")

    def test_every_row_is_written(self):
        for anchor, (_, pattern) in ROWS.items():
            with self.subTest(row=anchor):
                self.assertTrue(any(re.fullmatch(pattern, path) for path in self.written),
                                f"no writer wrote {anchor}: {sorted(self.written)}")


def write_every_store(work: Path) -> set[str]:
    """Run each Spec Chat writer once into `work`, then list what it left: paths under the state root, the spool's
    as <spec>.review/..., registry.toml by top-level table, and every empty folder."""
    home, xdg, repo = work / "home", work / "state", work / "repo"
    spec = repo / "docs/specs/a.spec.html"
    spec.parent.mkdir(parents=True)
    home.mkdir()
    spec.write_text("<!doctype html><p data-anchor='x'>x</p>\n", encoding="utf-8")
    env = {"HOME": str(home), "XDG_STATE_HOME": str(xdg), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "PYTHONDONTWRITEBYTECODE": "1"}
    review = str(spec) + ".review"
    with mock.patch.dict(os.environ, env):
        os.environ.pop("HERDR_SESSION", None)
        # review-host: registry rows and [process], the lock, and the log the service appends to.
        registry = review_host.paths(serve.session_state())[0]
        with review_host.state_lock(registry.parent):
            review_host.write_registry(registry, [{"id": "r1", "slug": "a", "spec": "docs/specs/a.spec.html"}],
                                       {"pid": 1, "port": 1, "bind": "127.0.0.1", "host": "127.0.0.1"})
        load = ("import importlib.util, sys; s = importlib.util.spec_from_file_location('h', sys.argv[1]); "
                "h = importlib.util.module_from_spec(s); s.loader.exec_module(h); "
                "h.append_output(h.paths(h._serve_module.session_state())[1]); "
                "print('spec-chat review-serve on http://127.0.0.1:1/')")
        subprocess.run(["python3", "-c", load, str(HOST)], env=env, check=True)
        # review-serve: a human event and a served version; emit-reply.sh: an agent event.
        serve._write_event(review, str(repo), "human", "1-comment-c1.json", {"type": "comment", "id": "c1"})
        serve._store_version(review, str(repo), spec.read_bytes())
        subprocess.run(["sh", str(EMIT_REPLY), review, "c1", "x", "null", "acknowledged", "no spec change", "ok"],
                       env=env, check=True, capture_output=True)
        # The review skill: each spool file it tells the owning agent to write.
        for name in sorted(set(re.findall(r"<spec>\.review/([^\s`/]+)", SKILL.read_text(encoding="utf-8")))):
            with open(os.path.join(review, name), "a", encoding="utf-8") as stream:
                stream.write("1-comment-c1.json\n")
        # Jev: a record and a ledger outcome in the live store and in the test namespace.
        service = serve.JevService()
        qset = next(iter(service.question_sets.values()))
        for seam in (service.seam, service.test_seam):
            seam.store.append({"cache_key": "k", "record_id": "judgment-1", "outcome": "final", "kind": qset.id,
                               "question_set": {"id": qset.id, "version": qset.version}})
            seam.ledger.append({"project": "p", "spec": "a.spec.html", "anchor": "x", "kind": qset.id,
                                "outcome": "dismissed", "judgment": "judgment-1", "by": "owner"})
    # The installer: its onboarding file.
    subprocess.run([str(INSTALL)], env=env, cwd=repo, check=True, capture_output=True, timeout=60)
    return listed(xdg / "spec-chat", "") | listed(Path(review), SPOOL)


def listed(root: Path, prefix: str) -> set[str]:
    written = set()
    for path in root.rglob("*"):
        name = prefix + path.relative_to(root).as_posix()
        if path.name == "registry.toml":
            written |= {f"{name}[{table}]" for table in tomllib.loads(path.read_text(encoding="utf-8"))}
        elif path.is_file() or path.is_symlink():
            written.add(name)
        elif not any(path.iterdir()):
            written.add(name + "/")
    return written


if __name__ == "__main__":
    unittest.main()
