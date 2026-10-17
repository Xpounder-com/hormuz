"""Replay the app's cold-start RPC sequence in a disposable, unauthenticated home."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest


NATIVE_STARTUP = '''#!PYTHON
import json,sys,tomllib,os,pathlib
home=pathlib.Path(os.environ['CODEX_HOME'])
assert sys.argv[1:]==json.loads((home/'expected-arguments.json').read_text())
for line in sys.stdin:
    request=json.loads(line)
    if 'id' not in request: continue
    method=request['method']
    if method=='initialize': result={'userAgent':'codex-cli/0.162.0-alpha.17.2'}
    elif method=='configRequirements/read': result={'requirements':None}
    elif method=='config/read': result={'config':tomllib.loads((home/'config.toml').read_text()),'layers':[],'origins':{}}
    else: raise RuntimeError('Unexpected startup request')
    print(json.dumps({'id':request['id'],'result':result}),flush=True)
'''


class CodexStartupWorkflowTests(unittest.TestCase):
    def test_normal_app_startup_activation_is_rejected_without_changing_any_files(self):
        with tempfile.TemporaryDirectory(prefix='hormuz-no-app-activation-') as directory:
            root = Path(directory)
            home = root / 'normal-codex'
            home.mkdir(mode=0o700)
            original = 'model = "gpt-fixture"\nmodel_reasoning_effort = "max"\n'
            (home / 'config.toml').write_text(original)
            for options in ([], ['--dry-run'], ['--isolated-test']):
                with self.subTest(options=options):
                    result = subprocess.run([sys.executable, '-m', 'hormuz', 'client', 'bridge', 'codex',
                        '--client-home', str(home), '--router-home', str(root / 'router'),
                        '--directory', str(root / 'adapter'), '--executable', str(root / 'native'), *options],
                        capture_output=True, text=True, timeout=6)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(json.loads(result.stderr)['error']['code'],
                                     'connected_client_app_activation_disabled')
            self.assertEqual((home / 'config.toml').read_text(), original)
            self.assertEqual(sorted(path.name for path in home.iterdir()), ['config.toml'])
            self.assertFalse((root / 'adapter').exists())
            self.assertFalse((root / 'router').exists())

    def test_local_catalog_outage_cannot_block_the_native_organization_settings_preflight(self):
        self.check_cold_start(['app-server', '--stdio'])

    def test_exact_desktop_launch_options_preserve_native_organization_settings_preflight(self):
        self.check_cold_start(['-c', 'features.code_mode_host=true', 'app-server', '--analytics-default-enabled'])

    def check_cold_start(self, native_arguments):
        with tempfile.TemporaryDirectory(prefix='hormuz-cold-start-') as directory:
            root = Path(directory)
            home, control, adapter = (root / name for name in ('codex', 'router', 'adapter'))
            for path in (home, control, adapter):
                path.mkdir(mode=0o700)
            original = 'model = "gpt-fixture"\nmodel_reasoning_effort = "max"\n[plugins.fixture]\nenabled = true\n'
            (home / 'config.toml').write_text(original)
            (home / 'auth.json').write_text('SYNTHETIC_AUTH_MUST_NOT_CHANGE')
            (home / 'expected-arguments.json').write_text(json.dumps(native_arguments))
            (home / 'models_cache.json').write_text(json.dumps({'models': [{'slug': 'gpt-fixture'}]}))
            native = root / 'native-codex'
            native.write_text(NATIVE_STARTUP.replace('#!PYTHON', '#!' + sys.executable, 1))
            native.chmod(0o700)
            requests = []

            class UnavailableCatalog(BaseHTTPRequestHandler):
                def do_GET(self):
                    requests.append(self.path)
                    self.send_response(503)
                    self.send_header('Content-Length', '0')
                    self.end_headers()

                def log_message(self, *_args):
                    pass

            server = HTTPServer(('127.0.0.1', 0), UnavailableCatalog)
            thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05})
            thread.start()
            try:
                (control / 'config.toml').write_text('model = "local-code"\nmodel_provider = "hormuz_fixture"\n'
                    '[model_providers.hormuz_fixture]\nbase_url = "http://127.0.0.1:'
                    + str(server.server_port) + '/v1"\nenv_key = "STARTUP_FIXTURE_TOKEN"\n')
                manifest = home / 'hormuz-app-bridge-v1.json'
                manifest.write_text(json.dumps({'schema_id': 'hormuz.codex-app-bridge', 'schema_version': 1,
                    'enabled': True, 'client_home': str(home), 'control_home': str(control),
                    'native_executable': str(native), 'native_sha256': hashlib.sha256(native.read_bytes()).hexdigest(),
                    'native_version': '0.162.0-alpha.17.2', 'catalog_path': str(adapter / 'models.json')}))
                manifest.chmod(0o600)
                command = [sys.executable, '-m', 'hormuz.codex_app_bridge', '--manifest', str(manifest), *native_arguments]
                environment = {**os.environ, 'CODEX_HOME': str(home), 'STARTUP_FIXTURE_TOKEN': 'synthetic-token'}
                with (root / 'stderr').open('w+') as errors:
                    process = subprocess.Popen(command, env=environment, stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, stderr=errors, start_new_session=True)
                    reader = selectors.DefaultSelector()
                    reader.register(process.stdout, selectors.EVENT_READ)
                    try:
                        def rpc(identifier, method, params):
                            process.stdin.write((json.dumps({'id': identifier, 'method': method, 'params': params}) + '\n').encode())
                            process.stdin.flush()
                            deadline = time.monotonic() + 6
                            while time.monotonic() < deadline:
                                if not reader.select(min(1, max(0, deadline - time.monotonic()))):
                                    continue
                                line = process.stdout.readline()
                                if not line:
                                    errors.flush()
                                    errors.seek(0)
                                    self.fail('Organization settings could not be loaded: native startup was blocked. ' + errors.read())
                                response = json.loads(line)
                                if response.get('id') == identifier:
                                    self.assertNotIn('error', response)
                                    return response['result']
                            self.fail('Organization settings could not be loaded: bounded startup deadline expired')

                        initialized = rpc('network-initialize', 'initialize', {'clientInfo': {
                            'name': 'codex_desktop', 'title': 'Codex Desktop', 'version': 'fixture'},
                            'capabilities': {'experimentalApi': True}})
                        self.assertEqual(initialized['userAgent'], 'codex-cli/0.162.0-alpha.17.2')
                        process.stdin.write(b'{"method":"initialized"}\n')
                        process.stdin.flush()
                        self.assertEqual(rpc('network-requirements', 'configRequirements/read', {}), {'requirements': None})
                        config = rpc('network-config', 'config/read', {'includeLayers': False})['config']
                        self.assertEqual(config['model'], 'gpt-fixture')
                        self.assertNotIn('model_catalog_json', config)
                        self.assertEqual((home / 'config.toml').read_text(), original)
                        self.assertEqual((home / 'auth.json').read_text(), 'SYNTHETIC_AUTH_MUST_NOT_CHANGE')
                        self.assertEqual(requests, ['/v1/models'])
                    finally:
                        reader.close()
                        process.stdin.close()
                        try:
                            process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGTERM)
                            process.wait(timeout=3)
                        process.stdout.close()
            finally:
                server.shutdown()
                thread.join(timeout=3)
                server.server_close()


if __name__ == '__main__':
    unittest.main()
