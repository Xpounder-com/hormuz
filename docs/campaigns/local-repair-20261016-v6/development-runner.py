"""One preregistered development episode. Explicit prepare/run phases; no retries."""
import argparse
import difflib
import hashlib
import http.client
import http.server
import json
import os
import re
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tarfile
import threading
import time
import tomllib

ROOT = Path('/private/tmp/hormuz-537-native-v6-20261016')
SOURCE = Path('/private/tmp/hormuz-537-patch-20261016')
SOURCE_PACKAGE = Path('/private/tmp/hormuz-537-v5-runtime-source.tar')
PYTHON = Path('/Users/mehrdadz/Downloads/memoWiki/hormuz/.venv/bin/python')
PINS = Path('/private/tmp/hormuz-537-v6-verified-pins.json')
PROFILE_KEYS = ('instructions', 'tools', 'tool_choice', 'temperature', 'top_p', 'reasoning', 'include',
                'text', 'parallel_tool_calls', 'store', 'service_tier', 'stream')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    path.chmod(0o600)


def read(path):
    data = path.read_bytes()
    if len(data) > 2 * 1024 * 1024:
        raise RuntimeError('Artifact exceeds two MiB')
    return json.loads(data)


def semantic_input(items):
    result = []
    for item in items:
        if (isinstance(item, dict) and item.get('type') == 'message'
                and item.get('role') in ('system', 'developer', 'user')
                and isinstance(item.get('id'), str)
                and re.fullmatch(r'msg_[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', item['id'])):
            item = {key: value for key, value in item.items() if key != 'id'}
        result.append(item)
    return result


def request_profile(value):
    profile = {key: value[key] for key in PROFILE_KEYS if key in value}
    profile.setdefault('store', False)
    profile['service_tier'] = profile.get('service_tier') or 'default'
    instructions = [{'position': index, 'message': item} for index, item in enumerate(semantic_input(value.get('input', [])))
                    if isinstance(item, dict) and item.get('role') in {'system', 'developer'}]
    if instructions:
        profile['input_instructions'] = instructions
    return profile


def stop(process):
    # Group identity comes only from this invocation's start_new_session child.
    # A completed leader does not establish that its tool descendants exited.
    for action in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, action)
        except ProcessLookupError:
            process.wait(timeout=1)
            return
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            process.poll()  # Reap the owned leader if it has exited.
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                process.wait(timeout=1)
                return
            time.sleep(0.05)
    raise RuntimeError('Owned native process group did not exit')


class Experiment:
    def __init__(self):
        self.pins = read(ROOT / 'pins.json')
        self.home = ROOT / 'home'
        self.client_home = self.home / '.codex'
        self.project = ROOT / 'project'
        self.config = ROOT / 'gateway.json'
        self.env = {'HOME': str(self.home), 'CODEX_HOME': str(self.client_home), 'TMPDIR': str(ROOT / 'tmp'),
                    'PATH': '/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': 'en_US.UTF-8',
                    'OTEL_SDK_DISABLED': 'true', 'PYTHONPATH': str(ROOT / 'installed')}
        self.base = [str(PYTHON), '-m', 'hormuz', '--config', str(self.config)]
        self.generation = None
        self.ports = []
        self.receipts = []
        self.token = None
        self.provider_id = None

    def cli(self, *args, timeout=90):
        result = subprocess.run([*self.base, *args], cwd=ROOT, env=self.env,
                                capture_output=True, text=True, timeout=timeout)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or 'Public CLI operation failed')
        value = json.loads(result.stdout)
        self.receipts.append({'operation': list(args[:2]), 'receipt': value})
        return value

    def connect(self):
        for key in ('client', 'runtime_archive', 'runtime_executable', 'model'):
            pin = self.pins[key]
            h = hashlib.sha256()
            with Path(pin['path']).open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    h.update(chunk)
            if h.hexdigest() != pin['sha256']:
                raise RuntimeError('Asset changed: ' + key)
        client = self.pins['client']['path']
        version = subprocess.check_output([client, '--version'], env=self.env, cwd=ROOT, text=True, timeout=5).strip()
        if version != 'codex-cli 0.160.0':
            raise RuntimeError('Candidate client version changed')
        result = self.cli('client', 'setup', 'codex', '--client-home', str(self.client_home),
            '--executable', client, '--runtime-archive', self.pins['runtime_archive']['path'],
            '--llama-executable', self.pins['runtime_executable']['path'], '--model-file', self.pins['model']['path'],
            '--qualification', str(ROOT / 'setup-profile.json'))
        self.generation = result['connection']['generation']
        self.ports = [result['configuration']['listen_port'], result['configuration']['runtime_port']]
        settings = read(self.config)
        if settings['route_admission']['mode'] != 'local_only' or any(u['locality'] != 'local' for u in settings['upstreams'].values()):
            raise RuntimeError('Only local destinations may be configured')
        if settings.get('ai_work', {}).get('cache_enabled', False):
            raise RuntimeError('Native probe cannot use answer cache')
        user = tomllib.loads((self.client_home / 'config.toml').read_text())
        self.provider_id = user['model_provider']
        auth = user['model_providers'][self.provider_id]['auth']
        self.token = subprocess.check_output([auth['command'], *auth['args']], env=self.env, cwd=ROOT, text=True, timeout=6).strip()
        if not self.token:
            raise RuntimeError('Owned credential helper returned no token')
        return result

    def public(self, path):
        connection = http.client.HTTPConnection('127.0.0.1', self.ports[0], timeout=3)
        try:
            connection.request('GET', '/v1/' + path, headers={'Authorization': 'Bearer ' + self.token})
            response = connection.getresponse()
            data = response.read(2 * 1024 * 1024 + 1)
            if response.status != 200 or len(data) > 2 * 1024 * 1024:
                raise RuntimeError('Public inspection failed')
            return json.loads(data)
        finally:
            connection.close()

    def launch(self, label, *, base_url=None, probe=None, timeout=90):
        args = [self.pins['client']['path'], 'exec', '--skip-git-repo-check', '--ephemeral', '--json',
                '--sandbox', 'workspace-write', '-c', 'approval_policy="never"', '-c', 'agents.enabled=false',
                '--disable', 'multi_agent_v2', '-c', 'sandbox_workspace_write.network_access=false',
                '-c', 'sandbox_workspace_write.exclude_slash_tmp=true',
                '-c', 'sandbox_workspace_write.exclude_tmpdir_env_var=true',
                '-c', 'sandbox_workspace_write.writable_roots=' + json.dumps([str(ROOT / 'tmp')]),
                '-c', 'model_reasoning_summary="none"', '-c', 'model_reasoning_effort="none"',
                '-c', 'developer_instructions=' + json.dumps((ROOT / 'developer-instructions.txt').read_text()),
                '-c', 'analytics.enabled=false', '-C', str(self.project)]
        if base_url:
            args += ['-c', 'model_providers.' + self.provider_id + '.base_url=' + json.dumps(base_url)]
        if probe:
            args += ['-c', 'model_providers.' + self.provider_id + '.http_headers={"X-Hormuz-Qualification-Probe"=' + json.dumps(probe) + '}']
        args.append((ROOT / 'prompt.txt').read_text())
        # Redact the run marker in retained argv; it never contains credentials.
        write(ROOT / (label + '-argv.json'), [arg.replace(probe, '<RUN_MARKER>') if probe else arg for arg in args])
        started = time.monotonic()
        timed_out = False
        with (ROOT / (label + '.jsonl')).open('w') as stdout, (ROOT / (label + '.stderr')).open('w') as stderr:
            process = subprocess.Popen(args, env=self.env, cwd=self.project, stdin=subprocess.DEVNULL,
                                       stdout=stdout, stderr=stderr, start_new_session=True)
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
            finally:
                cleanup_error = None
                try:
                    stop(process)
                except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                    cleanup_error = str(error)
        return {'exit_code': process.returncode, 'termination': 'timeout' if timed_out else 'completed',
                'elapsed_ms': int((time.monotonic() - started) * 1000), 'cleanup_error': cleanup_error}

    def close(self, report):
        if self.generation:
            try:
                report['disconnect'] = self.cli('client', 'disconnect', 'codex', '--client-home', str(self.client_home),
                    '--generation', self.generation, timeout=20)
                report['settings_restored'] = (self.client_home / 'config.toml').read_text() == (ROOT / 'original-config.toml').read_text()
                report['owned_listeners_closed'] = True
                for port in self.ports:
                    with socket.socket() as listener:
                        listener.settimeout(0.5)
                        if listener.connect_ex(('127.0.0.1', port)) == 0:
                            report['owned_listeners_closed'] = False
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                report['cleanup_error'] = str(error)
        report['receipts'] = self.receipts


def initialize():
    ROOT.mkdir(mode=0o700, exist_ok=False)
    for part in ('home', 'home/.codex', 'project', 'tmp', 'installed'):
        (ROOT / part).mkdir(mode=0o700)
    original = 'personality = "none"\n'
    (ROOT / 'home/.codex/config.toml').write_text(original)
    (ROOT / 'original-config.toml').write_text(original)
    write(ROOT / 'pins.json', read(PINS))
    profile = read(SOURCE / 'docs/campaigns/local-repair-20261016-v5/setup-profile.json')
    profile['qualification_id'] = 'codex0160-m5-32g-local-repair-v6-probe'
    write(ROOT / 'setup-profile.json', profile)
    corpus = read(SOURCE / 'docs/campaigns/local-repair-20261016-v6/corpus.json')
    write(ROOT / 'corpus.json', corpus)
    (ROOT / 'developer-instructions.txt').write_bytes((SOURCE / 'docs/campaigns/local-repair-20261016-v6/developer-instructions.txt').read_bytes())
    previous = read(SOURCE / 'docs/campaigns/local-repair-20261016-v5/frozen-manifest.json')
    expected = {record['path'].split('/installed/', 1)[1]: record['sha256']
                for record in previous['artifacts'] if '/installed/' in record['path']}
    with tarfile.open(SOURCE_PACKAGE) as archive:
        actual = {member.name: sha(archive.extractfile(member).read())
                  for member in archive.getmembers() if member.isfile()}
        if actual != expected:
            raise RuntimeError('Reconstructed runtime differs from the frozen v5 package')
        archive.extractall(ROOT / 'installed', filter='data')
    write(ROOT / 'runtime-equivalence.json', {'source_commit': previous['source_commit'],
        'runtime_files': len(actual), 'all_match_v5': True, 'source_archive_sha256': sha(SOURCE_PACKAGE.read_bytes())})
    first = corpus['entries'][0]
    if first['case_id'] != 'dev-clamp' or first['partition'] != 'development':
        raise RuntimeError('First case changed')
    (ROOT / 'project/solution.py').write_text(first['files']['solution.py'])
    (ROOT / 'prompt.txt').write_text(first['goal'])
    (ROOT / 'oracle.py').write_text(first['checks'])
    (ROOT / 'oracle.py').chmod(0o400)


def preflight():
    if (ROOT / 'preflight.json').exists() or (ROOT / 'first-input.json').exists():
        raise RuntimeError('Preflight already consumed; preserve it')
    experiment = Experiment()
    report = {'stage': 'zero-inference-native-capture', 'model_inferences': 0}
    server = thread = None
    observed = []
    class Capture(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def setup(self):
            super().setup()
            self.connection.settimeout(5)
        def do_POST(self):
            if len(observed) or self.path != '/v1/responses':
                self.send_error(400)
                return
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 65536:
                self.send_error(413)
                return
            body = json.loads(self.rfile.read(length))
            observed.append(body)
            # Never retain auth, cookies or other request headers.
            write(ROOT / 'first-input.json', body['input'])
            write(ROOT / 'request-profile.json', request_profile(body))
            write(ROOT / 'captured-request.json', body)
            write(ROOT / 'wire-observation.json', {'path': self.path, 'user_agent': self.headers.get('User-Agent'), 'request_bytes': length})
            data = b'{"error":{"message":"Intentional zero-inference qualification preflight","type":"qualification_preflight"}}'
            self.send_response(503)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    try:
        report['setup'] = experiment.connect()
        server = http.server.HTTPServer(('127.0.0.1', 0), Capture)
        server.timeout = 0.5
        server.socket.settimeout(5)
        thread = threading.Thread(target=server.serve_forever, daemon=False)
        thread.start()
        report['native'] = experiment.launch('preflight-native', base_url='http://127.0.0.1:' + str(server.server_port) + '/v1', timeout=25)
        report['captured_submissions'] = len(observed)
        report['work_state'] = experiment.public('work/state')
        if len(observed) != 1 or report['work_state']['totals'].get('attempts', 0):
            raise RuntimeError('Preflight did not isolate one unforwarded request')
        report['profile_sha256'] = sha(canonical(read(ROOT / 'request-profile.json')))
        report['first_input_sha256'] = sha(canonical(semantic_input(read(ROOT / 'first-input.json'))))
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        report['failure'] = str(error)
    finally:
        if server:
            server.shutdown()
            if thread:
                thread.join(timeout=2)
            server.server_close()
        experiment.close(report)
        write(ROOT / 'preflight.json', report)
    if 'failure' in report or 'cleanup_error' in report:
        raise RuntimeError('Preflight failed; inspect retained report')
    print(json.dumps({'stage': report['stage'], 'captured': len(observed), 'model_inferences': 0,
                      'profile_sha256': report['profile_sha256']}), flush=True)


def independent_check():
    # Untrusted implementation runs under macOS Seatbelt with no network or writes.
    policy = '(version 1)(allow default)(deny network*)(deny file-write*)'
    code = 'import runpy,sys\noracle=sys.argv.pop(1)\nsys.argv[0]=oracle\ntry:\n runpy.run_path(oracle,run_name="__main__")\nexcept AssertionError:\n sys.exit(1)\nexcept BaseException:\n sys.exit(2)\n'
    try:
        result = subprocess.run(['/usr/bin/sandbox-exec', '-p', policy, str(PYTHON), '-I', '-B', '-c', code,
                    str(ROOT / 'oracle.py'), str(ROOT / 'project/solution.py')], env={'PATH': '/usr/bin:/bin', 'HOME': str(ROOT / 'tmp')},
                    cwd=ROOT / 'tmp', stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
        return result.returncode
    except subprocess.TimeoutExpired:
        return None


def run():
    if (ROOT / 'episode-started.json').exists():
        raise RuntimeError('Development episode already consumed; no quality retry')
    manifest = read(ROOT / 'frozen-manifest.json')
    for record in manifest['artifacts']:
        if sha(Path(record['path']).read_bytes()) != record['sha256']:
            raise RuntimeError('Frozen artifact changed: ' + record['path'])
    before = (ROOT / 'project/solution.py').read_bytes()
    baseline = independent_check()
    if baseline != 1:
        raise RuntimeError('Independent baseline must fail an assertion before inference')
    experiment = Experiment()
    report = {'stage': 'native-development-repair', 'campaign_id': 'local-repair-20261016-v6', 'case_id': 'dev-clamp',
              'baseline_exit_code': baseline, 'native_episodes': 1, 'quality_retries': 0, 'automatic_admission_effect': 'none'}
    try:
        report['setup'] = experiment.connect()
        report['readiness_before'] = experiment.cli('client', 'status', 'codex', '--client-home', str(experiment.client_home), '--routing')
        ordinary = read(ROOT / 'captured-request.json')
        connection = http.client.HTTPConnection('127.0.0.1', experiment.ports[0], timeout=3)
        try:
            connection.request('POST', '/v1/responses', canonical(ordinary), headers={
                'Content-Type': 'application/json', 'Authorization': 'Bearer ' + experiment.token,
                'User-Agent': read(ROOT / 'wire-observation.json')['user_agent']})
            response = connection.getresponse()
            data = response.read(65537)
            if len(data) > 65536:
                raise RuntimeError('Ordinary denial exceeded inspection bound')
            report['ordinary_coding_denial'] = {'status': response.status, 'body': json.loads(data)}
            if response.status != 503 or experiment.public('work/state')['totals'].get('attempts', 0):
                raise RuntimeError('Ordinary unqualified coding must block before inference')
        finally:
            connection.close()
        armed = experiment.cli('client', 'qualify', 'codex', '--client-home', str(experiment.client_home), '--plan', str(ROOT / 'probe-plan.json'))
        write(ROOT / 'episode-started.json', {'case_id': 'dev-clamp', 'plan_sha256': manifest['plan_sha256'], 'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
        report['native'] = experiment.launch('development-native', probe=armed['probe_id'], timeout=90)
        report['probe_after'] = experiment.cli('client', 'qualify', 'codex', '--client-home', str(experiment.client_home))
        report['readiness_after'] = experiment.cli('client', 'status', 'codex', '--client-home', str(experiment.client_home), '--routing')
        report['work_state'] = experiment.public('work/state')
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        report['failure'] = str(error)
    finally:
        if experiment.generation:
            try:
                report['revoked'] = experiment.cli('client', 'qualify', 'codex', '--client-home', str(experiment.client_home), '--revoke')
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                report['revocation_error'] = str(error)
        experiment.close(report)
        try:
            with (ROOT / 'project/solution.py').open('rb') as stream:
                after = stream.read(65537)
            if len(after) > 65536:
                raise ValueError('Implementation exceeded 64 KiB inspection bound')
            (ROOT / 'implementation-after.bin').write_bytes(after)
            report['implementation_changed'] = before != after
            patch = ''.join(difflib.unified_diff(before.decode().splitlines(keepends=True), after.decode().splitlines(keepends=True), fromfile='before/solution.py', tofile='after/solution.py'))
            (ROOT / 'implementation.patch').write_text(patch)
            report['patch_sha256'] = sha(patch.encode()) if patch else None
        except (OSError, ValueError, UnicodeError) as error:
            report.update(implementation_changed=None, patch_sha256=None, implementation_inspection_error=str(error))
        try:
            report['checks_sha256'] = sha((ROOT / 'oracle.py').read_bytes())
            report['checks_unchanged'] = report['checks_sha256'] == read(ROOT / 'corpus.json')['entries'][0]['checks_sha256']
            report['check_exit_code'] = independent_check()
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
            report.update(checks_unchanged=False, check_exit_code=None, check_inspection_error=str(error))
        report['completed_turns'] = 0
        report['invalid_event_lines'] = 0
        try:
            with (ROOT / 'development-native.jsonl').open('rb') as stream:
                raw = stream.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError('Native event log exceeded two MiB inspection bound')
            for line in raw.splitlines():
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict):
                        raise ValueError('Nonobject native event')
                    report['completed_turns'] += int(event.get('type') == 'turn.completed')
                except (ValueError, UnicodeError):
                    report['invalid_event_lines'] += 1
        except (OSError, ValueError) as error:
            report['native_inspection_error'] = str(error)
        report['passed'] = bool(report.get('native', {}).get('exit_code') == 0 and report.get('native', {}).get('termination') == 'completed'
            and not report.get('native', {}).get('cleanup_error') and report['completed_turns'] and not report['invalid_event_lines']
            and report.get('implementation_changed') and report.get('checks_unchanged') and report.get('check_exit_code') == 0
            and report['native']['elapsed_ms'] <= 90000 and not report.get('failure')
            and report.get('settings_restored') is True and report.get('owned_listeners_closed') is True
            and not report.get('cleanup_error') and not any(key.endswith('inspection_error') for key in report))
        write(ROOT / 'development-result.json', report)
    print(json.dumps({key: report.get(key) for key in ('passed', 'native', 'implementation_changed', 'check_exit_code', 'completed_turns', 'failure', 'settings_restored', 'owned_listeners_closed')}), flush=True)
    return 0 if report['passed'] else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['initialize', 'preflight', 'run'])
    args = parser.parse_args()
    if args.phase == 'initialize':
        initialize()
    elif args.phase == 'preflight':
        preflight()
    else:
        raise SystemExit(run())
