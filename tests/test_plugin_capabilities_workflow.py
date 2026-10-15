"""Plugin capabilities observed at the shared public CLI workflow seam."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from tests import test_execution_destination_workflow as workflow


class PluginCapabilitiesWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = workflow.ExecutionDestinationWorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root.resolve()
        self.config = self.root / 'gateway.json'
        self.fixture.environment['HORMUZ_HISTORY_HOME'] = str(self.root / 'history')
        self.environment = {**os.environ, **self.fixture.environment,
                            'HORMUZ_HISTORY_HOME': str(self.root / 'history'),
                            'CODEX_HOME': str(self.root / 'codex')}

    def command(self, *arguments, expected=0):
        result = subprocess.run([sys.executable, '-m', 'hormuz', '--config', str(self.config), *arguments],
                                env=self.environment, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, expected, result.stderr or result.stdout)
        value = json.loads(result.stdout)
        self.assertNotIn('fixture-coding-client-credential', result.stdout)
        return value

    def test_diagnose_reports_disconnected_and_invalid_configuration_without_mutation(self):
        self.config.write_text(json.dumps(self.fixture.configuration('http://127.0.0.1:1', 'http://127.0.0.1:2')))
        before = self.config.read_bytes()
        result = self.command('client', 'diagnose', 'codex')
        self.assertEqual(result['schema_id'], 'hormuz.client-diagnosis')
        self.assertEqual(result['connection']['phase'], 'disconnected')
        self.assertEqual(result['task_completion'], 'unobserved')
        self.assertEqual(result['action'], 'preview_connection')
        self.assertEqual(self.config.read_bytes(), before)
        self.config.write_text('{"private":"DO_NOT_ECHO", invalid}')
        result = self.command('client', 'diagnose', 'codex', expected=2)
        self.assertEqual(result['error']['code'], 'configuration_invalid')
        self.assertNotIn('DO_NOT_ECHO', json.dumps(result))

    def test_explain_observed_request_without_exposing_retained_text(self):
        with workflow.provider('PRIVATE_REPLY') as (endpoint, received):
            value = self.fixture.configuration(endpoint, endpoint)
            with self.fixture.connected_client(value) as submit:
                self.command('history', 'enable')
                submit(request={'input': 'PRIVATE_PROMPT', 'max_output_tokens': 8})
                entry = self.command('history', 'list')['entries'][0]
                result = self.command('request', 'explain', entry['id'])
                self.assertEqual(result['schema_id'], 'hormuz.request-explanation')
                self.assertEqual(result['observation']['routed_model'], 'fixture-local-model')
                self.assertEqual(result['observation']['locality'], 'local')
                self.assertEqual(result['policy_decision'], 'allowed')
                self.assertEqual(result['optimization'], 'unobserved')
                self.assertEqual(result['coverage'], 'retained_opt_in_history_only')
                self.assertNotIn('PRIVATE_PROMPT', json.dumps(result))
                self.assertNotIn('PRIVATE_REPLY', json.dumps(result))
                missing = self.command('request', 'explain', '0' * 32, expected=2)
                self.assertEqual(missing['error']['code'], 'request_evidence_unavailable')

    def test_policy_preview_and_new_configuration_preserve_original(self):
        self.config.write_text(json.dumps(self.fixture.configuration('http://127.0.0.1:1', 'http://127.0.0.1:2')))
        original = self.config.read_bytes()
        output = self.root / 'local-only.json'
        args = ('policy', 'configure', '--local-only', '--monthly-budget-usd', '2.50', '--output', str(output))
        preview = self.command(*args, '--dry-run')
        self.assertEqual(preview['activation'], 'not_active')
        self.assertFalse(output.exists())
        applied = self.command(*args)
        self.assertEqual(applied['activation'], 'requires_reconnect')
        self.assertEqual(self.config.read_bytes(), original)
        proposal = json.loads(output.read_text())
        self.assertEqual(proposal['route_admission']['mode'], 'local_only')
        self.assertEqual(proposal['policies']['organization']['monthly_budget_usd'], 2.5)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        failed = self.command(*args, expected=2)
        self.assertEqual(failed['error']['code'], 'output_exists')
        self.assertEqual(json.loads(output.read_text()), proposal)

    def test_policy_budget_preview_rejects_nonfinite_or_negative_values(self):
        self.config.write_text(json.dumps(self.fixture.configuration('http://127.0.0.1:1', 'http://127.0.0.1:2')))
        for amount in ('nan', 'inf', '-1'):
            result = self.command('policy', 'configure', '--monthly-budget-usd', amount,
                                  '--output', str(self.root / 'never.json'), '--dry-run', expected=2)
            self.assertEqual(result['error']['code'], 'budget_invalid')

    def test_staged_local_policy_rejects_unknown_work_without_cloud_fallback(self):
        with workflow.provider('LOCAL') as (local, local_requests), workflow.provider('CLOUD') as (cloud, cloud_requests):
            value = self.fixture.admission_configuration(local, cloud, mode='auto')
            self.config.write_text(json.dumps(value))
            output = self.root / 'strict.json'
            self.command('policy', 'configure', '--local-only', '--output', str(output))
            with self.fixture.connected_client(json.loads(output.read_text())) as submit:
                denied = submit(request={'model': 'cloud-code', 'input': 'Unknown task.'})
                self.assertEqual(denied['status'], 503)
                self.assertEqual(local_requests, [])
                self.assertEqual(cloud_requests, [])

    def test_staged_budget_rejects_reservation_before_provider_dispatch(self):
        with workflow.provider('UNUSED') as (endpoint, received):
            value = self.fixture.configuration(endpoint, endpoint)
            value['model_routes']['local-code'].update(input_cost_per_million=1, output_cost_per_million=1)
            self.config.write_text(json.dumps(value))
            output = self.root / 'budget.json'
            self.command('policy', 'configure', '--monthly-budget-usd', '0.000001', '--output', str(output))
            with self.fixture.connected_client(json.loads(output.read_text())) as submit:
                denied = submit(request={'input': 'Summarize this fixture.'})
                self.assertEqual(denied['status'], 403)
                self.assertEqual(received, [])

    def model_arguments(self):
        from tests.test_connected_client_lifetime_workflow import CODEX
        executable = self.root / 'codex-fixture'
        executable.write_text(CODEX.replace('#!PYTHON', '#!' + sys.executable, 1))
        executable.chmod(0o700)
        runtime = self.root / 'runtime'
        runtime.write_bytes(b'fixture-runtime')
        model = self.root / 'model.gguf'
        model.write_bytes(b'GGUF-fixture')
        return ('--client-executable', str(executable), '--runtime-executable', str(runtime), '--model-file', str(model))

    def test_failed_model_probe_cannot_create_a_qualified_route(self):
        with workflow.provider('WRONG') as (endpoint, received):
            value = self.fixture.admission_configuration(endpoint, endpoint)
            value['route_admission']['local_routes']['local-code'].update(max_output_tokens=128, task_types=['summarization', 'structured_extraction'])
            value['ai_work'] = {'enabled': True, 'database': str(self.root / 'work.sqlite3'), 'tool_capable_aliases': ['local-code']}
            value['policies']['organization']['max_output_tokens'] = 128
            value['model_routes']['local-code'].update(model_revision='fixture-weights-v1', capabilities=['responses', 'text', 'tools', 'structured_output'])
            with self.fixture.connected_client(value):
                original = self.config.read_bytes()
                result = self.command('model', 'qualify', '--model', 'local-code', *self.model_arguments(),
                                      '--output', str(self.root / 'qualification.json'), expected=2)
                self.assertEqual(result['schema_id'], 'hormuz.model-qualification', result)
                self.assertEqual(result['result'], 'failed')
                self.assertEqual(result['checks'][0]['name'], 'task_quality')
                self.assertFalse(result['checks'][0]['passed'])
                self.assertEqual(self.config.read_bytes(), original)
                self.assertLessEqual(len(received), 4)

    def test_successful_probes_recommend_only_current_tested_assets(self):
        from contextlib import contextmanager
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import threading

        received = []
        class Handler(BaseHTTPRequestHandler):
            timeout = 3
            def log_message(self, *_args):
                pass
            def do_GET(self):
                data = b'{"status":"ok"}'
                self.send_response(200)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                received.append(request)
                tool_result = any(isinstance(item, dict) and item.get('type') == 'function_call_output'
                                  for item in request.get('input', []) if isinstance(request.get('input'), list))
                if request.get('tools') and not tool_result:
                    output = [{'type': 'function_call', 'call_id': 'probe-call', 'name': 'lookup_count',
                               'arguments': '{"item":"boxes"}'}]
                else:
                    output = [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text',
                               'text': '{"count":7}' if 'text' in request else '7'}]}]
                data = json.dumps({'id': 'probe-reply', 'object': 'response', 'status': 'completed',
                                   'model': request['model'], 'output': output,
                                   'usage': {'input_tokens': 8, 'output_tokens': 4, 'total_tokens': 12}}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            endpoint = 'http://127.0.0.1:' + str(server.server_port)
            value = self.fixture.admission_configuration(endpoint, endpoint)
            value['route_admission']['local_routes']['local-code'].update(max_output_tokens=128, task_types=['summarization', 'structured_extraction'])
            value['ai_work'] = {'enabled': True, 'database': str(self.root / 'work.sqlite3'), 'tool_capable_aliases': ['local-code']}
            value['policies']['organization']['max_output_tokens'] = 128
            value['model_routes']['local-code'].update(model_revision='fixture-weights-v1', capabilities=['responses', 'text', 'tools', 'structured_output'])
            with self.fixture.connected_client(value):
                assets = self.model_arguments()
                report = self.root / 'qualified.json'
                qualified = self.command('model', 'qualify', '--model', 'local-code', *assets, '--output', str(report))
                self.assertEqual(qualified['result'], 'passed')
                self.assertEqual(len(received), 4)
                self.assertFalse(qualified['route_authorized'])
                self.assertEqual(report.stat().st_mode & 0o777, 0o600)
                recommendation = self.command('model', 'recommend', '--model', 'local-code', *assets, '--qualification', str(report))
                self.assertEqual(recommendation['recommendation'], 'consider_for_tested_envelope')
                self.assertEqual(recommendation['inference_memory_fit'], 'unverified')
                (self.root / 'runtime').write_bytes(b'changed-runtime')
                stale = self.command('model', 'recommend', '--model', 'local-code', *assets, '--qualification', str(report))
                self.assertEqual(stale['recommendation'], 'requalify')
                self.assertEqual(len(received), 4)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_usage_insights_are_metadata_only_and_do_not_enable_history(self):
        with workflow.provider('PRIVATE_USAGE_REPLY') as (endpoint, received):
            value = self.fixture.configuration(endpoint, endpoint)
            with self.fixture.connected_client(value) as submit:
                empty = self.command('usage', 'insights')
                self.assertEqual(empty['totals']['requests'], 0)
                self.assertFalse(empty['enabled'])
                self.command('history', 'enable')
                submit(request={'input': 'PRIVATE_USAGE_PROMPT'})
                report = self.command('usage', 'insights')
                self.assertEqual(report['observed_tokens'], {'input': 4, 'output': 2, 'unknown_usage_requests': 0})
                self.assertEqual(report['totals']['unknown_cost_requests'], 1)
                self.assertIn('local_compute_cost_unknown', report['observations'])
                self.assertFalse(report['savings_measured'])
                self.assertNotIn('PRIVATE_USAGE', json.dumps(report))
                self.command('history', 'disable')
                self.command('history', 'delete', '--all')
                self.assertEqual(self.command('usage', 'insights')['totals']['requests'], 0)

    def test_local_probes_cannot_use_a_configured_cloud_fallback(self):
        with workflow.provider('CLOUD_UNUSED') as (cloud, received):
            value = self.fixture.configuration(cloud, cloud)
            value['model_routes']['cloud-code'] = {'protocol': 'openai', 'upstream_model': 'cloud'}
            value['model_routes']['local-code'].update(model_revision='fixture-v1', capabilities=['responses', 'text'])
            value['policies']['organization'].update(allowed_models=['cloud-code'], fallback_model='cloud-code', max_output_tokens=128)
            with self.fixture.connected_client(value):
                result = self.command('model', 'qualify', '--model', 'local-code', *self.model_arguments(),
                                      '--output', str(self.root / 'denied-probes.json'), expected=2)
                self.assertEqual(result['result'], 'failed')
                self.assertEqual(result['checks'][-1]['code'], 'router_request_rejected')
                self.assertEqual(received, [])

    def test_legacy_history_stays_readable_with_unobserved_routing_evidence(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        with workflow.provider('LEGACY_PRIVATE') as (endpoint, received):
            with self.fixture.connected_client(self.fixture.configuration(endpoint, endpoint)) as submit:
                self.command('history', 'enable')
                submit()
                record = self.command('history', 'list')['entries'][0]
                # Build legacy ciphertext using the documented public envelope,
                # not a history implementation helper.
                profile = Path(self.command('history', 'status')['storage_path'])
                envelope = json.loads((Path(__file__).parent / 'fixtures/local-history-v1.json').read_text())['encryption_envelope']
                cipher = AESGCM((profile / 'device.key').read_bytes())
                path = profile / 'entries' / (record['id'] + '.enc')
                ciphertext = path.read_bytes()
                header = envelope['header_utf8'].encode()
                end = len(header) + 12
                legacy = json.loads(cipher.decrypt(ciphertext[len(header):end], ciphertext[end:], record['id'].encode()))
                legacy.pop('routing_evidence')
                legacy['schema_version'] = 1
                nonce = os.urandom(12)
                path.write_bytes(header + nonce + cipher.encrypt(nonce, json.dumps(legacy).encode(), record['id'].encode()))
                result = self.command('request', 'explain', record['id'])
                self.assertEqual(result['policy_decision'], 'unobserved')
                self.assertNotIn('LEGACY_PRIVATE', json.dumps(result))
                self.assertEqual(self.command('history', 'show', record['id'])['entry']['schema_version'], 1)

    def test_diagnosis_tracks_preview_connection_user_edits_and_restoration(self):
        from tests.test_connected_client_lifetime_workflow import ConnectedClientLifetimeWorkflowTests
        fixture = ConnectedClientLifetimeWorkflowTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with fixture.configured():
            original = (fixture.home / 'config.toml').read_text()
            preview = json.loads(fixture.command('connect', '--dry-run').stdout)
            self.assertEqual((fixture.home / 'config.toml').read_text(), original)
            fixture.command('connect')
            diagnosis = json.loads(fixture.command('diagnose', '--executable', str(fixture.executable)).stdout)
            self.assertEqual(diagnosis['client_compatibility'], 'supported')
            self.assertEqual(diagnosis['action'], 'none')
            self.assertEqual(diagnosis['connection']['router'], 'ready')
            fixture.client('edit-model')
            diagnosis = json.loads(fixture.command('diagnose').stdout)
            self.assertEqual(diagnosis['action'], 'inspect_configuration_changes')
            fixture.command('disconnect')
            status = json.loads(fixture.command('status').stdout)
            self.assertEqual((status['phase'], status['router'], status['configuration']),
                             ('disconnected', 'stopped', 'not_owned'))
            self.assertIn('model', status['retained_settings'])
            self.assertIn('user-model', (fixture.home / 'config.toml').read_text())
