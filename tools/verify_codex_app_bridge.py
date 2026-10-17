"""Opt-in native app-server smoke test for combined catalogs and provider selection."""
import argparse
import http.client
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import tomllib


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--cloud-model', help='An explicitly selected existing ChatGPT account model')
    parser.add_argument('--resume-thread', help='An explicitly selected synthetic verification chat to resume locally')
    args = parser.parse_args()
    os.umask(0o077)
    root = Path(tempfile.mkdtemp(prefix='hormuz-native-app-bridge-')).resolve()
    manifest = json.loads(args.manifest.read_text())
    home = Path(manifest['client_home'])
    original = (home / 'config.toml').read_bytes()
    settings = tomllib.loads(original.decode())
    control = tomllib.loads((Path(manifest['control_home']) / 'config.toml').read_text())
    local_model = control['model']
    provider = control['model_providers'][control['model_provider']]
    proof = {'passed': False, 'scope': 'native app-server interface; visible desktop is separate',
        'evidence_directory': str(root), 'episodes': [], 'configured_plugins': len(settings.get('plugins', {})),
        'configured_mcp_servers': list(settings.get('mcp_servers', {})), 'feature_disable_overrides': []}
    process = None
    selector = selectors.DefaultSelector()
    buffer = bytearray()
    identifier = 0

    def receive(deadline):
        nonlocal buffer
        while time.monotonic() < deadline:
            if b'\n' in buffer:
                line, _, remainder = buffer.partition(b'\n')
                buffer = bytearray(remainder)
                if line:
                    return json.loads(line)
            elif selector.select(min(2, max(0, deadline - time.monotonic()))):
                data = os.read(process.stdout.fileno(), 65536)
                if not data:
                    raise RuntimeError('Native bridge closed')
                buffer.extend(data)
                if len(buffer) > 64 * 1024 * 1024:
                    raise RuntimeError('Bounded native frame limit reached')
        raise TimeoutError('Bounded native verification deadline reached')

    def rpc(method, params):
        nonlocal identifier
        identifier += 1
        process.stdin.write((json.dumps({'id': identifier, 'method': method, 'params': params}) + '\n').encode())
        process.stdin.flush()
        deadline = time.monotonic() + 50
        while True:
            message = receive(deadline)
            if message.get('id') == identifier:
                if 'error' in message:
                    raise RuntimeError(json.dumps(message['error']))
                return message['result']

    def work_state():
        authentication = provider['auth']
        credential = subprocess.run([authentication['command'], *authentication['args']],
            capture_output=True, text=True, timeout=6, check=True).stdout.strip()
        from urllib.parse import urlsplit
        origin = urlsplit(provider['base_url'])
        connection = http.client.HTTPConnection(origin.hostname, origin.port, timeout=4)
        try:
            connection.request('GET', '/v1/work/state', headers={'Authorization': 'Bearer ' + credential})
            response = connection.getresponse()
            body = response.read(2 * 1024 * 1024 + 1)
            assert response.status == 200 and len(body) <= 2 * 1024 * 1024
            return json.loads(body)
        finally:
            connection.close()

    try:
        before = work_state()
        prior = {attempt['request_id'] for work in before['works'] for attempt in work['attempts']}
        with (root / 'native.stderr').open('w') as errors:
            process = subprocess.Popen([sys.executable, '-m', 'hormuz.codex_app_bridge', '--manifest', str(args.manifest),
                'app-server', '--stdio'], env={**os.environ, 'CODEX_HOME': str(home), 'OTEL_SDK_DISABLED': 'true'},
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors, start_new_session=True)
            selector.register(process.stdout, selectors.EVENT_READ)
            rpc('initialize', {'clientInfo': {'name': 'hormuz_app_bridge_verification', 'version': '1'},
                'capabilities': {'experimentalApi': True}})
            process.stdin.write(b'{"method":"initialized","params":{}}\n')
            process.stdin.flush()
            config = rpc('config/read', {'includeLayers': False, 'cwd': str(args.project)})['config']
            proof['effective_config'] = {key: config.get(key) for key in ('model', 'model_provider', 'model_reasoning_effort', 'approvals_reviewer')}
            models = rpc('model/list', {'includeHidden': False, 'limit': 100})['data']
            proof['visible_models'] = [{key: model.get(key) for key in ('model', 'displayName', 'hidden', 'isDefault')} for model in models]
            assert local_model in {model['model'] for model in models}
            assert any(model['model'] != local_model for model in models)
            account = rpc('account/read', {'refreshToken': False})
            proof['account_type'] = (account.get('account') or {}).get('type')
            assert proof['account_type'] == 'chatgpt'
            selections = [(local_model, control['model_provider'], 'APP_LOCAL_OK: 7',
                'Calculate 2+5 and reply APP_LOCAL_OK: 7. Use no tools.')]
            if args.cloud_model:
                selections.append((args.cloud_model, settings.get('model_provider') or 'openai', 'CLOUD_ROUTING_OK',
                                   'Reply exactly CLOUD_ROUTING_OK. Use no tools.'))
            for model, expected_provider, answer, prompt in selections:
                thread = (rpc('thread/resume', {'threadId': args.resume_thread, 'model': model})
                    if args.resume_thread and model == local_model else
                    rpc('thread/start', {'model': model, 'modelProvider': None, 'cwd': str(args.project),
                        'sandbox': 'read-only', 'ephemeral': True}))
                episode = {'model': model, 'provider': thread['modelProvider'], 'reasoning_items': 0,
                    'answer_matches': False, 'tool_items': 0, 'requested_effort': 'max',
                    'thread_effort': thread.get('reasoningEffort')}
                proof['episodes'].append(episode)
                assert thread['modelProvider'] == expected_provider
                assert thread.get('reasoningEffort') == 'max'
                rpc('turn/start', {'threadId': thread['thread']['id'], 'model': model,
                    'effort': 'max', 'summary': 'auto', 'input': [{'type': 'text', 'text': prompt}]})
                deadline = time.monotonic() + 360
                count = 0
                while True:
                    message = receive(deadline)
                    count += 1
                    assert count <= 4096
                    if 'method' in message and 'id' in message:
                        raise RuntimeError('The no-tools probe requested an approval or input')
                    if message.get('method') == 'item/completed':
                        item = message['params']['item']
                        if item['type'] == 'reasoning':
                            episode['reasoning_items'] += 1
                        elif item['type'] == 'agentMessage':
                            episode['answer_matches'] |= item.get('text', '').strip() == answer
                        elif item['type'] != 'userMessage':
                            episode['tool_items'] += 1
                    if message.get('method') == 'turn/completed':
                        episode['status'] = message['params']['turn']['status']
                        episode['error'] = message['params']['turn'].get('error')
                        break
                print(json.dumps({'stage': 'native_episode', **episode}), flush=True)
                assert episode['status'] == 'completed' and episode['answer_matches'] and episode['tool_items'] == 0
                if model == local_model:
                    assert episode['reasoning_items'] > 0, 'The local reasoning probe produced no reasoning item'
        after = work_state()
        attempts = [attempt for work in after['works'] for attempt in work['attempts'] if attempt['request_id'] not in prior]
        proof['new_local_attempts'] = [{key: attempt.get(key) for key in ('request_id', 'model', 'state', 'route_fingerprint')}
                                      for attempt in attempts]
        assert len(attempts) == 1 and attempts[0]['state'] == 'succeeded' and attempts[0]['model'] == local_model
        assert all(after['totals'][key] == before['totals'][key] for key in ('unknown_attempts', 'retry_attempts', 'failover_attempts'))
        assert (home / 'config.toml').read_bytes() == original
        proof['normal_settings_unchanged'] = True
        proof['passed'] = True
    except Exception as error:
        proof['failure'] = str(error)
    finally:
        selector.close()
        if process:
            process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            process.stdout.close()
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        (root / 'verification.json').write_text(json.dumps(proof, indent=2))
        print(json.dumps({'passed': proof['passed'], 'evidence': str(root / 'verification.json'), 'failure': proof.get('failure')}))
    return 0 if proof['passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
