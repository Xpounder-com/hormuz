"""Qualification evidence and timeout behavior at the shared public CLI seam."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest

from tests import test_plugin_capabilities_workflow as capabilities


class ModelEvidenceWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.harness = capabilities.PluginCapabilitiesWorkflowTests()
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)
        self.root = self.harness.root
        self.config = self.harness.config
        self.fixture = self.harness.fixture

    def test_hung_client_version_returns_content_free_error_and_reaps_process(self):
        value = self.fixture.configuration('http://127.0.0.1:1', 'http://127.0.0.1:2')
        self.config.write_text(json.dumps(value))
        assets = self.harness.model_arguments()
        executable = self.root / 'codex-fixture'
        pid = self.root / 'client.pid'
        executable.write_text('#!' + sys.executable + '\nimport os, time\n'
                              + 'open(' + repr(str(pid)) + ', "w").write(str(os.getpid()))\n'
                              + 'print("PRIVATE_VERSION_PAYLOAD", flush=True)\n'
                              + 'os.close(1)\ntime.sleep(30)\n')
        result = subprocess.run([sys.executable, '-m', 'hormuz', '--config', str(self.config),
                                 'model', 'recommend', '--model', 'local-code', *assets,
                                 '--qualification', str(self.root / 'missing.json')],
                                env=self.harness.environment, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2, result.stderr or result.stdout)
        self.assertEqual(json.loads(result.stdout), {'schema_id': 'hormuz.capability-error',
                         'schema_version': 1, 'error': {'code': 'capability_unavailable'}})
        self.assertEqual(result.stderr, '')
        self.assertNotIn('PRIVATE_VERSION_PAYLOAD', result.stdout)
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pid.read_text()), 0)

    @contextmanager
    def probe_provider(self, *, after_request=None):
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
                items = request.get('input')
                tool_result = isinstance(items, list) and any(isinstance(item, dict) and
                    item.get('type') == 'function_call_output' for item in items)
                if request.get('tools') and not tool_result:
                    output = [{'type': 'function_call', 'call_id': 'probe-call', 'name': 'lookup_count',
                               'arguments': '{"item":"boxes"}'}]
                else:
                    output = [{'type': 'message', 'role': 'assistant', 'content': [
                        {'type': 'output_text', 'text': '{"count":7}' if 'text' in request else '7'}]}]
                data = json.dumps({'id': 'probe-reply', 'object': 'response', 'status': 'completed',
                                   'model': request['model'], 'output': output,
                                   'usage': {'input_tokens': 8, 'output_tokens': 4, 'total_tokens': 12}}).encode()
                if after_request:
                    after_request(len(received))
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield 'http://127.0.0.1:' + str(server.server_port), received
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def probe_configuration(self, endpoint):
        value = self.fixture.admission_configuration(endpoint, endpoint)
        value['route_admission']['local_routes']['local-code'].update(
            max_output_tokens=128, task_types=['summarization', 'structured_extraction'])
        value['ai_work'] = {'enabled': True, 'database': str(self.root / 'work.sqlite3'),
                            'tool_capable_aliases': ['local-code']}
        value['policies']['organization']['max_output_tokens'] = 128
        value['model_routes']['local-code'].update(model_revision='fixture-weights-v1',
                            capabilities=['responses', 'text', 'tools', 'structured_output'])
        return value

    @contextmanager
    def qualified_report(self):
        with self.probe_provider() as (endpoint, received):
            with self.fixture.connected_client(self.probe_configuration(endpoint)):
                assets = self.harness.model_arguments()
                output = self.root / 'qualification.json'
                report = self.harness.command('model', 'qualify', '--model', 'local-code',
                                              *assets, '--output', str(output))
                yield assets, output, report, received

    def test_shadow_enabled_qualification_rejects_before_any_model_request(self):
        with self.probe_provider() as (endpoint, received), self.probe_provider() as (classifier, classified):
            value = self.probe_configuration(endpoint)
            value['upstreams']['local-classifier'] = {'protocol': 'openai', 'base_url': classifier,
                                                       'locality': 'local', 'authentication': 'none'}
            value['model_routes']['local-router'] = {'protocol': 'openai', 'destination': 'local-classifier',
                'upstream_model': 'fixture-router', 'model_revision': 'fixture-router-v1',
                'capabilities': ['responses', 'text']}
            value['policies']['organization']['allowed_models'].append('local-router')
            value['shadow_router'] = {'schema_version': 1, 'mode': 'shadow', 'model': 'local-router',
                                      'local_model': 'local-code'}
            assets = self.harness.model_arguments()
            output = self.root / 'shadow-qualification.json'
            with self.fixture.connected_client(value):
                before = self.config.read_bytes()
                result = subprocess.run([sys.executable, '-m', 'hormuz', '--config', str(self.config),
                    'model', 'qualify', '--model', 'local-code', *assets, '--output', str(output)],
                    env=self.harness.environment, capture_output=True, text=True, timeout=20)
                self.assertEqual(classified, [], 'qualification must not start extra classifier requests')
                self.assertEqual(received, [])
                self.assertEqual(result.returncode, 2, result.stderr or result.stdout)
                self.assertEqual(json.loads(result.stdout), {'schema_id': 'hormuz.capability-error',
                    'schema_version': 1, 'error': {'code': 'qualification_shadow_router_unsupported'}})
                self.assertEqual(result.stderr, '')
                self.assertFalse(output.exists())
                self.assertEqual(self.config.read_bytes(), before)

            # A separately selected configuration without the shadow feature
            # retains the established four-request qualification workflow.
            standalone = copy.deepcopy(value)
            standalone.pop('shadow_router')
            with self.fixture.connected_client(standalone):
                before = self.config.read_bytes()
                report = self.harness.command('model', 'qualify', '--model', 'local-code',
                                             *assets, '--output', str(output))
                self.assertEqual(report['result'], 'passed')
                self.assertEqual(self.config.read_bytes(), before)
                self.assertEqual(classified, [])
                self.assertEqual(len(received), 4)

    def test_incomplete_qualification_cannot_recommend_a_model(self):
        with self.qualified_report() as (assets, output, report, received):
            del report['checks']
            output.write_text(json.dumps(report))
            result = self.harness.command('model', 'recommend', '--model', 'local-code',
                                         *assets, '--qualification', str(output), expected=2)
            self.assertEqual(result['error']['code'], 'qualification_invalid')
            self.assertEqual(len(received), 4)

    def test_recommendation_rejects_malformed_or_inconsistent_evidence(self):
        with self.qualified_report() as (assets, output, report, received):
            invalid = []
            for field in ('setup', 'recorded_at', 'expires_at', 'scope', 'route_authorized',
                          'runtime_binding', 'limits', 'result'):
                changed = copy.deepcopy(report)
                del changed[field]
                invalid.append(('missing_' + field, changed))
            for field, malformed in (('schema_version', True), ('result', 'unknown'),
                    ('recorded_at', True), ('expires_at', float('inf')),
                    ('recorded_at', float('nan')), ('expires_at', report['recorded_at'] - 1),
                    ('scope', 'any_coding_task'), ('route_authorized', 0),
                    ('runtime_binding', 'endpoint_attested')):
                changed = copy.deepcopy(report)
                changed[field] = malformed
                invalid.append((field + '_' + str(malformed), changed))
            for field, malformed in (('model_bytes', True), ('model_bytes', -1),
                                     ('client_sha256', 'bad-digest'), ('suite', 'unknown'),
                                     ('machine', None)):
                changed = copy.deepcopy(report)
                changed['setup'][field] = malformed
                invalid.append(('setup_' + field, changed))
            for field, malformed in (('maximum_requests', True), ('maximum_requests', 100),
                                     ('probe_deadline_seconds', float('inf')), ('retries', 1)):
                changed = copy.deepcopy(report)
                changed['limits'][field] = malformed
                invalid.append(('limits_' + field, changed))
            for checks in ([], [{'name': 'task_quality', 'passed': True}] * 4,
                    [{'name': item['name'], 'passed': 1} for item in report['checks']],
                    [*report['checks'][:3], {'name': 'tool_result', 'passed': False}],
                    [{'name': 'unknown', 'passed': False} for _ in range(4)]):
                changed = copy.deepcopy(report)
                changed['checks'] = checks
                invalid.append(('checks_' + str(len(invalid)), changed))
            changed = copy.deepcopy(report)
            changed['result'] = 'failed'
            invalid.append(('failure_without_failed_check', changed))
            for field, malformed in (('generation', 'unverified'), ('generation', True),
                                     ('model_alias', 'second-code'), ('model_revision', 'other-weights'),
                                     ('destination', None), ('upstream_model', [])):
                changed = copy.deepcopy(report)
                changed['execution'][field] = malformed
                invalid.append(('execution_' + field, changed))
            for malformed in (None, {}):
                changed = copy.deepcopy(report)
                changed['execution'] = malformed
                invalid.append(('incomplete_execution_' + str(malformed), changed))
            changed = copy.deepcopy(report)
            del changed['execution']
            invalid.append(('missing_execution', changed))
            for name, changed in invalid:
                with self.subTest(evidence=name):
                    output.write_text(json.dumps(changed))
                    result = self.harness.command('model', 'recommend', '--model', 'local-code',
                                             *assets, '--qualification', str(output), expected=2)
                    self.assertEqual(result['error']['code'], 'qualification_invalid')
            self.assertEqual(len(received), 4)

            # Ambiguous JSON must not let a duplicate override validated fields.
            output.write_text(json.dumps(report)[:-1] + ',"result":"passed"}')
            result = self.harness.command('model', 'recommend', '--model', 'local-code',
                                         *assets, '--qualification', str(output), expected=2)
            self.assertEqual(result['error']['code'], 'qualification_invalid')

    def test_substituted_destination_cannot_qualify_the_requested_alias(self):
        with self.probe_provider() as (first, first_requests), self.probe_provider() as (second, second_requests):
            value = self.probe_configuration(first)
            value['upstreams']['local-second'] = {'protocol': 'openai', 'base_url': second,
                                                  'locality': 'local', 'authentication': 'none'}
            value['model_routes']['second-code'] = {**value['model_routes']['local-code'],
                                                    'destination': 'local-second',
                                                    'model_revision': 'second-weights-v1'}
            value['policies']['organization']['allowed_models'].append('second-code')
            value['route_admission']['local_routes']['second-code'] = {
                **value['route_admission']['local_routes']['local-code'],
                'model_revision': 'second-weights-v1', 'qualification_id': 'second-qualified'}
            value['ai_work']['tool_capable_aliases'].append('second-code')
            # The second alias can serve 128 tokens; the requested alias cannot.
            value['route_admission']['local_routes']['local-code']['max_output_tokens'] = 64
            with self.fixture.connected_client(value):
                result = self.harness.command('model', 'qualify', '--model', 'local-code',
                    *self.harness.model_arguments(), '--output', str(self.root / 'substituted.json'), expected=2)
                self.assertEqual(result['result'], 'failed')
                self.assertEqual(result['checks'][-1]['code'], 'execution_selection_changed')
                self.assertEqual(first_requests, [])
                self.assertEqual(len(second_requests), 1)

    def test_generation_change_during_probes_invalidates_the_suite(self):
        with self.probe_provider() as (endpoint, received):
            value = self.probe_configuration(endpoint)
            # Identical transport/model facts still represent separate generations.
            value['model_routes']['second-code'] = dict(value['model_routes']['local-code'])
            value['policies']['organization']['allowed_models'].append('second-code')
            value['route_admission']['local_routes']['second-code'] = {
                **value['route_admission']['local_routes']['local-code'], 'qualification_id': 'second-qualified'}
            value['route_admission']['local_routes']['local-code']['task_types'] = ['summarization']
            value['ai_work']['tool_capable_aliases'].append('second-code')
            with self.fixture.connected_client(value):
                result = self.harness.command('model', 'qualify', '--model', 'local-code',
                    *self.harness.model_arguments(), '--output', str(self.root / 'generation.json'), expected=2)
                self.assertEqual(result['result'], 'failed')
                self.assertEqual(result['checks'][0], {'name': 'task_quality', 'passed': True})
                self.assertEqual(result['checks'][-1]['code'], 'execution_selection_changed')
                self.assertEqual(len(received), 2)

    def test_live_router_generation_is_required_to_recommend_current_evidence(self):
        with self.probe_provider() as (endpoint, received):
            value = self.probe_configuration(endpoint)
            assets = self.harness.model_arguments()
            output = self.root / 'qualification.json'
            with self.fixture.connected_client(value):
                report = self.harness.command('model', 'qualify', '--model', 'local-code',
                                             *assets, '--output', str(output))
                self.assertEqual(report['schema_version'], 2)
                self.assertEqual(report['execution']['model_alias'], 'local-code')
                self.assertEqual(report['execution']['destination'], 'local-llama')
                self.assertRegex(report['execution']['generation'], '^[a-f0-9]{64}$')
                recommendation = self.harness.command('model', 'recommend', '--model', 'local-code',
                                             *assets, '--qualification', str(output))
                self.assertTrue(recommendation['evidence_current'])
            # Identical configuration/assets with a restarted router are stale.
            with self.fixture.connected_client(value):
                recommendation = self.harness.command('model', 'recommend', '--model', 'local-code',
                                             *assets, '--qualification', str(output))
                self.assertFalse(recommendation['evidence_current'])
                self.assertEqual(recommendation['recommendation'], 'requalify')
            unavailable = self.harness.command('model', 'recommend', '--model', 'local-code',
                                             *assets, '--qualification', str(output))
            self.assertEqual(unavailable['recommendation'], 'requalify')
            self.assertEqual(len(received), 4)

    def test_failed_expired_and_legacy_evidence_require_requalification_without_dispatch(self):
        import time
        with self.qualified_report() as (assets, output, report, received):
            variants = []
            failed = copy.deepcopy(report)
            failed['checks'][0]['passed'] = False
            failed['result'] = 'failed'
            variants.append(('failed', failed))
            expired = copy.deepcopy(report)
            expired['recorded_at'] = int(time.time()) - 8 * 86400
            expired['expires_at'] = int(time.time()) - 86400
            variants.append(('expired', expired))
            legacy = copy.deepcopy(report)
            legacy.pop('execution')
            legacy['schema_version'] = 1
            variants.append(('legacy', legacy))
            for name, changed in variants:
                with self.subTest(evidence=name):
                    output.write_text(json.dumps(changed))
                    result = self.harness.command('model', 'recommend', '--model', 'local-code',
                                             *assets, '--qualification', str(output))
                    self.assertFalse(result['evidence_current'])
                    self.assertEqual(result['recommendation'], 'requalify')
            self.assertEqual(len(received), 4)

    def test_loopback_hostname_preserves_public_status_identity_verification(self):
        with self.probe_provider() as (endpoint, received):
            value = self.probe_configuration(endpoint)
            value['listen']['host'] = 'localhost'
            with self.fixture.connected_client(value):
                assets = self.harness.model_arguments()
                output = self.root / 'localhost.json'
                report = self.harness.command('model', 'qualify', '--model', 'local-code',
                                             *assets, '--output', str(output))
                self.assertEqual(report['result'], 'passed')
                result = self.harness.command('model', 'recommend', '--model', 'local-code',
                                             *assets, '--qualification', str(output))
                self.assertTrue(result['evidence_current'])
                self.assertEqual(len(received), 4)

    def test_supported_long_model_identifiers_round_trip_qualification_evidence(self):
        with self.probe_provider() as (endpoint, received):
            value = self.probe_configuration(endpoint)
            alias = 'a' * 256
            upstream_model = 'm' * 257
            route = value['model_routes'].pop('local-code')
            route['upstream_model'] = upstream_model
            value['model_routes'][alias] = route
            qualification = value['route_admission']['local_routes'].pop('local-code')
            value['route_admission']['local_routes'][alias] = qualification
            value['policies']['organization']['allowed_models'] = [alias]
            value['ai_work']['tool_capable_aliases'] = [alias]
            with self.fixture.connected_client(value):
                assets = self.harness.model_arguments()
                output = self.root / 'long-identifiers.json'
                report = self.harness.command('model', 'qualify', '--model', alias,
                                             *assets, '--output', str(output))
                self.assertEqual(report['result'], 'passed')
                self.assertEqual(report['setup']['model_alias'], alias)
                self.assertEqual(report['execution']['upstream_model'], upstream_model)
                result = self.harness.command('model', 'recommend', '--model', alias,
                                             *assets, '--qualification', str(output))
                self.assertTrue(result['evidence_current'])
                self.assertEqual(result['recommendation'], 'consider_for_tested_envelope')
                self.assertEqual(len(received), 4)

    def test_long_configured_alias_failure_remains_readable_for_requalification(self):
        with self.probe_provider() as (endpoint, received):
            value = self.probe_configuration(endpoint)
            # Persistent client configuration accepts aliases up to2048 characters;
            # the AI Work runtime independently rejects an alias beyond256.
            alias = 'a' * 2048
            value['model_routes'][alias] = value['model_routes'].pop('local-code')
            value['route_admission']['local_routes'][alias] = value['route_admission']['local_routes'].pop('local-code')
            value['policies']['organization']['allowed_models'] = [alias]
            value['ai_work']['tool_capable_aliases'] = [alias]
            with self.fixture.connected_client(value):
                assets = self.harness.model_arguments()
                output = self.root / 'long-alias.json'
                report = self.harness.command('model', 'qualify', '--model', alias,
                                             *assets, '--output', str(output), expected=2)
                self.assertEqual(report['result'], 'failed')
                result = self.harness.command('model', 'recommend', '--model', alias,
                                             *assets, '--qualification', str(output))
                self.assertFalse(result['evidence_current'])
                self.assertEqual(result['recommendation'], 'requalify')
                self.assertEqual(received, [])
