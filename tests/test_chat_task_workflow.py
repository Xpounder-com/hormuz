"""Explicit Hormuz tasks share the router without replacing the host model."""
import json
from pathlib import Path
import subprocess
import sys
import threading
import unittest

from tests import test_connected_client_lifetime_workflow as lifetime


class ChatTaskWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = lifetime.ConnectedClientLifetimeWorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.normal = self.fixture.root / 'normal-codex'
        self.normal.mkdir(mode=0o700)
        self.original = 'model = "gpt-fixture"\nmodel_reasoning_effort = "max"\n[plugins.fixture]\nenabled = true\n'
        (self.normal / 'config.toml').write_text(self.original)
        (self.normal / 'auth.json').write_text('UNCHANGED_SYNTHETIC_CHATGPT_AUTH')
        self.prompt = self.fixture.root / 'task.txt'
        self.prompt.write_text('Summarize this synthetic fixture.')

    def command(self, *arguments):
        return subprocess.run([sys.executable, '-m', 'hormuz', 'chat', *arguments,
            '--router-home', str(self.fixture.home)],
            env={**self.fixture.environment, 'CODEX_HOME': str(self.normal)},
            capture_output=True, text=True, timeout=10)

    def qualify_configuration(self):
        config = json.loads(self.fixture.path.read_text())
        config['ai_work'] = {'enabled': True, 'database': str(self.fixture.root / 'work.sqlite3')}
        config['model_routes']['local-code'].update(model_revision='fixture-weights-v1',
            capabilities=['responses', 'text', 'codex_http', 'codex_http_0160', 'codex_llama_v1'])
        config['route_admission'] = {'mode': 'local_only', 'local_routes': {'local-code': {
            'qualification_id': 'fixture-q1', 'model_revision': 'fixture-weights-v1',
            'max_request_bytes': 4096, 'max_output_tokens': 8, 'task_types': ['summarization']}}}
        self.fixture.path.write_text(json.dumps(config))

    def assert_normal_unchanged(self):
        self.assertEqual((self.normal / 'config.toml').read_text(), self.original)
        self.assertEqual((self.normal / 'auth.json').read_text(), 'UNCHANGED_SYNTHETIC_CHATGPT_AUTH')
        self.assertEqual(sorted(p.name for p in self.normal.iterdir()), ['auth.json', 'config.toml'])

    def test_explicit_local_task_returns_the_real_router_result_and_keeps_normal_codex_unchanged(self):
        with self.fixture.configured() as (local, cloud):
            self.qualify_configuration()
            self.fixture.command('connect')
            result = self.command('ask', '--model', 'local-code', '--prompt-file', str(self.prompt),
                                  '--max-output-tokens', '8')
            self.assertEqual(result.returncode, 0, result.stderr)
            receipt = json.loads(result.stdout)
            self.assertEqual((receipt['schema_id'], receipt['schema_version']), ('hormuz.chat-task', 1))
            self.assertEqual(receipt['text'], 'LOCAL_DESTINATION')
            self.assertEqual((receipt['model'], receipt['routed_model'], receipt['locality']),
                             ('local-code', 'fixture-local-model', 'local'))
            self.assertEqual(receipt['reasoning_effort'], 'max')
            self.assertEqual([len(local), len(cloud)], [1, 0])
            self.assertTrue(local[0]['body']['chat_template_kwargs']['enable_thinking'])
            self.assert_normal_unchanged()

    def test_chat_does_not_offer_or_submit_to_a_model_without_qualified_reasoning_support(self):
        with self.fixture.configured() as (local, cloud):
            self.qualify_configuration()
            config = json.loads(self.fixture.path.read_text())
            config['model_routes']['local-code']['capabilities'] = ['responses', 'text']
            self.fixture.path.write_text(json.dumps(config))
            self.fixture.command('connect')
            models = self.command('models')
            self.assertEqual(models.returncode, 0, models.stderr)
            self.assertEqual(json.loads(models.stdout)['models'], [])
            result = self.command('ask', '--model', 'local-code', '--prompt-file', str(self.prompt),
                                  '--max-output-tokens', '8')
            self.assertEqual(result.returncode, 2)
            receipt = json.loads(result.stderr)
            self.assertEqual(receipt['error']['code'], 'model_not_qualified')
            self.assertEqual(receipt['submission'], 'not_submitted')
            self.assertEqual([len(local), len(cloud)], [0, 0])
            self.assert_normal_unchanged()

    def test_configured_router_default_and_off_leave_the_normal_chat_usable(self):
        with self.fixture.configured() as (local, cloud):
            self.qualify_configuration()
            self.fixture.command('connect')
            models = self.command('models')
            self.assertEqual(models.returncode, 0, models.stderr)
            self.assertEqual(json.loads(models.stdout), {'schema_id': 'hormuz.chat-models', 'schema_version': 1,
                                                        'default_model': 'local-code', 'models': ['local-code']})
            result = self.command('ask', '--prompt-file', str(self.prompt), '--max-output-tokens', '8')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['selection'], 'router_default')
            stopped = subprocess.run([sys.executable, '-m', 'hormuz', 'off', '--client-home', str(self.fixture.home)],
                env={**self.fixture.environment, 'CODEX_HOME': str(self.normal)}, capture_output=True, text=True, timeout=10)
            self.assertEqual(stopped.returncode, 0, stopped.stderr)
            self.assertEqual(json.loads(stopped.stdout)['router'], 'stopped')
            after = self.command('ask', '--prompt-file', str(self.prompt), '--max-output-tokens', '8')
            self.assertEqual(after.returncode, 2)
            self.assertEqual(json.loads(after.stderr)['error']['code'], 'router_unavailable')
            self.assertEqual([len(local), len(cloud)], [1, 0])
            self.assert_normal_unchanged()

    def test_provider_failure_stays_inside_the_task_and_is_never_retried_or_sent_to_cloud(self):
        with lifetime.provider('PRIVATE_PROVIDER_FAILURE', status=503) as (failed_endpoint, failed):
            with self.fixture.configured() as (local, cloud):
                self.qualify_configuration()
                config = json.loads(self.fixture.path.read_text())
                config['upstreams']['local-llama']['base_url'] = failed_endpoint
                self.fixture.path.write_text(json.dumps(config))
                self.fixture.command('connect')
                result = self.command('ask', '--model', 'local-code', '--prompt-file', str(self.prompt),
                                      '--max-output-tokens', '8')
                self.assertEqual(result.returncode, 2)
                receipt = json.loads(result.stderr)
                self.assertEqual(receipt['error']['code'], 'router_request_rejected')
                self.assertEqual(receipt['retried'], False)
                self.assertNotIn('PRIVATE_PROVIDER_FAILURE', result.stderr)
                self.assertEqual([len(failed), len(local), len(cloud)], [1, 0, 0])
                self.assert_normal_unchanged()

    def test_explicit_interactive_choice_does_not_bypass_coding_qualification(self):
        with self.fixture.configured() as (local, cloud):
            self.qualify_configuration()
            config = json.loads(self.fixture.path.read_text())
            config['route_admission']['local_routes']['local-code']['task_types'] = ['interactive', 'code_change']
            self.fixture.path.write_text(json.dumps(config))
            self.prompt.write_text('Fix the arithmetic function in this Python code.')
            self.fixture.command('connect')
            result = self.command('ask', '--model', 'local-code', '--prompt-file', str(self.prompt),
                                  '--max-output-tokens', '8')
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stderr)['error']['code'], 'router_request_rejected')
            self.assertEqual([len(local), len(cloud)], [0, 0])
            self.assert_normal_unchanged()

    def test_oversized_prompt_is_rejected_before_starting_or_contacting_any_router(self):
        self.prompt.write_bytes(b'x' * 65537)
        result = self.command('ask', '--prompt-file', str(self.prompt))
        self.assertEqual(result.returncode, 2)
        receipt = json.loads(result.stderr)
        self.assertEqual(receipt['error']['code'], 'prompt_limit')
        self.assertEqual(receipt['submission'], 'not_submitted')
        self.assert_normal_unchanged()

    def test_changed_owned_provider_settings_block_submission_without_repairing_user_edits(self):
        with self.fixture.configured() as (local, cloud):
            self.qualify_configuration()
            self.fixture.command('connect')
            configuration = self.fixture.home / 'config.toml'
            edited = configuration.read_text() + 'request_max_retries = 1\n'
            configuration.write_text(edited)
            result = self.command('ask', '--model', 'local-code', '--prompt-file', str(self.prompt),
                                  '--max-output-tokens', '8')
            self.assertEqual(result.returncode, 2)
            receipt = json.loads(result.stderr)
            self.assertEqual(receipt['error']['code'], 'router_ownership_changed')
            self.assertEqual(receipt['submission'], 'not_submitted')
            self.assertEqual(configuration.read_text(), edited)
            self.assertEqual([len(local), len(cloud)], [0, 0])
            self.assert_normal_unchanged()

    def test_local_inference_can_take_longer_than_connection_setup_within_the_task_deadline(self):
        entered, release = threading.Event(), threading.Event()
        with self.fixture.configured(hold_first=(entered, release)) as (local, cloud):
            self.qualify_configuration()
            config = json.loads(self.fixture.path.read_text())
            config['upstream_timeout_seconds'] = 7
            self.fixture.path.write_text(json.dumps(config))
            self.fixture.command('connect')
            try:
                result = self.command('ask', '--model', 'local-code', '--prompt-file', str(self.prompt),
                                      '--max-output-tokens', '8', '--timeout-seconds', '8')
            finally:
                release.set()
            self.assertTrue(entered.is_set())
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['text'], 'LOCAL_DESTINATION')
            self.assertEqual([len(local), len(cloud)], [1, 0])
            self.assert_normal_unchanged()


if __name__ == '__main__':
    unittest.main()
