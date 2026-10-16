"""Codex model selections observed through stdio, the router and fake providers."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time
import unittest

from tests import test_connected_client_lifetime_workflow as lifetime


NATIVE = r'''#!PYTHON
import http.client,json,os,pathlib,sys,tomllib,uuid
from urllib.parse import urlsplit
if '--version' in sys.argv:
    print('codex-cli 0.162.0-alpha.17.2');sys.exit(0)
home=pathlib.Path(os.environ['CODEX_HOME'])
threads={}
def emit(value): print(json.dumps(value),flush=True)
def configuration(): return tomllib.loads((home/'config.toml').read_text())
def setup(params,thread=None):
    current=configuration()
    providers=dict(current.get('model_providers',{}))
    for key,value in (params.get('config') or {}).items():
        if key.startswith('model_providers.'): providers[key.split('.',1)[1]]=value
    return {'id':thread or str(uuid.uuid4()),'model':params.get('model') or current['model'],
        'provider':params.get('modelProvider') or current.get('model_provider') or 'openai',
        'providers':providers,'loaded':True,'cwd':params.get('cwd') or str(home)}
def view(thread):
    return {'thread':{'id':thread['id'],'modelProvider':thread['provider'],'status':{'type':'idle'}},
        'model':thread['model'],'modelProvider':thread['provider'],'reasoningEffort':'max',
        'cwd':thread['cwd'],'approvalPolicy':'on-request','approvalsReviewer':'auto_review',
        'sandbox':{'type':'readOnly'},'serviceTier':None,'collaborationMode':None}
for line in sys.stdin:
    request=json.loads(line)
    if 'method' not in request: continue
    method=request['method'];params=request.get('params') or {};identifier=request.get('id')
    if identifier is None: continue
    try:
        if method=='initialize': result={'userAgent':'fixture-native/0.162.0-alpha.17.2'}
        elif method=='config/read': result={'config':configuration(),'layers':[],'origins':{}}
        elif method=='model/list':
            result={'data':[{'id':'gpt-fixture','model':'gpt-fixture','displayName':'OpenAI fixture',
                'description':'Native account model','hidden':False,'isDefault':True,
                'defaultReasoningEffort':'max','supportedReasoningEfforts':[{'reasoningEffort':'max','description':'Maximum'}],
                'inputModalities':['text','image'],'supportsPersonality':True}], 'nextCursor':None}
        elif method=='thread/start':
            thread=setup(params);threads[thread['id']]=thread;result=view(thread)
        elif method=='thread/resume':
            prior=threads[params['threadId']]
            if not prior['loaded']:
                threads[params['threadId']]=setup(params,params['threadId'])
            result=view(threads[params['threadId']])
        elif method=='thread/unsubscribe':
            threads[params['threadId']]['loaded']=False;result={'status':'unsubscribed'}
        elif method=='thread/read': result={'thread':view(threads[params['threadId']])['thread']}
        elif method=='turn/start':
            thread=threads[params['threadId']]
            model=(params.get('collaborationMode') or {}).get('settings',{}).get('model') or params.get('model') or thread['model']
            if model=='local-code' and (home/'require-local-metadata').exists():
                overrides=[value.split('=',1)[1] for value in sys.argv if value.startswith('model_catalog_json=')]
                if not overrides: raise RuntimeError('The native engine has no qualified local model metadata')
                catalog=json.loads(pathlib.Path(json.loads(overrides[0])).read_text())['models']
                if not any(row['slug']=='local-code' and row.get('supports_search_tool') is True for row in catalog):
                    raise RuntimeError('The qualified discovery capability was not retained')
            provider=thread['providers'][thread['provider']]
            origin=urlsplit(provider['base_url'])
            if provider.get('auth'):
                import subprocess
                auth=provider['auth'];credential=subprocess.check_output([auth['command'],*auth['args']],text=True,timeout=5).strip()
            else: credential=os.environ[provider['env_key']]
            connection=http.client.HTTPConnection(origin.hostname,origin.port,timeout=4)
            try:
                connection.request('POST',origin.path.rstrip('/')+'/responses',
                    json.dumps({'model':model,'input':'Summarize the synthetic fixture.','max_output_tokens':8}),
                    {'Content-Type':'application/json','Authorization':'Bearer '+credential})
                response=connection.getresponse();body=json.loads(response.read())
                if response.status!=200: raise RuntimeError('Provider status '+str(response.status))
            finally: connection.close()
            thread['model']=model;turn='turn-'+str(uuid.uuid4())
            emit({'id':identifier,'result':{'turn':{'id':turn,'status':'inProgress','items':[]}}})
            emit({'method':'turn/started','params':{'threadId':thread['id'],'turn':{'id':turn,'status':'inProgress'}}})
            emit({'method':'item/completed','params':{'threadId':thread['id'],'turnId':turn,
                'item':{'type':'agentMessage','id':'answer','text':body['output'][0]['content'][0]['text']}}})
            emit({'method':'turn/completed','params':{'threadId':thread['id'],'turn':{'id':turn,'status':'completed','items':[]}}})
            continue
        elif method=='account/read': result={'account':{'type':'chatgpt','email':'fixture@example.invalid'},'requiresOpenaiAuth':True}
        elif method=='config/batchWrite':
            import re
            if params.get('expectedVersion') not in (None,'fixture-version'): raise RuntimeError('Config version conflict')
            path=home/'config.toml';text=path.read_text()
            for edit in params['edits']:
                key=edit['keyPath']
                if key not in ('model','model_reasoning_effort'): raise RuntimeError('Unsupported fixture key')
                text=re.sub(r'^'+re.escape(key)+r'\s*=.*$',key+' = '+json.dumps(edit['value']),text,flags=re.MULTILINE)
            path.write_text(text)
            result={'status':'ok','filePath':str(path),'version':'fixture-version','overriddenMetadata':None}
        else: raise RuntimeError('Unhandled fixture method '+method)
        emit({'id':identifier,'result':result})
    except Exception as error:
        emit({'id':identifier,'error':{'code':-32000,'message':str(error)}})
'''

LAUNCHCTL = r'''#!PYTHON
import json,pathlib,sys
path=pathlib.Path(__file__).with_suffix('.state.json')
state=json.loads(path.read_text()) if path.exists() else {}
operation,name,*values=sys.argv[1:]
if operation=='getenv':
    if name not in state: sys.exit(1)
    print(state[name]);sys.exit(0)
if operation=='setenv': state[name]=values[0]
elif operation=='unsetenv': state.pop(name,None)
else: sys.exit(2)
path.write_text(json.dumps(state))
'''


class CodexAppBridgeWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = lifetime.ConnectedClientLifetimeWorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.home = self.root / 'normal-codex'
        self.home.mkdir(mode=0o700)
        (self.home / 'models_cache.json').write_text(json.dumps({'models': [
            {'slug': 'gpt-fixture', 'display_name': 'OpenAI fixture', 'supports_search_tool': True,
             'visibility': 'list', 'supported_in_api': True, 'base_instructions': 'Native fixture instructions'}]}))
        self.native = self.root / 'native-codex'
        self.native.write_text(NATIVE.replace('#!PYTHON', '#!' + sys.executable, 1))
        self.native.chmod(0o700)
        self.fixture.executable.write_text(self.fixture.executable.read_text().replace('0.147.0', '0.162.0-alpha.17.2'))

    @contextmanager
    def connected(self):
        with self.fixture.configured() as traffic:
            configuration = json.loads(self.fixture.path.read_text())
            configuration['model_routes']['local-code'].update(model_revision='fixture-weights-v1',
                capabilities=['responses', 'text', 'streaming', 'tools', 'codex_http_0160', 'codex_llama_v1'])
            configuration['route_admission'] = {'mode': 'local_only', 'local_routes': {'local-code': {
                'qualification_id': 'fixture-q1', 'model_revision': 'fixture-weights-v1',
                'max_request_bytes': 2097152, 'max_output_tokens': 8, 'task_types': ['summarization', 'interactive']}}}
            self.fixture.path.write_text(json.dumps(configuration))
            cloud = configuration['upstreams']['openai']['base_url']
            self.original = ('# My ChatGPT setup\nmodel = "gpt-fixture"\nmodel_provider = "openai"\n'
                'model_reasoning_effort = "max"\napprovals_reviewer = "auto_review"\n'
                '[features]\nplugins = true\n[plugins.fixture]\nenabled = true\n'
                '[mcp_servers.fixture]\ncommand = "fixture-mcp"\n[model_providers.openai]\n'
                'name = "OpenAI"\nwire_api = "responses"\nbase_url = ' + json.dumps(cloud + '/v1')
                + '\nenv_key = "DESTINATION_CLOUD_KEY"\n')
            (self.home / 'config.toml').write_text(self.original)
            (self.home / 'auth.json').write_text('PRIVATE_SYNTHETIC_CHATGPT_AUTHENTICATION')
            self.fixture.command('connect')
            self.manifest = self.home / 'hormuz-app-bridge-v1.json'
            self.manifest.write_text(json.dumps({'schema_id': 'hormuz.codex-app-bridge', 'schema_version': 1,
                'enabled': True, 'client_home': str(self.home), 'control_home': str(self.fixture.home),
                'native_executable': str(self.native), 'native_sha256': hashlib.sha256(self.native.read_bytes()).hexdigest(),
                'native_version': '0.162.0-alpha.17.2', 'catalog_path': str(self.root / 'combined-models.json')}))
            self.manifest.chmod(0o600)
            environment = {**self.fixture.environment, 'CODEX_HOME': str(self.home)}
            errors = (self.root / 'bridge.stderr').open('w+')
            process = subprocess.Popen([sys.executable, '-m', 'hormuz.codex_app_bridge',
                '--manifest', str(self.manifest), 'app-server', '--stdio'],
                env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors, start_new_session=True)
            reader = selectors.DefaultSelector()
            reader.register(process.stdout, selectors.EVENT_READ)
            buffer = bytearray()
            sequence = 0

            def receive(deadline):
                nonlocal buffer
                while time.monotonic() < deadline:
                    if b'\n' in buffer:
                        line, _, remainder = buffer.partition(b'\n')
                        buffer = bytearray(remainder)
                        if line:
                            return json.loads(line)
                    elif reader.select(min(1, max(0, deadline - time.monotonic()))):
                        data = os.read(process.stdout.fileno(), 65536)
                        if not data:
                            errors.seek(0)
                            self.fail('Bridge exited: ' + errors.read())
                        buffer.extend(data)
                self.fail('Bounded bridge response deadline reached')

            def rpc(method, params=None):
                nonlocal sequence
                sequence += 1
                process.stdin.write((json.dumps({'id': sequence, 'method': method, 'params': params or {}}) + '\n').encode())
                process.stdin.flush()
                deadline = time.monotonic() + 8
                while True:
                    message = receive(deadline)
                    if message.get('id') == sequence:
                        self.assertNotIn('error', message, message)
                        return message['result']

            def turn(thread_id, model):
                rpc('turn/start', {'threadId': thread_id, 'model': model,
                    'input': [{'type': 'text', 'text': 'Summarize this fixture.'}]})
                answers = []
                deadline = time.monotonic() + 8
                while True:
                    message = receive(deadline)
                    if message.get('method') == 'item/completed':
                        answers.append(message['params']['item'].get('text'))
                    if message.get('method') == 'turn/completed':
                        self.assertEqual(message['params']['turn']['status'], 'completed')
                        return answers

            try:
                rpc('initialize', {'clientInfo': {'name': 'fixture_app', 'version': '1'},
                    'capabilities': {'experimentalApi': True}})
                yield rpc, turn, traffic
            finally:
                reader.close()
                process.stdin.close()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=5)
                process.stdout.close()
                errors.close()

    def test_native_picker_lists_local_and_openai_models_without_replacing_the_account_default(self):
        with self.connected() as (rpc, _, traffic):
            catalog = rpc('model/list', {'includeHidden': False, 'limit': 100})
            self.assertEqual([model['model'] for model in catalog['data']], ['gpt-fixture', 'local-code'])
            self.assertEqual([model['isDefault'] for model in catalog['data']], [True, False])
            self.assertFalse(any(model['hidden'] for model in catalog['data']))
            self.assertEqual(catalog['data'][0]['supportedReasoningEfforts'],
                             [{'reasoningEffort': 'max', 'description': 'Maximum'}])
            self.assertEqual((self.home / 'config.toml').read_text(), self.original)
            self.assertEqual((self.home / 'auth.json').read_text(), 'PRIVATE_SYNTHETIC_CHATGPT_AUTHENTICATION')
            self.assertEqual(traffic, ([], []))

    def test_selecting_local_and_openai_models_rebinds_the_idle_thread_to_the_correct_provider(self):
        with self.connected() as (rpc, turn, traffic):
            rpc('model/list', {'includeHidden': False, 'limit': 100})
            started = rpc('thread/start', {'model': 'gpt-fixture', 'sandbox': 'read-only',
                'approvalsReviewer': 'auto_review', 'config': {'model_reasoning_effort': 'max'}})
            thread_id = started['thread']['id']
            self.assertEqual(turn(thread_id, 'gpt-fixture'), ['ORIGINAL_DESTINATION'])
            self.assertEqual(turn(thread_id, 'local-code'), ['LOCAL_DESTINATION'])
            self.assertEqual(turn(thread_id, 'gpt-fixture'), ['ORIGINAL_DESTINATION'])
            local, cloud = traffic
            self.assertEqual([request['body']['model'] for request in local], ['fixture-local-model'])
            self.assertEqual([request['body']['model'] for request in cloud], ['gpt-fixture', 'gpt-fixture'])
            self.assertEqual((self.home / 'config.toml').read_text(), self.original)

    def test_picker_configuration_exposes_the_managed_model_view_without_editing_user_settings(self):
        with self.connected() as (rpc, _, traffic):
            effective = rpc('config/read', {'cwd': str(self.root), 'includeLayers': True})
            self.assertEqual(effective['config']['model'], 'gpt-fixture')
            self.assertEqual(effective['config']['model_provider'], 'openai')
            view = Path(effective['config']['model_catalog_json'])
            self.assertTrue(view.is_file())
            catalog = rpc('model/list', {'includeHidden': False, 'limit': 100})
            self.assertEqual([model['model'] for model in json.loads(view.read_text())['data']],
                             ['gpt-fixture', 'local-code'])
            self.assertEqual((self.home / 'config.toml').read_text(), self.original)
            self.assertEqual(traffic, ([], []))

    def test_saving_a_local_model_and_effort_keeps_the_chatgpt_defaults_and_login_unchanged(self):
        with self.connected() as (rpc, _, traffic):
            rpc('model/list', {'includeHidden': False, 'limit': 100})
            saved = rpc('config/batchWrite', {'edits': [
                {'keyPath': 'model', 'value': 'local-code', 'mergeStrategy': 'upsert'},
                {'keyPath': 'model_reasoning_effort', 'value': 'high', 'mergeStrategy': 'upsert'}],
                'filePath': None, 'expectedVersion': 'fixture-version', 'reloadUserConfig': True})
            self.assertEqual(saved['status'], 'ok')
            self.assertEqual((self.home / 'config.toml').read_text(), self.original)
            effective = rpc('config/read', {'includeLayers': False})
            self.assertEqual((effective['config']['model'], effective['config']['model_reasoning_effort']), ('local-code', 'high'))
            account = rpc('account/read', {'refreshToken': False})
            self.assertEqual(account['account']['type'], 'chatgpt')
            self.assertEqual((self.home / 'auth.json').read_text(), 'PRIVATE_SYNTHETIC_CHATGPT_AUTHENTICATION')
            self.assertEqual(traffic, ([], []))

    def test_off_disables_the_running_bridge_and_returns_to_chatgpt_without_reinstalling_codex(self):
        with self.connected() as (rpc, turn, traffic):
            rpc('model/list', {'includeHidden': False, 'limit': 100})
            local = rpc('thread/start', {'model': 'local-code', 'sandbox': 'read-only'})
            self.assertEqual(turn(local['thread']['id'], 'local-code'), ['LOCAL_DESTINATION'])
            disabled = subprocess.run([sys.executable, '-m', 'hormuz', 'off'],
                env={**self.fixture.environment, 'CODEX_HOME': str(self.home)},
                capture_output=True, text=True, timeout=18)
            self.assertEqual(disabled.returncode, 0, disabled.stderr)
            receipt = json.loads(disabled.stdout)
            self.assertEqual((receipt['phase'], receipt['router']), ('disconnected', 'stopped'))
            self.assertFalse(json.loads(self.manifest.read_text())['enabled'])
            self.assertEqual([model['model'] for model in rpc('model/list')['data']], ['gpt-fixture'])
            effective = rpc('config/read', {'includeLayers': False})
            self.assertEqual(effective['config']['model'], 'gpt-fixture')
            self.assertNotIn('model_catalog_json', effective['config'])
            cloud = rpc('thread/start', {'model': 'gpt-fixture', 'sandbox': 'read-only'})
            self.assertEqual(turn(cloud['thread']['id'], 'gpt-fixture'), ['ORIGINAL_DESTINATION'])
            self.assertEqual((self.home / 'config.toml').read_text(), self.original)
            self.assertEqual([len(requests) for requests in traffic], [1, 1])

    def test_install_preview_and_one_command_disable_preserve_the_native_app_and_previous_launch_hook(self):
        with self.connected() as (_, _, traffic):
            self.manifest.unlink()
            launchctl = self.root / 'launchctl-fixture'
            launchctl.write_text(LAUNCHCTL.replace('#!PYTHON', '#!' + sys.executable, 1))
            launchctl.chmod(0o700)
            subprocess.run([str(launchctl), 'setenv', 'CODEX_CLI_PATH', '/fixture/previous-codex'], check=True, timeout=3)
            digest = hashlib.sha256(self.native.read_bytes()).hexdigest()
            directory = self.root / 'installed-app-bridge'
            command = [sys.executable, '-m', 'hormuz', 'client', 'bridge', 'codex', '--client-home', str(self.home),
                '--router-home', str(self.fixture.home), '--directory', str(directory),
                '--executable', str(self.native), '--launchctl', str(launchctl), '--isolated-test']
            environment = {**self.fixture.environment, 'CODEX_HOME': str(self.home)}
            preview = subprocess.run([*command, '--dry-run'], env=environment, capture_output=True, text=True, timeout=12)
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertEqual(json.loads(preview.stdout)['phase'], 'preview')
            self.assertFalse(directory.exists())
            self.assertEqual(subprocess.check_output([str(launchctl), 'getenv', 'CODEX_CLI_PATH'], text=True, timeout=3).strip(),
                             '/fixture/previous-codex')
            installed = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=12)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            receipt = json.loads(installed.stdout)
            self.assertEqual(receipt['phase'], 'enabled')
            self.assertEqual((receipt['schema_id'], receipt['schema_version']), ('hormuz.codex-app-bridge-install', 1))
            self.assertEqual(subprocess.check_output([str(launchctl), 'getenv', 'CODEX_CLI_PATH'], text=True, timeout=3).strip(),
                             receipt['bridge_executable'])
            self.assertEqual(subprocess.check_output([receipt['bridge_executable'], '--version'], text=True, timeout=5).strip(),
                             'codex-cli 0.162.0-alpha.17.2')
            disabled = subprocess.run([sys.executable, '-m', 'hormuz', 'off'], env=environment,
                capture_output=True, text=True, timeout=18)
            self.assertEqual(disabled.returncode, 0, disabled.stderr)
            self.assertEqual(subprocess.check_output([str(launchctl), 'getenv', 'CODEX_CLI_PATH'], text=True, timeout=3).strip(),
                             '/fixture/previous-codex')
            self.assertEqual(hashlib.sha256(self.native.read_bytes()).hexdigest(), digest)
            self.assertEqual((self.home / 'config.toml').read_text(), self.original)
            self.assertEqual(traffic, ([], []))

    def test_native_generation_uses_qualified_local_metadata_alongside_unchanged_cloud_metadata(self):
        with self.connected() as (rpc, turn, traffic):
            (self.home / 'require-local-metadata').write_text('required')
            started = rpc('thread/start', {'model': 'local-code', 'sandbox': 'read-only'})
            self.assertEqual(turn(started['thread']['id'], 'local-code'), ['LOCAL_DESTINATION'])
            self.assertEqual((self.home / 'config.toml').read_text(), self.original)
            self.assertEqual([len(requests) for requests in traffic], [1, 0])
