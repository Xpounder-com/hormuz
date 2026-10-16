"""Opt-in native Codex 0.160.0/local-model workflow; existing assets, no downloads."""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import tomllib


def _read(path):
    with path.open('rb') as stream:
        value = stream.read(1024 * 1024 + 1)
    if len(value) > 1024 * 1024:
        raise RuntimeError('Native verification output exceeded its bound')
    return value


def _stop(process):
    if process.poll() is not None:
        return
    for action in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
        os.killpg(process.pid, action)
        try:
            process.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue
    raise RuntimeError('Native child did not stop')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex', required=True, type=Path)
    parser.add_argument('--runtime-archive', required=True, type=Path)
    parser.add_argument('--llama-executable', required=True, type=Path)
    parser.add_argument('--model-file', required=True, type=Path)
    parser.add_argument('--python', type=Path, default=Path(sys.executable))
    parser.add_argument('--site', type=Path, help='Optional installed-wheel import root')
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix='hormuz-codex0160-native-')).resolve()
    root.chmod(0o700)
    home, client_home, project = root / 'home', root / 'home/.codex', root / 'project'
    for directory in (home, client_home, project):
        directory.mkdir(mode=0o700)
    original = 'personality = "none"\nmodel_reasoning_effort = "xhigh"\n'
    (client_home / 'config.toml').write_text(original)
    (project / 'verify_fixture.py').write_text(
        'def add(a, b):\n    return a + b\n\nassert add(2, 5) == 7\nprint("ROUTING_OK")\n')
    environment = {'HOME': str(home), 'CODEX_HOME': str(client_home),
                   'PATH': '/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin',
                   'TMPDIR': str(root), 'LANG': 'en_US.UTF-8', 'OTEL_SDK_DISABLED': 'true'}
    if args.site:
        environment['PYTHONPATH'] = str(args.site.resolve())
    executable = args.codex.expanduser().absolute()
    version = subprocess.run([str(executable), '--version'], env=environment,
                             capture_output=True, text=True, check=True, timeout=5).stdout.strip()
    if version != 'codex-cli 0.160.0':
        raise RuntimeError('Native verification requires the existing Codex 0.160.0 executable')
    executable_digest = hashlib.sha256(executable.resolve().read_bytes()).hexdigest()
    config = root / 'gateway.json'
    command = [str(args.python.expanduser().absolute()), '-m', 'hormuz', '--config', str(config)]
    receipts = []
    result = {'passed': False, 'codex_version': version, 'codex_executable': str(executable),
              'codex_entrypoint_sha256': executable_digest, 'evidence_directory': str(root),
              'native_episodes': 1, 'model_downloads': 0, 'credentials': 'isolated local command helper',
              'saved_reasoning_effort': 'xhigh', 'launch_reasoning_effort': 'none',
              'scope': 'supplied-script single-agent HTTP; not autonomous coding quality'}

    def cli(*arguments, timeout=90):
        output = subprocess.run([*command, *arguments], cwd=root, env=environment,
                                capture_output=True, text=True, timeout=timeout)
        if output.returncode:
            raise RuntimeError(output.stderr.strip() or 'Public CLI operation failed')
        value = json.loads(output.stdout)
        receipts.append({'operation': arguments[:2], 'receipt': value})
        return value

    generation = None
    ports = []
    print(json.dumps({'stage': 'native_setup', 'evidence_directory': str(root)}), flush=True)
    try:
        setup = ['client', 'setup', 'codex', '--client-home', str(client_home),
                 '--executable', str(executable), '--runtime-archive', str(args.runtime_archive.resolve()),
                 '--llama-executable', str(args.llama_executable.resolve()), '--model-file', str(args.model_file.resolve())]
        preview = cli(*setup, '--dry-run')
        if preview['connection']['client_version'] != '0.160.0' or config.exists():
            raise RuntimeError('Native setup preview changed configuration or selected the wrong client')
        connected = cli(*setup)
        owner = connected['connection']
        generation = owner['generation']
        if (owner['phase'], owner['router'], owner['configuration'], owner['local_runtime']['state']) != ('connected', 'ready', 'installed', 'ready'):
            raise RuntimeError('Managed local setup is not ready')
        settings = json.loads(config.read_text())
        if settings['route_admission']['mode'] != 'local_only' or any(u['locality'] != 'local' for u in settings['upstreams'].values()):
            raise RuntimeError('Native verification requires exclusively local routing')
        ports = [connected['configuration']['listen_port'], connected['configuration']['runtime_port']]
        user_config = tomllib.loads((client_home / 'config.toml').read_text())
        provider = user_config['model_providers'][user_config['model_provider']]
        auth = provider['auth']
        # Use the public generated command exactly as Codex does; never read native auth caches.
        token = subprocess.run([auth['command'], *auth['args']], env=environment, cwd=root,
                               capture_output=True, text=True, check=True, timeout=6).stdout.strip()
        if not token or token in (client_home / 'config.toml').read_text():
            raise RuntimeError('Managed caller credential was missing or exposed in user configuration')
        prompt = ('Review this Python function by running python3 verify_fixture.py with exec_command. '
                  'Do not change files. Report the script output. /no_think')
        native_arguments = [str(executable), 'exec', '--skip-git-repo-check', '--ephemeral', '--json',
                            '--sandbox', 'read-only', '-c', 'agents.enabled=false', '--disable', 'multi_agent_v2',
                            '-c', 'model_reasoning_summary="none"', '-c', 'model_reasoning_effort="none"',
                            '-c', 'analytics.enabled=false', '-C', str(project), prompt]
        print(json.dumps({'stage': 'native_task_running', 'codex_version': version}), flush=True)
        with (root / 'native.jsonl').open('w') as stdout, (root / 'native.stderr').open('w') as stderr:
            native = subprocess.Popen(native_arguments, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                      cwd=project, env=environment, start_new_session=True)
            try:
                returncode = native.wait(timeout=120)
            finally:
                _stop(native)
        events = [json.loads(line) for line in _read(root / 'native.jsonl').splitlines() if line]
        commands = [event['item'] for event in events if event.get('type') == 'item.completed'
                    and event.get('item', {}).get('type') == 'command_execution']
        completed = [event for event in events if event.get('type') == 'turn.completed']
        result.update(native_exit=returncode, completed_turns=len(completed), commands=commands)
        catalog_errors = [event for event in events if event.get('item', {}).get('type') == 'error'
                          and 'Model metadata' in event['item'].get('message', '')]
        result['catalog_fallback_errors'] = len(catalog_errors)
        if returncode or not completed or not any(item.get('exit_code') == 0 and 'ROUTING_OK' in item.get('aggregated_output', '') for item in commands):
            raise RuntimeError('Native Codex did not complete the supplied script through its tool loop')
        if catalog_errors or b'failed to decode models response' in _read(root / 'native.stderr'):
            raise RuntimeError('Codex could not load the qualified model catalog')
        connection = http.client.HTTPConnection('127.0.0.1', ports[0], timeout=3)
        try:
            connection.request('GET', '/v1/work/state', headers={'Authorization': 'Bearer ' + token})
            response = connection.getresponse()
            raw = response.read(1024 * 1024 + 1)
            if response.status != 200 or len(raw) > 1024 * 1024:
                raise RuntimeError('Public work-state inspection failed')
            state = json.loads(raw)
        finally:
            connection.close()
        (root / 'work-state.json').write_text(json.dumps(state, indent=2) + '\n')
        result['work_state'] = state
        attempts = [attempt for work in state['works'] for attempt in work['attempts']]
        if (len(state['works']) != 1 or not 2 <= len(attempts) <= 4
                or any(attempt['state'] != 'succeeded' or attempt['model'] != connected['configuration']['model'] for attempt in attempts)
                or not any(attempt['reason'] == 'session_affinity' for attempt in attempts)
                or any(state['totals'][key] for key in ('denied', 'unknown_attempts', 'retry_attempts', 'failover_attempts'))):
            raise RuntimeError('Public attempts did not preserve successful local conversation affinity')
        result['status'] = cli('client', 'status', 'codex', '--client-home', str(client_home))
        if (result['status']['phase'], result['status']['router'], result['status']['configuration']) != ('connected', 'ready', 'installed'):
            raise RuntimeError('Native task changed or lost the owned connection')
        result['passed'] = True
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        result['failure'] = str(error)
    finally:
        try:
            disconnected = cli('client', 'disconnect', 'codex', '--client-home', str(client_home),
                               *(['--generation', generation] if generation else []), timeout=20)
            result['disconnect'] = disconnected
            restored = tomllib.loads((client_home / 'config.toml').read_text())
            result['settings_restored'] = (all(key not in restored for key in ('model', 'model_provider', 'web_search'))
                and restored.get('personality') == 'none' and restored.get('model_reasoning_effort') == 'xhigh'
                and not any(key.startswith('hormuz_') for key in restored.get('model_providers', {})))
            if disconnected['phase'] != 'disconnected' or disconnected['router'] != 'stopped' or disconnected.get('retained_settings') or not result['settings_restored']:
                result['passed'] = False
            for port in ports:
                with socket.socket() as probe:
                    probe.settimeout(1)
                    if probe.connect_ex(('127.0.0.1', port)) == 0:
                        result['passed'] = False
                        result['failure'] = 'An owned listener remained active after disconnect'
        except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
            result.update(passed=False, cleanup_failure=str(error))
        result['receipts'] = receipts
        path = root / 'verification.json'
        path.write_text(json.dumps(result, indent=2) + '\n')
        path.chmod(0o600)
        print(json.dumps({'passed': result['passed'], 'evidence': str(path), 'failure': result.get('failure'),
                          'settings_restored': result.get('settings_restored')}, indent=2))
    return 0 if result['passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
