from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROVISION = ROOT / "provision-hermes-dashboard.sh"
ROLLBACK = ROOT / "rollback-hermes-dashboard.sh"
REPAIR_CADDY = ROOT / "repair-hermes-dashboard-caddy.sh"


class HermesDashboardProvisioningTests(unittest.TestCase):
    def test_provisioner_enforces_authenticated_loopback_dashboard_and_scoped_https(self):
        text = PROVISION.read_text()

        self.assertIn("https://chiron.local", text)
        self.assertNotIn("hermes.local", text.replace("chiron.local", ""))
        self.assertIn("--host 127.0.0.1", text)
        self.assertIn("--port 9119", text)
        self.assertIn("dashboard.public_url", text)
        self.assertIn("dashboard.basic_auth.username", text)
        self.assertIn("HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH", text)
        self.assertIn("HERMES_DASHBOARD_BASIC_AUTH_SECRET", text)
        self.assertNotIn("printf 'HERMES_DASHBOARD_BASIC_AUTH_PASSWORD=%s", text)
        self.assertIn("hashlib.scrypt", text)
        self.assertIn("auto_https disable_redirects", text)
        self.assertIn("admin off", text)
        self.assertIn("bind 0.0.0.0", text)
        self.assertIn("tls internal", text)
        self.assertIn("192.168.1.0/25", text)
        self.assertIn("10.1.50.0/24", text)
        self.assertIn("127.0.0.0/8", text)
        self.assertIn("--dport 443", text)
        self.assertNotIn("--dport 9119", text)
        self.assertIn("caddy validate", text)
        self.assertIn("auth_required", text)
        self.assertIn('"basic"', text)
        self.assertIn("COMPLETED hermes-dashboard provisioning", text)

    def test_provisioner_creates_persistent_services_and_private_backups(self):
        text = PROVISION.read_text()

        self.assertIn("hermes-dashboard.service", text)
        self.assertIn("hermes-dashboard-firewall.service", text)
        self.assertIn("/var/backups/hermes-dashboard", text)
        self.assertIn("chmod 600", text)
        self.assertIn("systemctl enable", text)
        self.assertIn("hermes-dashboard-caddy-root.crt", text)

    def test_rollback_removes_managed_state_and_restores_backup(self):
        text = ROLLBACK.read_text()

        self.assertIn("STATE_DIR=/var/lib/hermes-dashboard", text)
        self.assertIn("POINTER=$STATE_DIR/last-backup", text)
        self.assertIn("hermes-dashboard-firewall remove", text)
        self.assertIn("hermes-dashboard.service", text)
        self.assertIn("hermes-dashboard-firewall.service", text)
        self.assertIn("/etc/caddy/Caddyfile", text)
        self.assertIn("COMPLETED hermes-dashboard rollback", text)

    def test_caddy_repair_disables_conflicting_admin_listener_and_verifies_https(self):
        text = REPAIR_CADDY.read_text()

        self.assertIn("listen tcp 127.0.0.1:2019: bind: address already in use", text)
        self.assertIn("admin off", text)
        self.assertIn("auto_https disable_redirects", text)
        self.assertIn("caddy validate", text)
        self.assertIn("systemctl restart caddy.service", text)
        self.assertIn("auth_required", text)
        self.assertIn('"basic"', text)
        self.assertIn("hermes-dashboard-caddy-root.crt", text)
        self.assertIn("COMPLETED hermes-dashboard Caddy repair", text)


if __name__ == "__main__":
    unittest.main()
