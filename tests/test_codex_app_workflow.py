"""Rich coding-client requests observed through the approved router workflow."""
import json
from importlib.resources import files
import unittest

from tests import test_execution_destination_workflow as destination


class CodexAppWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = destination.ExecutionDestinationWorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def configuration(self, local, cloud):
        value = self.fixture.admission_configuration(local, cloud, mode='local_only')
        value['max_request_bytes'] = 2 * 1024 * 1024
        value['model_routes']['local-code']['capabilities'] = [
            'responses', 'text', 'streaming', 'tools', 'codex_http', 'codex_http_0160', 'codex_llama_v1']
        value['route_admission']['local_routes']['local-code'].update(max_request_bytes=2 * 1024 * 1024,
                                                                     task_types=['summarization'])
        return value

    def test_rich_model_catalog_keeps_plugin_mcp_and_reasoning_features_available(self):
        with destination.provider('LOCAL') as (local, requests), \
             destination.provider('UNUSED_CLOUD') as (cloud, cloud_requests):
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                model = submit(method='GET', path='/models')['body']['models'][0]
                for field in ('include_skills_usage_instructions', 'include_plugin_usage_instructions',
                              'include_apps_usage_instructions', 'supports_search_tool', 'supports_reasoning_summary_parameter'):
                    self.assertIs(model[field], True, field)
                self.assertEqual(model['apply_patch_tool_type'], 'freeform')
                self.assertEqual(model['context_window'], 32768)
            self.assertEqual((requests, cloud_requests), ([], []))

    def test_bundled_app_profile_accepts_native_automatic_review_without_dropping_parent_tags(self):
        profile = json.loads(files('hormuz').joinpath(
            'local-setup-codex-app-0162-llama-b11617-qwen3-4b-v2.json').read_text())
        thread = '11111111-1111-4111-8111-111111111111'
        parent = '22222222-2222-4222-8222-222222222222'
        tags = {'thread_id': thread, 'session_id': thread, 'turn_id': 'synthetic-review-turn',
                'root_turn_id': 'synthetic-parent-turn', 'parent_turn_id': 'synthetic-parent-turn',
                'x-codex-parent-thread-id': parent, 'x-openai-subagent': 'auto_review'}
        output = [{'type': 'message', 'role': 'assistant', 'content': [
            {'type': 'output_text', 'text': '{"decision":"deny"}'}]}]
        request = {'input': 'Assess this synthetic read-only command approval.', 'client_metadata': tags,
                   'reasoning': {'effort': 'low', 'summary': 'auto'},
                   'text': {'format': {'type': 'json_schema', 'name': 'approval', 'strict': True,
                       'schema': {'type': 'object', 'properties': {'decision': {'enum': ['allow', 'deny']}},
                                  'required': ['decision'], 'additionalProperties': False}}}}
        with destination.provider('LOCAL', response_fields={'output': output}) as (local, requests), \
             destination.provider('UNUSED_CLOUD') as (cloud, cloud_requests):
            value = self.configuration(local, cloud)
            value['model_routes']['local-code']['capabilities'] = profile['capabilities']
            value['route_admission']['local_routes']['local-code']['task_types'] = profile['task_types']
            value['ai_work'] = {'enabled': True, 'database': str(self.fixture.root / 'work.sqlite3')}
            headers = {'thread-id': thread, 'session-id': thread}
            with self.fixture.connected_client(value) as submit:
                response = submit(headers=headers, request=request)
                self.assertEqual(response['status'], 200, response)
                self.assertEqual(json.loads(response['body']['output'][0]['content'][0]['text']), {'decision': 'deny'})
                none = submit(headers=headers, request={**request, 'reasoning': {'effort': 'none'}})
                self.assertEqual(none['status'], 200, none)
                for invalid in ({'parent_turn_id': {}}, {'x-openai-subagent': 'x' * 2049}, {'unknown-parent-tag': 'invalid'}):
                    rejected = submit(headers=headers, request={**request, 'client_metadata': {**tags, **invalid}})
                    self.assertEqual(rejected['status'], 503, rejected)
                work = submit(method='GET', path='/work/jobs/' + response['headers']['x-hormuz-work-id'])['body']
                self.assertEqual([attempt['state'] for attempt in work['attempts']], ['succeeded', 'succeeded'])
            self.assertEqual(len(requests), 2)
            self.assertEqual(requests[0]['body']['client_metadata'], tags)
            self.assertEqual(requests[0]['body']['text'], request['text'])
            self.assertEqual(requests[0]['body']['reasoning']['effort'], 'low')
            self.assertIs(requests[1]['body']['chat_template_kwargs']['enable_thinking'], False)
            self.assertEqual(requests[1]['body']['reasoning_budget_tokens'], 0)
            self.assertEqual(cloud_requests, [])

    def test_large_namespaced_tool_request_and_response_keep_the_client_tool_identity(self):
        tool = {'type': 'namespace', 'name': 'mcp__fixture', 'description': 'Local fixture tools.',
                'tools': [{'type': 'function', 'name': 'echo', 'description': 'Echo supplied text.',
                           'parameters': {'type': 'object', 'properties': {'text': {'type': 'string'}},
                                          'required': ['text'], 'additionalProperties': False}, 'strict': False}]}
        model_output = [{'type': 'function_call', 'call_id': 'fixture-call',
                         'name': 'hormuz_mcp__fixture__echo', 'arguments': '{"text":"fixture"}'}]
        with destination.provider('LOCAL', response_fields={'output': model_output}) as (local, requests), \
             destination.provider('UNUSED_CLOUD') as (cloud, cloud_requests):
            value = self.configuration(local, cloud)
            with self.fixture.connected_client(value) as submit:
                response = submit(request={'input': 'Summarize ' + 'fixture text. ' * 8000, 'tools': [tool]})
                self.assertEqual(response['status'], 200, response)
                self.assertEqual(response['headers']['x-hormuz-destination-locality'], 'local')
                call = response['body']['output'][0]
                self.assertEqual((call['type'], call['namespace'], call['name']),
                                 ('function_call', 'mcp__fixture', 'echo'))
                self.assertEqual(call['arguments'], '{"text":"fixture"}')
            self.assertEqual([(tool['type'], tool['name']) for tool in requests[0]['body']['tools']],
                             [('function', 'hormuz_mcp__fixture__echo')])
            self.assertEqual(cloud_requests, [])

    def test_freeform_tool_remains_a_client_custom_call_in_streamed_output(self):
        tool = {'type': 'custom', 'name': 'apply_patch', 'description': 'Apply the supplied patch.',
                'format': {'type': 'grammar', 'syntax': 'lark', 'definition': 'start: "PATCH_OK"'}}
        output = [{'type': 'function_call', 'id': 'fc-patch', 'call_id': 'patch-call', 'name': 'apply_patch',
                   'arguments': '{"input":"PATCH_OK"}'}]
        with destination.provider('LOCAL', event_stream=True, response_fields={'output': output}) as (local, requests), \
             destination.provider('UNUSED_CLOUD') as (cloud, cloud_requests):
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                response = submit(request={'input': 'Summarize the fixture.', 'stream': True, 'tools': [tool]})
                self.assertEqual(response['status'], 200, response)
                events = [json.loads(line[6:]) for line in response['body'].splitlines() if line.startswith('data: ')]
                self.assertEqual(events[0]['item'], {'type': 'custom_tool_call', 'id': 'fc-patch',
                                                    'call_id': 'patch-call', 'name': 'apply_patch', 'input': 'PATCH_OK'})
                self.assertEqual(events[-1]['response']['output'][0]['input'], 'PATCH_OK')
            self.assertEqual(requests[0]['body']['tools'][0]['parameters']['required'], ['input'])
            self.assertEqual(cloud_requests, [])

    def test_client_tool_discovery_retains_mcp_tools_and_its_continuation(self):
        discovery = {'type': 'tool_search', 'execution': 'client', 'description': 'Find available MCP tools.',
                     'parameters': {'type': 'object', 'properties': {'query': {'type': 'string'}},
                                    'required': ['query'], 'additionalProperties': False}}
        output = [{'type': 'function_call', 'id': 'fc-search', 'call_id': 'search-call', 'name': 'tool_search',
                   'arguments': '{"query":"fixture echo"}'}]
        loaded = {'type': 'namespace', 'name': 'mcp__fixture', 'description': 'Discovered fixture tools.', 'tools': [
            {'type': 'function', 'name': 'echo', 'description': 'Echo.', 'parameters': {'type': 'object'},
             'strict': False, 'defer_loading': True}]}
        thread = '11111111-1111-4111-8111-111111111111'
        tags = {'thread_id': thread, 'session_id': thread}
        headers = {'thread-id': thread, 'session-id': thread}
        with destination.provider('LOCAL', response_fields={'output': output}) as (local, requests), \
             destination.provider('UNUSED_CLOUD') as (cloud, cloud_requests):
            value = self.configuration(local, cloud)
            value['ai_work'] = {'enabled': True, 'tool_capable_aliases': ['local-code'],
                                'database': str(self.fixture.root / 'work.sqlite3')}
            with self.fixture.connected_client(value) as submit:
                first = submit(headers=headers, request={'input': 'Summarize the fixture.',
                                                        'client_metadata': tags, 'tools': [discovery]})
                self.assertEqual(first['status'], 200, first)
                call = first['body']['output'][0]
                self.assertEqual((call['type'], call['execution'], call['arguments']),
                                 ('tool_search_call', 'client', {'query': 'fixture echo'}))
                result = {'type': 'tool_search_output', 'call_id': 'search-call', 'execution': 'client', 'tools': [loaded]}
                second = submit(headers=headers, request={'tools': [discovery], 'client_metadata': tags, 'input': [call, result]})
                self.assertEqual(second['status'], 200, second)
                self.assertEqual(second['headers']['x-hormuz-routing-reason'], 'session_affinity')
                repeated = submit(headers=headers, request={'tools': [discovery], 'client_metadata': tags,
                    'input': [call, result, {**call, 'call_id': 'search-again'}, {**result, 'call_id': 'search-again'}]})
                self.assertEqual(repeated['status'], 200, repeated)
                model = submit(method='GET', path='/models')['body']['models'][0]
                self.assertIs(model['supports_search_tool'], True)
            self.assertIn('hormuz_mcp__fixture__echo', [tool['name'] for tool in requests[1]['body']['tools']])
            self.assertEqual(requests[1]['body']['input'][1]['type'], 'function_call_output')
            discovered = json.loads(requests[1]['body']['input'][1]['output'])['tools']
            self.assertEqual([tool['name'] for tool in discovered], ['hormuz_mcp__fixture__echo'])
            self.assertEqual(discovered[0]['parameters'], {'type': 'object'})
            self.assertEqual([tool['name'] for tool in requests[2]['body']['tools']].count('hormuz_mcp__fixture__echo'), 1)
            self.assertEqual(cloud_requests, [])

    def test_tool_discovery_keeps_the_clients_builtin_callable_name(self):
        tool = {'type': 'tool_search', 'execution': 'client', 'description': 'Find client tools.',
                'parameters': {'type': 'object', 'properties': {'query': {'type': 'string'}}, 'required': ['query']}}
        output = [{'type': 'function_call', 'call_id': 'search-call', 'name': 'tool_search', 'arguments': '{"query":"fixture"}'}]
        with destination.provider('LOCAL', event_stream=True, response_fields={'output': output}) as (local, requests), \
             destination.provider('UNUSED_CLOUD') as (cloud, cloud_requests):
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                response = submit(request={'input': 'Summarize the fixture.', 'stream': True, 'tools': [tool]})
                self.assertEqual(response['status'], 200, response)
                events = [json.loads(line[6:]) for line in response['body'].splitlines() if line.startswith('data: ')]
                self.assertEqual(events[0]['item']['type'], 'tool_search_call')
            self.assertEqual(requests[0]['body']['tools'][0]['name'], 'tool_search')
            self.assertEqual(cloud_requests, [])

    def test_malformed_native_tool_declarations_fail_closed_without_provider_dispatch(self):
        malformed = [
            {'type': ['namespace'], 'name': 'fixture'},
            {'type': 'custom', 'name': 'patch', 'description': 'Fixture.', 'format': 'not-an-object'},
            {'type': 'namespace', 'name': 'invalid\nnamespace', 'description': 'Fixture.', 'tools': [
                {'type': 'function', 'name': 'fixture', 'parameters': {'type': 'object'}, 'strict': False}]},
            {'type': 'tool_search', 'execution': 'server', 'description': 'Hosted discovery.', 'parameters': {'type': 'object'}},
        ]
        with destination.provider('LOCAL') as (local, requests), destination.provider('UNUSED_CLOUD') as (cloud, cloud_requests):
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                for tool in malformed:
                    response = submit(request={'input': 'Summarize the fixture.', 'tools': [tool]})
                    self.assertEqual(response['status'], 503, response)
                injected = submit(request={'input': 'Summarize the fixture.', 'reasoning': {'effort': 'max'},
                                           'chat_template_kwargs': {'enable_thinking': False}})
                self.assertEqual(injected['status'], 503)
                unbounded = submit(request={'input': 'Summarize the fixture.', 'reasoning': {'effort': 'max'},
                                            'reasoning_budget_tokens': -1})
                self.assertEqual(unbounded['status'], 503)
            self.assertEqual((requests, cloud_requests), ([], []))

    def test_invalid_provider_tool_identity_settles_as_unknown_without_exposing_a_call(self):
        output = [{'type': 'function_call', 'call_id': 'bad-call', 'name': ['invalid-name'], 'arguments': '{}'}]
        with destination.provider('LOCAL', event_stream=True, response_fields={'output': output}) as (local, requests), \
             destination.provider('UNUSED_CLOUD') as (cloud, cloud_requests):
            value = self.configuration(local, cloud)
            value['ai_work'] = {'enabled': True, 'tool_capable_aliases': ['local-code'], 'database': str(self.fixture.root / 'work.sqlite3')}
            with self.fixture.connected_client(value) as submit:
                response = submit(request={'input': 'Summarize the fixture.', 'stream': True, 'tools': [
                    {'type': 'function', 'name': 'fixture', 'description': 'Fixture.', 'parameters': {'type': 'object'}, 'strict': False}]})
                self.assertNotIn('bad-call', response['body'])
                work = submit(method='GET', path='/work/jobs/' + response['headers']['x-hormuz-work-id'])['body']
                self.assertEqual(work['attempts'][0]['state'], 'unknown')
            self.assertEqual((len(requests), cloud_requests), (1, []))

    def test_explicit_interactive_selection_does_not_require_a_prompt_prefix_or_enable_auto_routing(self):
        with destination.provider('LOCAL') as (local, requests), destination.provider('BASELINE') as (cloud, cloud_requests):
            value = self.configuration(local, cloud)
            value['route_admission']['local_routes']['local-code']['task_types'] = ['interactive']
            with self.fixture.connected_client(value) as submit:
                response = submit(request={'input': 'Hello. What is two plus five?'})
                self.assertEqual(response['status'], 200, response)
                self.assertEqual(response['headers']['x-hormuz-routed-model'], 'fixture-local-model')
                denied = submit(request={'model': 'cloud-code', 'input': 'Hello.'})
                self.assertEqual(denied['status'], 503)
            value['route_admission']['mode'] = 'auto'
            with self.fixture.connected_client(value) as submit:
                baseline = submit(request={'model': 'cloud-code', 'input': 'Hello.'})
                self.assertEqual(baseline['status'], 200, baseline)
                self.assertEqual(baseline['headers']['x-hormuz-routing-reason'], 'permitted_baseline')
            self.assertEqual((len(requests), len(cloud_requests)), (1, 1))

    def test_invalid_custom_arguments_settle_the_dispatched_attempt_as_unknown(self):
        tool = {'type': 'custom', 'name': 'apply_patch', 'description': 'Fixture patch.',
                'format': {'type': 'grammar', 'syntax': 'lark', 'definition': 'start: "PATCH_OK"'}}
        output = [{'type': 'function_call', 'call_id': 'bad-call', 'name': 'apply_patch', 'arguments': '{'}]
        with destination.provider('LOCAL', event_stream=True, response_fields={'output': output}) as (local, requests), \
             destination.provider('UNUSED_CLOUD') as (cloud, cloud_requests):
            value = self.configuration(local, cloud)
            value['ai_work'] = {'enabled': True, 'tool_capable_aliases': ['local-code'], 'database': str(self.fixture.root / 'work.sqlite3')}
            with self.fixture.connected_client(value) as submit:
                response = submit(request={'input': 'Summarize the fixture.', 'stream': True, 'tools': [tool]})
                self.assertNotIn('bad-call', response['body'])
                work = submit(method='GET', path='/work/jobs/' + response['headers']['x-hormuz-work-id'])['body']
                self.assertEqual(work['attempts'][0]['state'], 'unknown')
            self.assertEqual((len(requests), cloud_requests), (1, []))

    def test_expanded_native_namespace_output_still_settles_real_usage(self):
        tool = {'type': 'namespace', 'name': 'fixture', 'description': 'Fixture.', 'tools': [
            {'type': 'function', 'name': 'echo', 'description': 'Echo.', 'parameters': {'type': 'object'}, 'strict': False}]}
        output = [{'type': 'function_call', 'call_id': 'fixture-call', 'name': 'hormuz_fixture__echo', 'arguments': '{}'}]
        with destination.provider('LOCAL', event_stream=True, compact_json=True, response_fields={'output': output}) as (local, requests), \
             destination.provider('UNUSED_CLOUD') as (cloud, cloud_requests):
            value = self.configuration(local, cloud)
            value['ai_work'] = {'enabled': True, 'tool_capable_aliases': ['local-code'], 'database': str(self.fixture.root / 'work.sqlite3')}
            with self.fixture.connected_client(value) as submit:
                response = submit(request={'input': 'Summarize the fixture.', 'stream': True, 'tools': [tool]})
                self.assertEqual(response['status'], 200, response)
                self.assertIn('"namespace":"fixture"', response['body'])
                work = submit(method='GET', path='/work/jobs/' + response['headers']['x-hormuz-work-id'])['body']
                self.assertEqual(work['attempts'][0]['state'], 'succeeded')
                self.assertEqual(work['costs']['unknown_attempts'], 0)
            self.assertEqual((len(requests), cloud_requests), (1, []))

    def test_max_reasoning_and_plaintext_reasoning_continuation_reach_the_local_model(self):
        reasoning = {'type': 'reasoning', 'id': 'rs-fixture',
                     'summary': [{'type': 'summary_text', 'text': 'I checked the fixture.'}],
                     'content': [{'type': 'reasoning_text', 'text': 'I checked the fixture.'}]}
        thread = '11111111-1111-4111-8111-111111111111'
        tags = {'thread_id': thread, 'session_id': thread}
        headers = {'thread-id': thread, 'session-id': thread}
        with destination.provider('LOCAL', response_fields={'output': [reasoning]}) as (local, requests), \
             destination.provider('UNUSED_CLOUD') as (cloud, cloud_requests):
            value = self.configuration(local, cloud)
            value['ai_work'] = {'enabled': True, 'database': str(self.fixture.root / 'work.sqlite3')}
            with self.fixture.connected_client(value) as submit:
                first = submit(headers=headers, request={'input': 'Summarize the fixture.', 'client_metadata': tags,
                                                        'reasoning': {'effort': 'max', 'summary': 'auto'}})
                self.assertEqual(first['status'], 200, first)
                self.assertEqual(first['body']['output'][0], reasoning)
                replay = {key: item for key, item in reasoning.items() if key != 'content'}
                second = submit(headers=headers, request={'input': [replay], 'client_metadata': tags,
                                                         'reasoning': {'effort': 'max', 'summary': 'auto'}})
                self.assertEqual(second['status'], 200, second)
                self.assertEqual(second['headers']['x-hormuz-routing-reason'], 'session_affinity')
                model = submit(method='GET', path='/models')['body']['models'][0]
                self.assertIs(model['supports_reasoning_summary_parameter'], True)
                self.assertIn('max', [level['effort'] for level in model['supported_reasoning_levels']])
                denied = submit(headers=headers, request={'input': [{**replay, 'encrypted_content': 'opaque-provider-state'}],
                                                         'client_metadata': tags, 'reasoning': {'effort': 'max'}})
                self.assertNotEqual(denied['status'], 200)
            self.assertEqual(len(requests), 2)
            self.assertTrue(all(request['body']['chat_template_kwargs']['enable_thinking'] for request in requests))
            self.assertEqual(requests[0]['body']['reasoning']['effort'], 'max')
            self.assertGreater(requests[0]['body']['reasoning_budget_tokens'], 0)
            self.assertLess(requests[0]['body']['reasoning_budget_tokens'], requests[0]['body']['max_output_tokens'])
            self.assertEqual(requests[1]['body']['input'][0]['content'][0]['text'], 'I checked the fixture.')
            self.assertEqual(cloud_requests, [])

    def test_live_web_search_is_delegated_to_existing_client_tool_discovery(self):
        tools = [{'type': 'tool_search', 'execution': 'client', 'description': 'Find client tools.',
                  'parameters': {'type': 'object', 'properties': {'query': {'type': 'string'}}, 'required': ['query']}},
                 {'type': 'web_search', 'external_web_access': True}]
        output = [{'type': 'function_call', 'call_id': 'search-call', 'name': 'hormuz_web_search',
                   'arguments': '{"query":"fixture documentation"}'}]
        with destination.provider('LOCAL', response_fields={'output': output}) as (local, requests), \
             destination.provider('UNUSED_CLOUD') as (cloud, cloud_requests):
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                response = submit(request={'input': 'Summarize the fixture documentation.', 'tools': tools})
                self.assertEqual(response['status'], 200, response)
                call = response['body']['output'][0]
                self.assertEqual((call['type'], call['execution']), ('tool_search_call', 'client'))
                self.assertEqual(call['arguments'], {'query': 'search_service web search', 'limit': 1})
                for unsupported in ({'type': 'web_search', 'external_web_access': False},
                                    {'type': 'web_search', 'external_web_access': True, 'filters': {'allowed_domains': ['example.test']}}):
                    self.assertNotEqual(submit(request={'input': 'Summarize the fixture.', 'tools': [tools[0], unsupported]})['status'], 200)
            self.assertEqual(len(requests), 1)
            self.assertEqual(cloud_requests, [])

    def test_streamed_namespaced_call_and_client_result_continue_on_the_same_local_destination(self):
        tool = {'type': 'namespace', 'name': 'fixture', 'description': 'Fixture.', 'tools': [
            {'type': 'function', 'name': 'echo', 'parameters': {'type': 'object'}, 'description': 'Echo.', 'strict': False}]}
        output = [{'type': 'function_call', 'id': 'fc-fixture', 'call_id': 'fixture-call',
                   'name': 'hormuz_fixture__echo', 'arguments': '{}'}]
        tags = {'thread_id': '11111111-1111-4111-8111-111111111111',
                'session_id': '11111111-1111-4111-8111-111111111111'}
        headers = {'thread-id': tags['thread_id'], 'session-id': tags['session_id']}
        with destination.provider('LOCAL', event_stream=True, response_fields={'output': output}) as (local, requests), \
             destination.provider('UNUSED_CLOUD') as (cloud, cloud_requests):
            value = self.configuration(local, cloud)
            value['ai_work'] = {'enabled': True, 'tool_capable_aliases': ['local-code'],
                                'database': str(self.fixture.root / 'work.sqlite3')}
            with self.fixture.connected_client(value) as submit:
                first = submit(headers=headers, request={'input': 'Summarize the fixture.', 'stream': True,
                                                        'client_metadata': tags, 'tools': [tool]})
                self.assertEqual(first['status'], 200, first)
                events = [json.loads(line[6:]) for line in first['body'].splitlines() if line.startswith('data: ')]
                call = events[0]['item']
                self.assertEqual((call['namespace'], call['name']), ('fixture', 'echo'))
                second = submit(headers=headers, request={'stream': True, 'client_metadata': tags, 'tools': [tool],
                    'input': [call, {'type': 'function_call_output', 'call_id': 'fixture-call', 'output': 'ROUTING_OK'}]})
                self.assertEqual(second['status'], 200, second)
                self.assertEqual(second['headers']['x-hormuz-routing-reason'], 'session_affinity')
                self.assertEqual(first['headers']['x-hormuz-work-id'], second['headers']['x-hormuz-work-id'])
            self.assertEqual(requests[1]['body']['input'][0]['name'], 'hormuz_fixture__echo')
            self.assertNotIn('namespace', requests[1]['body']['input'][0])
            self.assertEqual(cloud_requests, [])

    def test_completed_tool_and_reasoning_status_remain_valid_during_native_replay(self):
        tool = {'type': 'function', 'name': 'fixture', 'description': 'Fixture.', 'parameters': {'type': 'object'}, 'strict': False}
        thread = '11111111-1111-4111-8111-111111111111'
        tags = {'thread_id': thread, 'session_id': thread}
        headers = {'thread-id': thread, 'session-id': thread}
        with destination.provider('LOCAL') as (local, requests), destination.provider('UNUSED_CLOUD') as (cloud, cloud_requests):
            value = self.configuration(local, cloud)
            value['ai_work'] = {'enabled': True, 'tool_capable_aliases': ['local-code'], 'database': str(self.fixture.root / 'work.sqlite3')}
            with self.fixture.connected_client(value) as submit:
                self.assertEqual(submit(headers=headers, request={'input': 'Summarize the fixture.', 'client_metadata': tags})['status'], 200)
                replay = [{'type': 'reasoning', 'id': 'rs-fixture', 'status': 'completed', 'summary': [],
                           'content': [{'type': 'reasoning_text', 'text': 'Checked the fixture.'}]},
                          {'type': 'function_call', 'name': 'fixture', 'call_id': 'fixture-call', 'status': 'completed', 'arguments': '{}'},
                          {'type': 'function_call_output', 'call_id': 'fixture-call', 'output': 'ROUTING_OK'}]
                response = submit(headers=headers, request={'input': replay, 'tools': [tool], 'client_metadata': tags})
                self.assertEqual(response['status'], 200, response)
                self.assertEqual(response['headers']['x-hormuz-routing-reason'], 'session_affinity')
            self.assertEqual(requests[1]['body']['input'][1]['status'], 'completed')
            self.assertEqual(cloud_requests, [])
