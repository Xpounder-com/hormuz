"""Explicit Hormuz tasks share the router without replacing the host model."""
import json
import os
from pathlib import Path
import shlex
import signal
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
        self.addCleanup(lambda: subprocess.run([sys.executable,'-m','hormuz','off'],
            env={**self.fixture.environment,'CODEX_HOME':str(self.normal)},
            capture_output=True,text=True,timeout=18))

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

    def legacy_manifest(self, control_home):
        path = self.normal/'hormuz-app-bridge-v1.json'
        path.write_text(json.dumps({'schema_id':'hormuz.codex-app-bridge','schema_version':1,
            'enabled':False,'client_home':str(self.normal),'control_home':str(control_home),
            'native_executable':str(self.fixture.executable),'native_sha256':'a'*64,
            'native_version':'0.162.0-alpha.17.2','catalog_path':str(self.fixture.root/'catalog.json')}))
        path.chmod(0o600)

    def off(self):
        return subprocess.run([sys.executable,'-m','hormuz','off'],
            env={**self.fixture.environment,'CODEX_HOME':str(self.normal)},
            capture_output=True,text=True,timeout=18)

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

    def test_chat_choices_cannot_retry_through_a_configured_capacity_alternate(self):
        with lifetime.provider('ALTERNATE', status=lambda body: 429 if body['model']=='fixture-local-model' else 200) as (endpoint, requests):
            with self.fixture.configured() as (local, cloud):
                self.qualify_configuration()
                config = json.loads(self.fixture.path.read_text())
                config['upstreams']['local-llama']['base_url'] = endpoint
                config['model_routes']['local-code']['failover_alias'] = 'local-alternate'
                config['model_routes']['local-alternate'] = {**config['model_routes']['local-code'],
                    'upstream_model': 'fixture-alternate-model'}
                config['model_routes']['local-alternate'].pop('failover_alias')
                config['route_admission']['local_routes']['local-alternate'] = dict(config['route_admission']['local_routes']['local-code'])
                config['policies']['organization']['allowed_models'].append('local-alternate')
                self.fixture.path.write_text(json.dumps(config))
                self.fixture.command('connect')
                for options in ((), ('--model', 'local-code')):
                    with self.subTest(options=options):
                        before = len(requests)
                        result = self.command('ask', *options, '--prompt-file', str(self.prompt), '--max-output-tokens', '8')
                        self.assertEqual(result.returncode, 2)
                        self.assertEqual(len(requests)-before, 1)
                        self.assertEqual(requests[-1]['body']['model'], 'fixture-local-model')
                        self.assertFalse(json.loads(result.stderr)['retried'])
                self.assertEqual([len(local), len(cloud)], [0, 0])
                self.assert_normal_unchanged()

    def test_plain_off_finds_a_fresh_chat_owner_without_any_retired_bridge(self):
        (self.fixture.root/'client-version.txt').write_text('0.160.0')
        with lifetime.provider('UNUSED_CLOUD') as (cloud, requests):
            configuration, _, engine = self.fixture.managed_configuration(cloud)
            options = self.fixture.local_setup_options(configuration)
            profile_path = Path(options[1])
            profile = json.loads(profile_path.read_text())
            profile.update(schema_version=3, client_version='0.160.0', coding_evidence=None,
                           capabilities=['responses','text','codex_http','codex_llama_v1'])
            profile_path.write_text(json.dumps(profile))
            self.addCleanup(lambda: self.fixture.command('disconnect',check=False))
            self.fixture.command('setup',*options)
            models = self.command('models')
            self.assertEqual(models.returncode,0,models.stderr)
            self.assertFalse((self.normal/'hormuz-app-bridge-v1.json').exists())
            off = subprocess.run([sys.executable,'-m','hormuz','off'],
                env={**self.fixture.environment,'CODEX_HOME':str(self.normal)},
                capture_output=True,text=True,timeout=18)
            self.assertEqual(off.returncode,0,off.stderr)
            status = json.loads(self.fixture.command('status').stdout)
            self.assertEqual((status['phase'],status['router']),('disconnected','stopped'))
            self.assertEqual(status['local_runtime']['state'],'stopped')
            self.assertTrue((engine/'stopped').exists())
            self.assertEqual(requests,[])
            self.assert_normal_unchanged()

    def test_stale_chat_owner_cannot_stop_a_replacement_through_a_legacy_bridge(self):
        with self.fixture.configured():
            self.qualify_configuration()
            self.fixture.command('connect')
            self.assertEqual(self.command('models').returncode,0)
            self.legacy_manifest(self.fixture.home)
            self.fixture.command('disconnect')
            self.fixture.command('connect')
            off = self.off()
            self.assertEqual(off.returncode,2,off.stdout)
            self.assertEqual(json.loads(off.stderr)['error']['code'],'connected_client_generation_mismatch')
            self.assertEqual(json.loads(self.fixture.command('status').stdout)['phase'],'connected')
            self.assertEqual(self.command('models').returncode,0)
            self.assertEqual(self.off().returncode,0)

    def test_legacy_recovery_error_still_stops_both_owned_routers_and_remains_visible(self):
        legacy = lifetime.ConnectedClientLifetimeWorkflowTests()
        legacy.setUp()
        self.addCleanup(legacy.doCleanups)
        with self.fixture.configured(), legacy.configured():
            self.qualify_configuration()
            self.fixture.command('connect')
            legacy.command('connect')
            self.assertEqual(self.command('models').returncode,0)
            self.legacy_manifest(legacy.home)
            activation = self.normal/'hormuz-app-activation-v1.json'
            activation.write_text('{"invalid":true}')
            activation.chmod(0o600)
            off = self.off()
            self.assertEqual(off.returncode,2,off.stdout)
            self.assertEqual(json.loads(off.stderr)['error']['code'],'connected_client_state_invalid')
            for owner in (self.fixture,legacy):
                self.assertEqual(json.loads(owner.command('status').stdout)['router'],'stopped')
            self.assertEqual((self.normal/'config.toml').read_text(),self.original)
            self.assertEqual(activation.read_text(),'{"invalid":true}')

    def test_pending_legacy_shutdown_is_not_hidden_by_a_stopped_chat_owner(self):
        legacy = lifetime.ConnectedClientLifetimeWorkflowTests()
        legacy.setUp()
        self.addCleanup(legacy.doCleanups)
        with self.fixture.configured(), legacy.configured():
            self.qualify_configuration()
            self.fixture.command('connect')
            legacy.command('connect')
            self.assertEqual(self.command('models').returncode,0)
            self.legacy_manifest(legacy.home)
            processes = subprocess.check_output(['ps','-axo','pid=,args='],text=True,timeout=3)
            owned = []
            for line in processes.splitlines():
                pid, _, command = line.strip().partition(' ')
                if 'hormuz.connected_client_runtime' not in command or str(legacy.home.resolve()) not in command:
                    continue
                arguments = shlex.split(command)
                if ('hormuz.connected_client_runtime' in arguments and
                        any(argument.startswith(str(legacy.home.resolve())+'/') for argument in arguments)):
                    owned.append(int(pid))
            self.assertEqual(len(owned),1)
            os.kill(owned[0],signal.SIGSTOP)
            try:
                off = self.off()
                self.assertEqual(off.returncode,0,off.stderr)
                receipt = json.loads(off.stdout)
                self.assertEqual(receipt['phase'],'disconnecting')
                self.assertNotEqual(receipt['router'],'stopped')
                self.assertEqual(json.loads(self.fixture.command('status').stdout)['router'],'stopped')
            finally:
                os.kill(owned[0],signal.SIGCONT)
            self.assertEqual(self.off().returncode,0)

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
