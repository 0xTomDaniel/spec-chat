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
        self.assertIn("non-loopback external probe", REVIEW)
        self.assertIn("HTTP 200", SHAPE)
        self.assertIn("loopback-only proof", SHAPE)

    def test_contract_requires_url_delivery_before_parking(self):
        deliver = REVIEW.index("Deliver, in this order")
        park = REVIEW.index("watcher or checker park")
        self.assertLess(deliver, park)
        self.assertIn("URL delivery", SHAPE)
        self.assertIn("Refuse URL delivery and checker parking", SHAPE)

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
