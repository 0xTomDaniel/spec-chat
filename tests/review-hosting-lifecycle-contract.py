import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW = (ROOT / "skill/review-spec/SKILL.md").read_text()
SHAPE = (ROOT / "skill/shape-spec/SKILL.md").read_text()
ADAPTERS = (ROOT / "skill/review-spec/references/cli-adapters.md").read_text()
LAUNCHER_PATH = ROOT / "skill/review-spec/scripts/launch-review-serve.sh"
LAUNCHER = LAUNCHER_PATH.read_text()


class ReviewHostingLifecycleContractTest(unittest.TestCase):
    def assert_contract_phrase(self, contract, phrase):
        normalized_contract = " ".join(contract.split())
        normalized_phrase = " ".join(phrase.split())
        self.assertIn(normalized_phrase, normalized_contract)

    def test_launcher_discovers_approved_ports_without_host_assumptions(self):
        self.assertTrue(os.access(LAUNCHER_PATH, os.X_OK))
        self.assertIn("SPEC_CHAT_APPROVED_INGRESS_PORTS", LAUNCHER)
        self.assertIn("ufw status", LAUNCHER)
        self.assertIn("no approved ingress port is free", LAUNCHER)
        for forbidden in ("8877", "36069", "45581"):
            self.assertNotIn(forbidden, LAUNCHER)
            self.assertNotIn(forbidden, REVIEW)
            self.assertNotIn(forbidden, SHAPE)

    def test_launcher_probes_external_exact_spec_and_baseline_before_handoff(self):
        self.assertIn("SPEC_CHAT_EXTERNAL_VANTAGE", LAUNCHER)
        self.assertIn("verify-review.py", LAUNCHER)
        self.assertIn("BASELINE_URL", LAUNCHER)
        self.assertIn("external-spec-http=200", LAUNCHER)
        self.assertIn("external-baseline-http=200", LAUNCHER)
        self.assertIn("external-spec-sha256=", LAUNCHER)
        self.assertIn("external-baseline-base=", LAUNCHER)
        probe = LAUNCHER.index('"$PROBE"')
        handoff = LAUNCHER.index("review-handoff=allowed")
        wait = LAUNCHER.rindex('wait "$SERVER_PID"')
        self.assertLess(probe, handoff)
        self.assertLess(handoff, wait)
        self.assertIn("non-loopback vantage", REVIEW)
        self.assertIn("operator-confirmed public URL receipt", REVIEW)
        self.assertIn("HTTP 200", SHAPE)
        self.assertIn("same-machine proof", SHAPE)
        self.assertIn("exact resource and baseline proof", SHAPE)

    def test_contract_requires_url_delivery_before_parking(self):
        deliver = REVIEW.index("Deliver, in this order")
        park = REVIEW.index("watcher or checker park")
        self.assertLess(deliver, park)
        self.assertIn("URL delivery", SHAPE)
        self.assertIn("Refuse URL delivery and checker parking", SHAPE)
        self.assertIn("neither an executable non-loopback probe nor a complete operator-confirmed public URL receipt", SHAPE)

    def test_operator_receipt_is_append_only_local_and_secret_safe(self):
        for contract in (REVIEW, SHAPE):
            self.assert_contract_phrase(contract, "append-only local operator receipt")
            self.assert_contract_phrase(contract, "URL fingerprint")
        self.assert_contract_phrase(REVIEW, "never write the raw secret URL to the receipt")
        self.assert_contract_phrase(SHAPE, "never publish the raw URL into the issue, change request, receipt, or another public durable record")

    def test_same_machine_receipt_has_complete_local_scope(self):
        required = (
            "explicit same-machine scope",
            "host identity or equivalent local scope",
            "approved port",
            "process identity",
            "exact content result",
            "local URL or transport path",
            "no public reachability claim",
        )
        for field in required:
            self.assert_contract_phrase(REVIEW, field)
            self.assert_contract_phrase(SHAPE, field)

    def test_receipt_path_is_manual_separate_and_session_bound(self):
        for contract in (REVIEW, SHAPE):
            self.assert_contract_phrase(contract, "external-probe path remains")
            self.assert_contract_phrase(contract, "receipt path")
            self.assert_contract_phrase(contract, "manual caller-owned handoff seam")
            self.assert_contract_phrase(contract, "probe is unavailable")
            self.assert_contract_phrase(contract, "stale")
            self.assert_contract_phrase(contract, "new")
        self.assert_contract_phrase(REVIEW, "does not add an application-wide receipt store or receipt implementation")
        self.assert_contract_phrase(SHAPE, "does not add a broad application or receipt-store implementation")

    def test_empty_spool_and_manual_resume_keep_hosting_alive(self):
        self.assertIn("empty spool", REVIEW)
        self.assertIn("session is parked", REVIEW)
        self.assertIn("same public server, secret URL, and checker", REVIEW)
        self.assertIn("captured secret URL", ADAPTERS)
        self.assertIn("same URL", ADAPTERS)
        self.assertIn("processed empty **Finish review** hand-off", ADAPTERS)

    def test_shutdown_requires_explicit_finish_terminal(self):
        self.assertIn("processed empty Finish review hand-off", REVIEW)
        self.assertIn("exact cursor advance succeeded", REVIEW)
        self.assertIn("Only after that processed terminal hand-off may", REVIEW)
        self.assertNotIn("stop the server when review ends", REVIEW)

    def test_shell_launcher_is_syntax_valid(self):
        subprocess.run(["sh", "-n", str(LAUNCHER_PATH)], check=True)


if __name__ == "__main__":
    unittest.main()
