"""Opt-in app-engine verification with plugins, MCP and max reasoning enabled."""
import argparse
from collections import Counter
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import selectors
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tomllib

MCP = '''import json,sys
for line in sys.stdin:
    request=json.loads(line)
    if 'id' not in request: continue
    method=request.get('method')
    if method=='initialize': result={'protocolVersion':request['params']['protocolVersion'],'capabilities':{'tools':{}},'serverInfo':{'name':'routing_fixture','version':'1'}}
    elif method=='tools/list': result={'tools':[{'name':'probe','description':'Read-only local routing probe. Call it to obtain the device-local verification result.','inputSchema':{'type':'object','properties':{},'additionalProperties':False}}]}
    elif method=='tools/call': result={'content':[{'type':'text','text':'MCP_ROUTING_OK'}],'isError':False}
    else: result={}
    print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}),flush=True)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('codex', 'runtime-archive', 'llama-executable', 'model-file', 'qualification'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--client-home', type=Path)
    parser.add_argument('--gateway-config', type=Path)
    parser.add_argument('--project', type=Path, help='Use an existing project without writing verification files into it')
    parser.add_argument('--leave-connected', action='store_true')
    parser.add_argument('--normal-mcp', action='store_true', help='Verify the existing node_repl MCP instead of a private fixture')
    parser.add_argument('--discover-first', action='store_true', help='Discover the MCP tool in one turn, then invoke it in a second turn')
    parser.add_argument('--capture-wire', action='store_true', help='Private synthetic request capture; isolated homes only')
    parser.add_argument('--capture-shapes', action='store_true', help='Record only content-free request shapes and response statuses')
    parser.add_argument('--task', choices=('script', 'mcp', 'both', 'approval'), default='both')
    args = parser.parse_args()
    if args.capture_wire and args.client_home:
        parser.error('Wire capture is limited to synthetic isolated homes')
    os.umask(0o077)
    root = Path(tempfile.mkdtemp(prefix='hormuz-codex-app-native-')).resolve()
    fixture_project = root / 'project'
    fixture_project.mkdir(mode=0o700)
    fixture = fixture_project / 'verify_fixture.py'
    fixture.write_text('assert 2 + 5 == 7\nprint("ROUTING_OK")\n')
    project = args.project.expanduser().resolve() if args.project else fixture_project
    if not project.is_dir():
        parser.error('Selected project must already exist')
    script_command = 'python3 ' + shlex.quote(str(fixture)) if args.project else 'python3 verify_fixture.py'
    home = args.client_home.expanduser().resolve() if args.client_home else root / 'home/.codex'
    if not args.client_home:
        home.mkdir(mode=0o700, parents=True)
        mcp = root / 'fixture_mcp.py'
        mcp.write_text(MCP)
        (home / 'config.toml').write_text('model_reasoning_effort = "max"\napprovals_reviewer = "auto_review"\n'
            '[features]\nplugins = true\napps = true\nmulti_agent = true\n'
            '[mcp_servers.routing_fixture]\ncommand = ' + json.dumps(sys.executable) +
            '\nargs = [' + json.dumps(str(mcp)) + ']\nenabled = true\n')
    original = (home / 'config.toml').read_bytes()
    (root / 'settings-before.toml').write_bytes(original)
    config = args.gateway_config.expanduser().resolve() if args.gateway_config else root / 'gateway.json'
    env = {'HOME': str(home.parent), 'CODEX_HOME': str(home),
           'PATH': '/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': 'en_US.UTF-8',
           'TMPDIR': str(root), 'OTEL_SDK_DISABLED': 'true'}
    cli = [sys.executable, '-m', 'hormuz', '--config', str(config)]
    receipts = []
    def command(*arguments, timeout=180):
        result = subprocess.run([*cli, *arguments], env=env, capture_output=True, text=True, timeout=timeout)
        value = json.loads(result.stdout if result.returncode == 0 else result.stderr)
        receipts.append({'operation': list(arguments[:2]), 'receipt': value})
        if result.returncode:
            raise RuntimeError(json.dumps(value))
        return value
    proof = {'passed': False, 'scope': 'bundled app-server protocol; native desktop UI is separate', 'task': args.task,
             'client_home': str(home), 'normal_client_home': bool(args.client_home),
             'codex_executable': str(args.codex), 'codex_sha256': hashlib.sha256(args.codex.read_bytes()).hexdigest(),
             'evidence_directory': str(root), 'model_downloads': 0, 'feature_disable_overrides': []}
    native = None
    proxy = None
    proxy_worker = None
    generation = None
    selector = selectors.DefaultSelector()
    buffer = bytearray()
    messages = []
    def send(value):
        native.stdin.write((json.dumps(value) + '\n').encode())
        native.stdin.flush()
    def receive(deadline):
        nonlocal buffer
        while time.monotonic() < deadline:
            if b'\n' in buffer:
                line, _, rest = buffer.partition(b'\n')
                buffer = bytearray(rest)
                if line:
                    value = json.loads(line)
                    messages.append(value)
                    if len(messages) > 4096:
                        raise RuntimeError('Native event count exceeded its bound')
                    return value
                continue
            if not selector.select(min(10, max(0, deadline - time.monotonic()))):
                continue
            data = os.read(native.stdout.fileno(), 65536)
            if not data:
                raise RuntimeError('Native app-server closed before completing the turn')
            buffer.extend(data)
            if len(buffer) > 4 * 1024 * 1024:
                raise RuntimeError('Native frame exceeded its bound')
        raise TimeoutError('Native verification deadline exceeded')
    def result(identifier, seconds):
        deadline = time.monotonic() + seconds
        while True:
            value = receive(deadline)
            if value.get('id') == identifier:
                if 'error' in value:
                    raise RuntimeError(json.dumps(value['error']))
                return value['result']
    def work_state(settings):
        auth = settings['model_providers'][settings['model_provider']]['auth']
        token = subprocess.run([auth['command'], *auth['args']], env=env, capture_output=True,
                               text=True, check=True, timeout=6).stdout.strip()
        gateway = json.loads(config.read_text())
        transport = http.client.HTTPConnection('127.0.0.1', gateway['listen']['port'], timeout=4)
        try:
            transport.request('GET', '/v1/work/state', headers={'Authorization': 'Bearer ' + token})
            response = transport.getresponse()
            raw = response.read(2 * 1024 * 1024 + 1)
            assert response.status == 200 and len(raw) <= 2 * 1024 * 1024
            return json.loads(raw)
        finally:
            transport.close()
    try:
        setup = ['client', 'setup', 'codex', '--client-home', str(home), '--executable', str(args.codex),
                 '--runtime-archive', str(args.runtime_archive), '--llama-executable', str(args.llama_executable),
                 '--model-file', str(args.model_file), '--qualification', str(args.qualification)]
        command(*setup, '--dry-run')
        connection = command(*setup)
        generation = connection['connection']['generation']
        settings = tomllib.loads((home / 'config.toml').read_text())
        assert settings.get('model_reasoning_effort') == 'max'
        assert all(settings.get('features', {}).get(key, True) for key in ('plugins', 'apps', 'multi_agent'))
        proof['codex_version'] = connection['connection']['client_version']
        proof['saved_reasoning_effort'] = settings['model_reasoning_effort']
        before_state = work_state(settings)
        prior_attempts = {attempt['request_id'] for work in before_state['works'] for attempt in work['attempts']}
        native_arguments = [str(args.codex), 'app-server', '--stdio']
        if args.capture_wire or args.capture_shapes:
            gateway = json.loads(config.read_text())
            class Capture(BaseHTTPRequestHandler):
                timeout = 190
                requests = 0
                def log_message(self, *_):
                    pass
                def do_POST(self):
                    Capture.requests += 1
                    length = int(self.headers['Content-Length'])
                    if Capture.requests > 8 or length > 2 * 1024 * 1024:
                        self.send_error(413)
                        return
                    body = self.rfile.read(length)
                    if args.capture_wire:
                        (root / ('request-' + str(Capture.requests) + '.json')).write_bytes(body)
                    else:
                        value = json.loads(body)
                        shape = {'bytes': len(body), 'keys': sorted(value), 'model': value.get('model'),
                            'max_output_tokens': value.get('max_output_tokens'), 'reasoning': value.get('reasoning'),
                            'include': value.get('include'), 'tool_types': dict(Counter(tool.get('type') for tool in value.get('tools', []))),
                            'input_types': dict(Counter(item.get('type') for item in value.get('input', []) if isinstance(item, dict))),
                            'text_format': value.get('text', {}).get('format', {}).get('type'),
                            'metadata_keys': sorted(value.get('client_metadata', {}))}
                        (root / ('request-' + str(Capture.requests) + '-shape.json')).write_text(json.dumps(shape, indent=2))
                    upstream = http.client.HTTPConnection('127.0.0.1', gateway['listen']['port'], timeout=185)
                    try:
                        headers = {key: value for key, value in self.headers.items()
                                   if key.lower() not in {'host', 'connection', 'content-length', 'transfer-encoding'}}
                        upstream.request('POST', self.path, body, headers=headers)
                        reply = upstream.getresponse()
                        (root / ('response-' + str(Capture.requests) + '-status.json')).write_text(json.dumps({'status': reply.status}))
                        self.send_response(reply.status)
                        self.send_header('Content-Type', reply.getheader('Content-Type', 'application/json'))
                        self.send_header('Connection', 'close')
                        self.end_headers()
                        self.close_connection = True
                        while chunk := reply.read1(16384):
                            self.wfile.write(chunk)
                            self.wfile.flush()
                    except (OSError, http.client.HTTPException):
                        pass
                    finally:
                        upstream.close()
            proxy = HTTPServer(('127.0.0.1', 0), Capture)
            proxy_worker = threading.Thread(target=proxy.serve_forever, daemon=True)
            proxy_worker.start()
            native_arguments.extend(['-c', 'model_providers.' + settings['model_provider'] + '.base_url=' +
                                     json.dumps('http://127.0.0.1:' + str(proxy.server_port) + '/v1')])
        print(json.dumps({'stage': 'native_app_server', 'evidence_directory': str(root)}), flush=True)
        stderr = (root / 'native.stderr').open('w')
        try:
            native = subprocess.Popen(native_arguments, env=env, stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=stderr, start_new_session=True)
            selector.register(native.stdout, selectors.EVENT_READ)
            send({'id': 1, 'method': 'initialize', 'params': {'clientInfo': {'name': 'hormuz_app_verification', 'version': '1'},
                                                                          'capabilities': {'experimentalApi': True}}})
            proof['initialize'] = result(1, 15)
            send({'method': 'initialized', 'params': {}})
            send({'id': 2, 'method': 'thread/start', 'params': {'model': settings['model'],
                  'modelProvider': settings['model_provider'], 'cwd': str(project), 'ephemeral': True, 'sandbox': 'read-only'}})
            thread = result(2, 45)
            proof['thread_configuration'] = {key: thread.get(key) for key in ('model', 'modelProvider', 'reasoningEffort')}
            target = 'node_repl js' if args.normal_mcp else 'routing_fixture probe'
            instruction = ('Use tool_search to find ' + target + '. Then use the discovered js tool to run '
                           'console.log("MCP_ROUTING_OK"). ' if args.normal_mcp else
                           'Use tool_search to find routing_fixture probe, then call its probe tool once. ')
            prompt = 'Review this Python function using the supplied verification script. ' + instruction + \
                     'Then use exec_command to run ' + script_command + '. Do not modify files. Report both tool outputs. Reason briefly.'
            if args.task == 'script':
                prompt = 'Review this Python function by running ' + script_command + ' with exec_command. Do not change files. Report the script output. Reason briefly.'
            elif args.task == 'mcp':
                prompt = ('Review this local tool integration. Call the tool_search function directly with '
                          '{"query": ' + json.dumps(target) + ', "limit": 1}. '
                          + ('Invoke the discovered js function with {"code":"console.log(\\"MCP_ROUTING_OK\\")"}. '
                             if args.normal_mcp else 'Invoke the discovered probe function with {}. ')
                          + 'Wait for the discovery result before invoking the MCP function. '
                          + 'Use direct tool calls; do not run shell commands. Report its result. Reason briefly.')
            elif args.task == 'approval':
                prompt = ('Review this local command approval integration. Use exec_command once with '
                    '{"cmd":"printf APPROVAL_ROUTING_OK", "sandbox_permissions":"require_escalated", '
                    '"justification":"Verify the existing automatic approval reviewer with one read-only print command."}. '
                    'Keep the existing approval settings. Report the actual command result. Reason briefly.')
            discovery_prompt = ('Discover the MCP tools for ' + target + ' using tool_search with limit 1. '
                                'Report the discovered function names. This turn only discovers tools; invoke no other tools.')
            send({'id': 3, 'method': 'turn/start', 'params': {'threadId': thread['thread']['id'],
                  'input': [{'type': 'text', 'text': discovery_prompt if args.discover_first else prompt}], 'effort': 'max', 'summary': 'auto'}})
            result(3, 15)
            deadline = time.monotonic() + 480
            completed_items = []
            discovery_done = False
            while True:
                value = receive(deadline)
                if 'id' in value and 'method' in value:
                    approval = value.get('params', {})
                    meta = approval.get('_meta', {})
                    parameters = meta.get('tool_params', {})
                    server, tool = ('node_repl', 'js') if args.normal_mcp else ('routing_fixture', 'probe')
                    exact_probe = (isinstance(parameters, dict) and
                        (parameters == {} if not args.normal_mcp else
                         set(parameters) <= {'code', 'title', 'timeout_ms'} and
                         parameters.get('code', '').strip().rstrip(';') == 'console.log("MCP_ROUTING_OK")'))
                    if (value['method'] == 'mcpServer/elicitation/request' and approval.get('mode') == 'form'
                            and approval.get('threadId') == thread['thread']['id']
                            and approval.get('serverName') == server and meta.get('codex_approval_kind') == 'mcp_tool_call'
                            and approval.get('message') == f'Allow the {server} MCP server to run tool "{tool}"?'
                            and exact_probe):
                        send({'id': value['id'], 'result': {'action': 'accept', 'content': {}}})
                        proof.setdefault('probe_approvals', []).append({'server': server, 'tool': tool})
                        continue
                    send({'id': value['id'], 'error': {'code': -32000, 'message': 'Verification does not grant new approvals'}})
                    raise RuntimeError('Native verification requested an ungranted approval or input')
                if value.get('method') == 'item/completed':
                    completed_items.append(value['params']['item'])
                    print(json.dumps({'stage': 'native_item_completed', 'type': value['params']['item']['type']}), flush=True)
                    if len(completed_items) > 24:
                        raise RuntimeError('Native tool/item count exceeded its bound')
                if value.get('method') == 'turn/completed':
                    proof['turn'] = value['params']['turn']
                    assert proof['turn']['status'] == 'completed', 'Native turn failed'
                    if args.discover_first and not discovery_done:
                        proof['discovery_turn'] = proof['turn']
                        discovery_done = True
                        namespace = 'mcp__node_repl.js' if args.normal_mcp else 'mcp__routing_fixture.probe'
                        invocation = ('Invoke the discovered MCP function ' + namespace + ' directly with ' +
                            ('{"code":"console.log(\\"MCP_ROUTING_OK\\")"}' if args.normal_mcp else '{}') +
                            '. Report its actual returned output. Reason briefly.')
                        send({'id': 4, 'method': 'turn/start', 'params': {'threadId': thread['thread']['id'],
                            'input': [{'type': 'text', 'text': invocation}], 'effort': 'max', 'summary': 'auto'}})
                        result(4, 15)
                        continue
                    break
            commands = [item for item in completed_items if item['type'] == 'commandExecution']
            mcp_calls = [item for item in completed_items if item['type'] == 'mcpToolCall']
            proof['commands'] = commands
            proof['mcp_calls'] = mcp_calls
            proof['reasoning_notifications'] = sum('reasoning' in value.get('method', '').lower() for value in messages)
            proof['reasoning_items'] = sum(item['type'] == 'reasoning' and bool(item.get('content') or item.get('summary')) for item in completed_items)
            proof['notification_counts'] = dict(Counter(value.get('method') for value in messages if 'method' in value))
            if args.task in {'script', 'both'}:
                assert any(item.get('exitCode') == 0 and 'ROUTING_OK' in item.get('aggregatedOutput', '') for item in commands), 'Supplied script did not complete'
            if args.task in {'mcp', 'both'}:
                server, tool = ('node_repl', 'js') if args.normal_mcp else ('routing_fixture', 'probe')
                assert any(item.get('server') == server and item.get('tool') == tool and item.get('status') == 'completed'
                    and item.get('error') is None and any(part.get('type') == 'text'
                        and 'MCP_ROUTING_OK' in part.get('text', '').splitlines()
                        for part in (item.get('result') or {}).get('content', []))
                    for item in mcp_calls), 'MCP round trip did not complete on the selected server'
            if args.task == 'approval':
                assert any(item.get('exitCode') == 0 and 'APPROVAL_ROUTING_OK' in item.get('aggregatedOutput', '')
                           for item in commands), 'The native automatic approval round trip did not complete'
            assert proof['reasoning_notifications'] > 0 or proof['reasoning_items'] > 0, 'No actual model reasoning was observed'
        finally:
            stderr.close()
        state = work_state(settings)
        proof['work_state'] = state
        attempts = [attempt for work in state['works'] for attempt in work['attempts'] if attempt['request_id'] not in prior_attempts]
        proof['probe_attempts'] = attempts
        assert (2 if args.task in {'script', 'approval'} else 3) <= len(attempts) <= 8 and all(attempt['state'] == 'succeeded' for attempt in attempts), \
            'Provider attempts did not settle successfully: ' + str([attempt['state'] for attempt in attempts])
        assert any(attempt['reason'] == 'session_affinity' for attempt in attempts)
        assert all(state['totals'][key] == before_state['totals'][key] for key in ('unknown_attempts', 'retry_attempts', 'failover_attempts'))
        proof['passed'] = True
    except Exception as error:
        proof['failure'] = str(error)
    finally:
        if native:
            native.stdin.close()
            try:
                native.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(native.pid, signal.SIGTERM)
                try:
                    native.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(native.pid, signal.SIGKILL)
                    native.wait(timeout=5)
            native.stdout.close()
        selector.close()
        if proxy:
            proxy.shutdown()
            proxy.server_close()
            proxy_worker.join(timeout=2)
        if generation is not None and not args.leave_connected:
            try:
                proof['disconnect'] = command('client', 'disconnect', 'codex', '--client-home', str(home),
                                               *(['--generation', generation] if generation else []), timeout=20)
                proof['settings_restored'] = tomllib.loads((home / 'config.toml').read_text()) == tomllib.loads(original.decode())
                proof['passed'] = proof['passed'] and proof['settings_restored']
            except Exception as error:
                proof.update(passed=False, cleanup_failure=str(error))
        elif generation is not None:
            proof['left_connected'] = generation
        (root / 'native-events.json').write_text(json.dumps(messages, indent=2) + '\n')
        proof['receipts'] = receipts
        (root / 'verification.json').write_text(json.dumps(proof, indent=2) + '\n')
        print(json.dumps({'passed': proof['passed'], 'evidence': str(root / 'verification.json'),
                          'failure': proof.get('failure'), 'reasoning_notifications': proof.get('reasoning_notifications')}, indent=2), flush=True)
    return 0 if proof['passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
