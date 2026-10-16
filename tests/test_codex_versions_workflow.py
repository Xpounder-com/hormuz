"""Version-qualified Codex lifecycle through the shared public CLI."""
import hashlib
import json
from pathlib import Path
import sys
import tomllib
import unittest

from tests import test_connected_client_lifetime_workflow as lifetime
from tests import test_execution_destination_workflow as destination


class CodexVersionsWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = lifetime.ConnectedClientLifetimeWorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_codex_0160_connects_routes_and_restores_with_its_actual_version(self):
        (self.fixture.root / 'client-version.txt').write_text('0.160.0')
        with self.fixture.configured():
            preview = json.loads(self.fixture.command('connect', '--dry-run').stdout)
            self.assertEqual(preview['client_version'], '0.160.0')
            connected = json.loads(self.fixture.command('connect').stdout)
            self.assertEqual(connected['client_version'], '0.160.0')
            self.assertEqual(connected['router'], 'ready')
            self.assertEqual(self.fixture.client()['status'], 200)
            diagnosis = json.loads(self.fixture.command('diagnose', '--executable',
                                  str(self.fixture.executable), '--project', str(self.fixture.root)).stdout)
            self.assertEqual(diagnosis['client_compatibility'], 'supported')
            self.assertEqual(diagnosis['supported_client_version'], '0.160.0')
            self.assertEqual(diagnosis['override_diagnosis']['supported_client_version'], '0.160.0')
            disconnected = json.loads(self.fixture.command('disconnect').stdout)
            self.assertEqual(disconnected['phase'], 'disconnected')
            self.assertEqual(disconnected['router'], 'stopped')
            self.assertEqual(self.fixture.client('inspect')['model'], 'original-model')

    def test_switching_between_supported_versions_requires_a_new_owned_generation(self):
        with self.fixture.configured():
            original = json.loads(self.fixture.command('connect').stdout)
            (self.fixture.root / 'client-version.txt').write_text('0.160.0')
            before = (self.fixture.home / 'config.toml').read_bytes()
            changed = self.fixture.command('connect', '--dry-run', check=False)
            self.assertEqual(changed.returncode, 2)
            self.assertEqual(json.loads(changed.stderr)['error']['code'], 'connected_client_setup_changed')
            self.assertEqual((self.fixture.home / 'config.toml').read_bytes(), before)
            self.assertEqual(json.loads(self.fixture.command('status').stdout)['client_version'], '0.147.0')
            self.fixture.command('disconnect')
            upgraded = json.loads(self.fixture.command('connect').stdout)
            self.assertEqual(upgraded['client_version'], '0.160.0')
            self.assertNotEqual(upgraded['generation'], original['generation'])

    def test_codex_0160_http_profile_routes_only_with_current_qualification(self):
        fixture = destination.ExecutionDestinationWorkflowTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with destination.provider('LOCAL_0160', event_stream=True) as (local, local_requests), \
             destination.provider('CLOUD_MUST_STAY_UNUSED') as (cloud, cloud_requests):
            value = fixture.admission_configuration(local, cloud, mode='local_only')
            value['model_routes']['local-code']['capabilities'] = ['responses', 'text', 'streaming', 'tools', 'codex_http_0160']
            value['route_admission']['local_routes']['local-code']['task_types'] = ['summarization']
            request = json.loads((Path(__file__).parent / 'fixtures/codex-http-request-0.160.json').read_text())
            with fixture.connected_client(value) as submit:
                routed = submit(request=request)
                self.assertEqual(routed['status'], 200, routed)
                self.assertEqual(routed['headers']['x-hormuz-destination-locality'], 'local')
                self.assertEqual(len(local_requests), 1)
                self.assertEqual(local_requests[0]['body']['client_metadata']['root_turn_id'], 'synthetic-codex-0160-turn')
                self.assertEqual(cloud_requests, [])

    def test_unknown_codex_version_still_blocks_and_cannot_claim_clean_overrides(self):
        with self.fixture.configured():
            self.fixture.command('connect')
            before = (self.fixture.home / 'config.toml').read_bytes()
            (self.fixture.root / 'client-version.txt').write_text('0.160.1')
            result = self.fixture.command('connect', '--dry-run', check=False)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stderr)['error']['code'], 'connected_client_version_unsupported')
            diagnosis = json.loads(self.fixture.command('diagnose', '--executable',
                                  str(self.fixture.executable), '--project', str(self.fixture.root)).stdout)
            self.assertEqual(diagnosis['client_compatibility'], 'unsupported')
            self.assertEqual(diagnosis['action'], 'select_supported_client')
            self.assertEqual(set(diagnosis['override_diagnosis']['effective_routing'].values()), {'unobserved'})
            self.assertEqual((self.fixture.home / 'config.toml').read_bytes(), before)

    def test_explicit_no_reasoning_is_qualified_only_for_codex_0160(self):
        fixture = destination.ExecutionDestinationWorkflowTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        request = json.loads((Path(__file__).parent / 'fixtures/codex-http-request-0.160.json').read_text())
        request['reasoning'] = {'effort': 'none'}
        # The reasoning shape itself needs the new qualification, even without
        # a root-turn tag distinguishing the two clients.
        request['client_metadata'].pop('root_turn_id')
        with destination.provider('LOCAL_0160', event_stream=True) as (local, local_requests), \
             destination.provider('CLOUD_MUST_STAY_UNUSED') as (cloud, cloud_requests):
            value = fixture.admission_configuration(local, cloud, mode='local_only')
            value['model_routes']['local-code']['capabilities'] = ['responses', 'text', 'streaming', 'tools', 'codex_http_0160']
            value['route_admission']['local_routes']['local-code']['task_types'] = ['summarization']
            with fixture.connected_client(value) as submit:
                routed = submit(request=request)
                self.assertEqual(routed['status'], 200, routed)
                self.assertEqual(local_requests[0]['body']['reasoning'], {'effort': 'none'})
                for reasoning in ({'effort': 'high'}, {'effort': 'none', 'summary': 'auto'}, {'effort': None}):
                    self.assertEqual(submit(request={**request, 'reasoning': reasoning})['status'], 503)
            value['model_routes']['local-code']['capabilities'].remove('codex_http_0160')
            value['model_routes']['local-code']['capabilities'].append('codex_http')
            with fixture.connected_client(value) as submit:
                self.assertEqual(submit(request=request)['status'], 503)
            self.assertEqual(len(local_requests), 1)
            self.assertEqual(cloud_requests, [])

    def test_models_keeps_openai_data_and_adds_the_qualified_codex_0160_catalog(self):
        fixture = destination.ExecutionDestinationWorkflowTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with destination.provider('LOCAL_0160') as (local, local_requests), \
             destination.provider('CLOUD_MUST_STAY_UNUSED') as (cloud, cloud_requests):
            value = fixture.admission_configuration(local, cloud)
            value['model_routes']['local-code']['capabilities'].extend(['streaming', 'tools', 'codex_http_0160'])
            with fixture.connected_client(value) as submit:
                response = submit(method='GET', path='/models')
                self.assertEqual(response['status'], 200)
                catalog = response['body']
                self.assertEqual(catalog['object'], 'list')
                self.assertEqual({item['id'] for item in catalog['data']}, {'local-code', 'cloud-code'})
                self.assertEqual([item['slug'] for item in catalog.get('models', [])], ['local-code'])
                model = catalog['models'][0]
                compatibility = json.loads((Path(__file__).parent / 'fixtures/codex-model-catalog-0.160-v1.json').read_text())
                self.assertTrue(set(compatibility['model_required_fields']) <= set(model))
                self.assertEqual(hashlib.sha256(model['base_instructions'].encode()).hexdigest(),
                                 compatibility['base_instructions_sha256'])
                self.assertIs(model['supports_reasoning_summary_parameter'], False)
                self.assertEqual(model['default_reasoning_level'], 'none')
                self.assertEqual([item['effort'] for item in model['supported_reasoning_levels']], ['none'])
                self.assertEqual(model['input_modalities'], ['text'])
                self.assertEqual(model['shell_type'], 'unified_exec')
                self.assertEqual(local_requests, [])
                self.assertEqual(cloud_requests, [])

    def test_codex_0160_provider_keeps_http_and_disables_automatic_request_replay(self):
        (self.fixture.root / 'client-version.txt').write_text('0.160.0')
        with self.fixture.configured():
            self.fixture.command('connect')
            config = tomllib.loads((self.fixture.home / 'config.toml').read_text())
            provider = config['model_providers'][config['model_provider']]
            self.assertEqual(provider['wire_api'], 'responses')
            self.assertIs(provider.get('supports_websockets'), False)
            self.assertEqual(provider.get('request_max_retries'), 0)
            self.assertEqual(provider.get('stream_max_retries'), 0)
            selected = json.loads(self.fixture.path.read_text())['listen']
            self.assertEqual(provider.get('model_catalog_url'), f'http://127.0.0.1:{selected["port"]}/v1/models')

    def test_local_setup_rejects_a_profile_for_another_supported_client_before_publication(self):
        with lifetime.provider('CLOUD_MUST_STAY_UNUSED') as (cloud, requests):
            value, _, _ = self.fixture.managed_configuration(cloud)
            options = self.fixture.local_setup_options(value)
            (self.fixture.root / 'client-version.txt').write_text('0.160.0')
            before = (self.fixture.home / 'config.toml').read_bytes()
            result = self.fixture.command('setup', *options, check=False)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stderr)['error']['code'], 'connected_client_version_unsupported')
            self.assertFalse(self.fixture.path.exists())
            self.assertEqual((self.fixture.home / 'config.toml').read_bytes(), before)
            self.assertEqual(requests, [])

    def test_codex_0160_ignore_user_config_is_reported_as_a_launch_bypass(self):
        (self.fixture.root / 'client-version.txt').write_text('0.160.0')
        with self.fixture.configured():
            self.fixture.command('connect')
            before = (self.fixture.home / 'config.toml').read_bytes()
            result = json.loads(self.fixture.command('diagnose', '--project', str(self.fixture.root),
                                                    '--launch-arg=exec', '--launch-arg=--ignore-user-config').stdout)
            self.assertEqual(result['action'], 'inspect_launch_overrides')
            overrides = result['override_diagnosis']
            self.assertEqual(overrides['launch_state'], 'detected')
            self.assertEqual(overrides['effective_routing']['model_provider'], 'overridden')
            self.assertEqual(overrides['running_session'], 'unobserved')
            self.assertEqual((self.fixture.home / 'config.toml').read_bytes(), before)

    def test_codex_0160_capability_keeps_unsupported_tools_reasoning_and_metadata_blocked(self):
        fixture = destination.ExecutionDestinationWorkflowTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        request = json.loads((Path(__file__).parent / 'fixtures/codex-http-request-0.160.json').read_text())
        with destination.provider('LOCAL_MUST_STAY_UNUSED') as (local, local_requests), \
             destination.provider('CLOUD_MUST_STAY_UNUSED') as (cloud, cloud_requests):
            value = fixture.admission_configuration(local, cloud)
            value['model_routes']['local-code']['capabilities'].extend(['streaming', 'tools', 'codex_http_0160'])
            value['route_admission']['local_routes']['local-code']['task_types'] = ['summarization']
            variants = [
                {**request, 'reasoning': {'summary': 'auto'}},
                {**request, 'tools': [{'type': 'namespace', 'name': 'multi_agent_v1', 'tools': []}]},
                {**request, 'client_metadata': {**request['client_metadata'], 'root_turn_id': 'x' * 2049}},
                {**request, 'client_metadata': {**request['client_metadata'], 'unknown': 'value'}},
            ]
            with fixture.connected_client(value) as submit:
                for body in variants:
                    self.assertEqual(submit(request=body)['status'], 503)
            value['model_routes']['local-code']['capabilities'].remove('codex_http_0160')
            value['model_routes']['local-code']['capabilities'].append('codex_http')
            with fixture.connected_client(value) as submit:
                self.assertEqual(submit(request=request)['status'], 503)
            self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_ignore_user_config_also_ignores_a_profile_that_would_restore_the_owned_route(self):
        (self.fixture.root / 'client-version.txt').write_text('0.160.0')
        with self.fixture.configured():
            self.fixture.command('connect')
            before = (self.fixture.home / 'config.toml').read_bytes()
            (self.fixture.home / 'review.config.toml').write_bytes(before)
            result = json.loads(self.fixture.command('diagnose', '--project', str(self.fixture.root),
                '--launch-arg=exec', '--launch-arg=--ignore-user-config',
                '--launch-arg=--profile', '--launch-arg=review').stdout)
            self.assertEqual(result['action'], 'inspect_launch_overrides')
            self.assertEqual(result['override_diagnosis']['effective_routing']['model_provider'], 'overridden')
            self.assertEqual((self.fixture.home / 'config.toml').read_bytes(), before)

    def test_local_setup_accepts_a_matching_0160_profile_and_restores_owned_settings(self):
        (self.fixture.root / 'client-version.txt').write_text('0.160.0')
        with lifetime.provider('CLOUD_MUST_STAY_UNUSED') as (cloud, requests):
            value, _, _ = self.fixture.managed_configuration(cloud)
            options = self.fixture.local_setup_options(value)
            profile_path = Path(options[1])
            profile = json.loads(profile_path.read_text())
            profile.update(client_version='0.160.0', qualification_id='synthetic-codex0160-v1')
            profile_path.write_text(json.dumps(profile))
            self.addCleanup(lambda: self.fixture.command('disconnect', check=False))
            connected = json.loads(self.fixture.command('setup', *options).stdout)
            self.assertEqual(connected['configuration']['qualification_id'], 'synthetic-codex0160-v1')
            self.assertEqual(connected['connection']['client_version'], '0.160.0')
            self.assertEqual(self.fixture.client()['body']['output'][0]['content'][0]['text'], 'MANAGED_LOCAL')
            disconnected = json.loads(self.fixture.command('disconnect').stdout)
            self.assertEqual(disconnected['local_runtime']['state'], 'stopped')
            self.assertEqual(self.fixture.client('inspect')['model'], 'original-model')
            self.assertEqual(requests, [])

    def test_0160_disconnect_preserves_user_edits_to_new_provider_fields(self):
        (self.fixture.root / 'client-version.txt').write_text('0.160.0')
        with self.fixture.configured():
            connected = json.loads(self.fixture.command('connect').stdout)
            path = self.fixture.home / 'config.toml'
            path.write_text(path.read_text().replace('stream_max_retries = 0', 'stream_max_retries = 1'))
            disconnected = json.loads(self.fixture.command('disconnect').stdout)
            provider = 'hormuz_' + connected['generation']
            self.assertIn('model_providers.' + provider, disconnected['retained_settings'])
            restored = tomllib.loads(path.read_text())
            self.assertEqual(restored['model'], 'original-model')
            self.assertEqual(restored['model_providers'][provider]['stream_max_retries'], 1)

    def test_codex_catalog_hides_policy_denied_and_unqualified_aliases(self):
        fixture = destination.ExecutionDestinationWorkflowTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with destination.provider('LOCAL_MUST_STAY_UNUSED') as (local, local_requests), \
             destination.provider('CLOUD_MUST_STAY_UNUSED') as (cloud, cloud_requests):
            value = fixture.admission_configuration(local, cloud)
            value['model_routes']['local-code']['capabilities'].extend(['streaming', 'codex_http_0160'])
            value['policies']['organization']['allowed_models'] = ['cloud-code']
            with fixture.connected_client(value) as submit:
                catalog = submit(method='GET', path='/models')['body']
                self.assertEqual([item['id'] for item in catalog['data']], ['cloud-code'])
                self.assertEqual(catalog['models'], [])
            value['policies']['organization']['allowed_models'] = ['local-code']
            value['route_admission']['local_routes'] = {}
            with fixture.connected_client(value) as submit:
                catalog = submit(method='GET', path='/models')['body']
                self.assertEqual([item['id'] for item in catalog['data']], ['local-code'])
                self.assertEqual(catalog['models'], [])
            self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_codex_0160_identifies_project_responses_metadata_as_ignored(self):
        (self.fixture.root / 'client-version.txt').write_text('0.160.0')
        with self.fixture.configured():
            project = self.fixture.root / 'project'
            (project / '.codex').mkdir(parents=True, mode=0o700)
            (project / '.git').mkdir()
            (project / '.codex/config.toml').write_text('responses_api_metadata = {value="PRIVATE_SENTINEL"}\n')
            with (self.fixture.home / 'config.toml').open('a') as output:
                output.write('\n[projects.' + json.dumps(str(project)) + ']\ntrust_level="trusted"\n')
            self.fixture.command('connect')
            output = self.fixture.command('diagnose', '--project', str(project))
            self.assertNotIn('PRIVATE_SENTINEL', output.stdout)
            diagnosis = json.loads(output.stdout)
            self.assertEqual(diagnosis['project_overrides'], 'ignored')
            layer = diagnosis['override_diagnosis']['layers'][0]
            self.assertEqual(layer['ignored_keys'], ['responses_api_metadata'])

    def test_setup_rechecks_the_qualification_version_before_previewing_connection(self):
        with lifetime.provider('CLOUD_MUST_STAY_UNUSED') as (cloud, requests):
            value, _, _ = self.fixture.managed_configuration(cloud)
            options = self.fixture.local_setup_options(value)
            profile_path = Path(options[1])
            profile = json.loads(profile_path.read_text())
            profile['client_version'] = '0.160.0'
            profile_path.write_text(json.dumps(profile))
            self.fixture.executable.write_text(
                '#!' + sys.executable + '\n'
                'import pathlib\n'
                'p=pathlib.Path(__file__).with_name("version-count")\n'
                'count=int(p.read_text()) if p.exists() else 0\n'
                'p.write_text(str(count+1))\n'
                'print("codex-cli " + ("0.160.0" if count == 0 else "0.147.0"))\n')
            before = (self.fixture.home / 'config.toml').read_bytes()
            result = self.fixture.command('setup', *options, '--dry-run', check=False)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stderr)['error']['code'], 'connected_client_version_unsupported')
            self.assertFalse(self.fixture.path.exists())
            self.assertEqual((self.fixture.home / 'config.toml').read_bytes(), before)
            self.assertEqual(requests, [])
