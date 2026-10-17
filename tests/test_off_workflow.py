"""One-command restoration through the shared connected-client owner."""
import json
import subprocess
import sys
import tomllib
import unittest

from tests import test_connected_client_lifetime_workflow as lifetime


class OffWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = lifetime.ConnectedClientLifetimeWorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_off_restores_chatgpt_preferences_and_stops_only_the_owned_runtime_without_router_config(self):
        with lifetime.provider('UNUSED_CLOUD') as (cloud, requests):
            configuration, _, engine = self.fixture.managed_configuration(cloud)
            options = self.fixture.local_setup_options(configuration)
            original = ('# Preserve my normal Codex preferences.\nmodel = "original-model"\n'
                        'model_provider = "openai"\nmodel_reasoning_effort = "max"\n'
                        'approvals_reviewer = "auto_review"\n[features]\nplugins = true\n'
                        '[plugins.fixture]\nenabled = true\n[mcp_servers.fixture]\n'
                        'command = "fixture-mcp"\n')
            settings = self.fixture.home / 'config.toml'
            settings.write_text(original)
            authentication = self.fixture.home / 'auth.json'
            authentication.write_text('PRIVATE_SYNTHETIC_CHATGPT_AUTHENTICATION')
            self.addCleanup(lambda: self.fixture.command('disconnect', check=False))
            self.fixture.command('setup', *options)
            self.fixture.path.rename(self.fixture.root / 'unavailable-gateway.json')
            result = subprocess.run([sys.executable, '-m', 'hormuz', 'off'],
                env=self.fixture.environment, capture_output=True, text=True, timeout=18)
            self.assertEqual(result.returncode, 0, result.stderr)
            receipt = json.loads(result.stdout)
            self.assertEqual((receipt['phase'], receipt['router'], receipt['configuration']),
                             ('disconnected', 'stopped', 'not_owned'))
            self.assertEqual(receipt['local_runtime'], {'ownership': 'owned', 'state': 'stopped'})
            self.assertEqual(tomllib.loads(settings.read_text()), tomllib.loads(original))
            self.assertIn('# Preserve my normal Codex preferences.', settings.read_text())
            self.assertEqual(authentication.read_text(), 'PRIVATE_SYNTHETIC_CHATGPT_AUTHENTICATION')
            self.assertTrue((engine / 'stopped').exists())
            self.assertEqual(requests, [])
