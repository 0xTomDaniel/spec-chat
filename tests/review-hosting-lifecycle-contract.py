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
    def test_launcher_uses_host_approved_ports_without_fixed_assumption(self):
        self.assertTrue(os.access(LAUNCHER_PATH, os.X_OK))
        self.assertIn("usage: launch-review-serve.sh NARROW_ROOT SPEC_PATH EXACT_BASE", LAUNCHER)
        self.assertIn("SPEC_CHAT_APPROVED_INGRESS_PORTS", LAUNCHER)
        self.assertIn("ufw status", LAUNCHER)
        self.assertIn("no approved ingress port is free", LAUNCHER)
        self.assertIn('bind(("0.0.0.0", int(sys.argv[1])))', LAUNCHER)
        for forbidden in ("8877", "36069", "45581"):
            self.assertNotIn(forbidden, LAUNCHER)
            self.assertNotIn(forbidden, REVIEW)
            self.assertNotIn(forbidden, SHAPE)

    def test_launcher_runs_internal_exact_content_checks_before_handoff(self):
        self.assertIn("verify-review.py", LAUNCHER)
        self.assertIn("BASELINE_URL", LAUNCHER)
        self.assertIn("served spec bytes differ", LAUNCHER)
        self.assertIn("baseline route returned a different base", LAUNCHER)
        self.assertIn("review-handoff=allowed", LAUNCHER)
        checks = LAUNCHER.index("served spec bytes differ")
        handoff = LAUNCHER.index("review-handoff=allowed")
        wait = LAUNCHER.rindex('wait "$SERVER_PID"')
        self.assertLess(checks, handoff)
        self.assertLess(handoff, wait)
        self.assertNotIn("PROBE", LAUNCHER)
        self.assertNotIn("SPEC_CHAT_EXTERNAL_VANTAGE", LAUNCHER)

    def test_contract_is_box_side_and_requires_no_operator_transport_ceremony(self):
        for contract in (REVIEW, SHAPE):
            self.assertIn("host", contract.lower())
            self.assertIn("reviewer-machine", contract)
            self.assertNotIn("operator-confirmed public URL receipt", contract)
            self.assertNotIn("URL fingerprint", contract)
            self.assertNotIn("attestor", contract)
            self.assertNotIn("SPEC_CHAT_EXTERNAL_VANTAGE", contract)
        self.assertIn("no operator", REVIEW.lower())
        self.assertIn("No second probe", SHAPE)
        self.assertIn("no usable port exists", REVIEW)

    def test_contract_preserves_secret_url_ownership_and_finish_lifecycle(self):
        for contract in (REVIEW, SHAPE):
            self.assertIn("secret URL", contract)
            self.assertIn("collision safety", contract.lower())
            self.assertTrue("exact resource" in contract or "exact served HTML" in contract)
            self.assertIn("/api/baseline", contract)
            self.assertIn("Finish review", contract)
        self.assertIn("service process", REVIEW)
        self.assertIn("direct service ownership", SHAPE)
        self.assertIn("same public server, secret URL, and checker", REVIEW)
        self.assertIn("processed empty **Finish review** hand-off", ADAPTERS)

    def test_launcher_syntax(self):
        subprocess.run(["sh", "-n", str(LAUNCHER_PATH)], check=True)


if __name__ == "__main__":
    unittest.main()
