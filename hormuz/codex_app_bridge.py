"""Provider-aware stdio adapter around the user's unchanged Codex app engine."""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import json
import os
from pathlib import Path
import secrets
import signal
import stat
import subprocess
import sys
import tempfile
import tomllib
from urllib.parse import urlsplit

FRAME_LIMIT = 64 * 1024 * 1024
MANIFEST_FIELDS = {'schema_id', 'schema_version', 'enabled', 'client_home', 'control_home',
                   'native_executable', 'native_sha256', 'native_version', 'catalog_path'}


def read_manifest(path, *, verify_native=True):
    metadata = path.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077 or metadata.st_size > 128 * 1024):
        raise ValueError('Unsafe app bridge manifest')
    value = json.loads(path.read_text())
    if (set(value) != MANIFEST_FIELDS or value['schema_id'] != 'hormuz.codex-app-bridge'
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or type(value['enabled']) is not bool or value['native_version'] != '0.162.0-alpha.17.2'):
        raise ValueError('Unsupported app bridge manifest')
    for field in ('client_home', 'control_home', 'native_executable', 'catalog_path'):
        if type(value[field]) is not str or not Path(value[field]).is_absolute() or '\x00' in value[field]:
            raise ValueError('Invalid app bridge path')
    if Path(value['client_home']).resolve() == Path(value['control_home']).resolve():
        raise ValueError('App bridge requires a separate router control home')
    if verify_native:
        with Path(value['native_executable']).open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != value['native_sha256']:
                raise ValueError('The native Codex executable changed')
    return value


class Bridge:
    def __init__(self, manifest, native_args):
        self.manifest = manifest
        self.enabled = manifest['enabled']
        self.native_args = native_args
        self.native = None
        self.pending = {}
        self.sequence = 0
        self.prefix = 'hormuz.' + secrets.token_hex(16) + '.'
        self.output = None
        self.handlers = set()
        self.catalog_lock = asyncio.Lock()
        self.threads = {}
        self.native_aliases = set()
        self.rebinding = set()
        original = tomllib.loads((Path(manifest['client_home']) / 'config.toml').read_text())
        self.default_provider = original.get('model_provider') or 'openai'
        self.original_catalog = original.get('model_catalog_json')
        settings = tomllib.loads((Path(manifest['control_home']) / 'config.toml').read_text())
        self.provider_id = settings['model_provider']
        self.provider = settings['model_providers'][self.provider_id]
        self.local_aliases = {settings['model']}
        self.selection_path = Path(manifest['catalog_path']).with_name('local-selection-v1.json')
        self.selection = {}
        if self.selection_path.exists():
            metadata = self.selection_path.lstat()
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
                    or metadata.st_mode & 0o077 or metadata.st_size > 128 * 1024):
                raise ValueError('Unsafe local selection file')
            selected = json.loads(self.selection_path.read_text())
            if (set(selected) != {'schema_id', 'schema_version', 'edits'}
                    or selected['schema_id'] != 'hormuz.codex-app-selection' or selected['schema_version'] != 1
                    or type(selected['edits']) is not dict
                    or set(selected['edits']) - {'model', 'model_reasoning_effort'}
                    or selected['edits'].get('model') not in self.local_aliases):
                raise ValueError('Invalid local model selection')
            self.selection = selected['edits']

    def write_view(self, models):
        self.write_document(Path(self.manifest['catalog_path']),
            {'schema_id': 'hormuz.codex-app-model-view', 'schema_version': 1, 'data': models})

    @staticmethod
    def write_document(target, document):
        parent = target.parent.stat()
        if parent.st_uid != os.getuid() or parent.st_mode & 0o022:
            raise ValueError('Unsafe managed catalog directory')
        if target.exists() or target.is_symlink():
            existing = target.lstat()
            if not stat.S_ISREG(existing.st_mode) or existing.st_uid != os.getuid() or existing.st_mode & 0o077:
                raise ValueError('Unsafe managed catalog file')
        descriptor, name = tempfile.mkstemp(prefix='.hormuz-model-view-', dir=target.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, 'w') as output:
                json.dump(document, output)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def refresh_enabled(self):
        try:
            current = read_manifest(Path(self.manifest['client_home']) / 'hormuz-app-bridge-v1.json', verify_native=False)
            self.enabled = current['enabled'] and all(current[key] == value for key, value in self.manifest.items() if key != 'enabled')
        except (OSError, ValueError, KeyError):
            self.enabled = False

    def filter_selection_write(self, method, params):
        if method == 'config/value/write':
            edits = [{key: params[key] for key in ('keyPath', 'value', 'mergeStrategy')}]
        else:
            edits = params['edits']
        model = next((edit['value'] for edit in reversed(edits) if edit['keyPath'] == 'model'), self.selection.get('model'))
        if model not in self.local_aliases:
            return method, params, {} if any(edit['keyPath'] == 'model' for edit in edits) else None
        target = params.get('filePath')
        if target is not None and Path(target).resolve() != (Path(self.manifest['client_home']) / 'config.toml').resolve():
            raise ValueError('Local defaults require the normal user configuration scope')
        selected = dict(self.selection)
        remaining = []
        for edit in edits:
            key = edit['keyPath']
            if key in {'model', 'model_reasoning_effort'}:
                if key == 'model_reasoning_effort' and edit['value'] not in {'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'}:
                    raise ValueError('Unsupported local reasoning effort')
                if edit['mergeStrategy'] != 'upsert':
                    raise ValueError('Unsupported local preference edit')
                selected[key] = edit['value']
            else:
                remaining.append(edit)
        return 'config/batchWrite', {key: value for key, value in {**params, 'edits': remaining}.items()
            if key not in {'keyPath', 'value', 'mergeStrategy'}}, selected

    async def emit(self, value):
        self.output.write((json.dumps(value, separators=(',', ':')) + '\n').encode())
        await self.output.drain()

    async def send_native(self, value):
        self.native.stdin.write((json.dumps(value, separators=(',', ':')) + '\n').encode())
        await self.native.stdin.drain()

    async def rpc(self, method, params, *, ordered=True):
        if len(self.pending) >= 64:
            raise RuntimeError('Native request queue is full')
        self.sequence += 1
        identifier = self.prefix + str(self.sequence)
        future = asyncio.get_running_loop().create_future()
        delivered = asyncio.get_running_loop().create_future() if ordered else None
        self.pending[identifier] = (future, delivered)
        try:
            await self.send_native({'id': identifier, 'method': method, 'params': params})
            return await asyncio.wait_for(future, timeout=45), delivered
        finally:
            self.pending.pop(identifier, None)

    def local_catalog(self, *, native=False):
        origin = urlsplit(self.provider['base_url'])
        if origin.scheme != 'http' or origin.hostname != '127.0.0.1' or origin.path != '/v1':
            raise ValueError('The local provider must use the owned loopback router')
        if 'auth' in self.provider:
            authentication = self.provider['auth']
            credential = subprocess.run([authentication['command'], *authentication['args']],
                capture_output=True, text=True, timeout=6, check=True).stdout.strip()
        else:
            credential = os.environ[self.provider['env_key']]
        connection = http.client.HTTPConnection(origin.hostname, origin.port, timeout=4)
        try:
            connection.request('GET', '/v1/models', headers={'Authorization': 'Bearer ' + credential})
            response = connection.getresponse()
            body = response.read(2 * 1024 * 1024 + 1)
            if response.status != 200 or len(body) > 2 * 1024 * 1024:
                raise ValueError('Local catalog unavailable')
            models = json.loads(body)['models']
            if not isinstance(models, list) or len(models) > 128:
                raise ValueError('Local catalog exceeds its bound')
            if native:
                return models
            return [{'id': model['slug'], 'model': model['slug'], 'displayName': model['display_name'],
                     'description': model['description'], 'hidden': model['visibility'] != 'list',
                     'isDefault': False, 'defaultReasoningEffort': model['default_reasoning_level'],
                     'supportedReasoningEfforts': [{'reasoningEffort': level['effort'], 'description': level['description']}
                         for level in model['supported_reasoning_levels']],
                     'inputModalities': model['input_modalities'], 'supportsPersonality': False}
                    for model in models]
        finally:
            connection.close()

    def prepare_engine_catalog(self):
        home = Path(self.manifest['client_home'])
        settings = tomllib.loads((home / 'config.toml').read_text())
        configured = settings.get('model_catalog_json')
        path = Path(configured).expanduser() if configured else home / 'models_cache.json'
        if not path.is_absolute():
            path = home / path
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('Native model metadata exceeds its bound')
        native = json.loads(path.read_text())['models']
        if not isinstance(native, list) or not native or len(native) > 512:
            raise ValueError('Native model metadata unavailable')
        local = self.local_catalog(native=True)
        if {model['slug'] for model in native} & {model['slug'] for model in local}:
            raise ValueError('Native and local model metadata overlap')
        target = Path(self.manifest['catalog_path']).with_name('engine-models-v1.json')
        self.write_document(target, {'models': native + local})
        return target

    def selected_params(self, params, model, provider):
        value = {**params, 'model': model, 'modelProvider': provider}
        configuration = dict(value.get('config') or {})
        if provider == self.provider_id:
            configuration['model_providers.' + self.provider_id] = self.provider
            configuration['model_provider'] = self.provider_id
            configuration['model'] = model
        value['config'] = configuration
        return value

    def remember(self, params, response):
        thread_id = response['thread']['id']
        if thread_id not in self.threads and len(self.threads) >= 1024:
            raise RuntimeError('Loaded thread limit reached')
        self.threads[thread_id] = {'provider': response['modelProvider'], 'model': response['model'],
            'params': params, 'busy': False, 'lock': asyncio.Lock(), 'collaboration': response.get('collaborationMode')}

    async def prepare_turn(self, params):
        thread_id = params['threadId']
        thread = self.threads.get(thread_id)
        if thread is None:
            if params.get('model') in self.local_aliases:
                raise RuntimeError('Resume this chat before selecting a local provider')
            return params
        async with thread['lock']:
            model = params.get('model') or (params.get('collaborationMode') or {}).get('settings', {}).get('model') or thread['model']
            provider = (self.provider_id if model in self.local_aliases else self.default_provider
                        if model in self.native_aliases else thread['provider'])
            if provider == self.provider_id and not self.enabled:
                raise RuntimeError('Hormuz is disabled; select a native account model')
            if provider != thread['provider']:
                if thread['busy'] or thread['params'].get('ephemeral'):
                    raise RuntimeError('Provider changes require an idle persistent chat')
                self.rebinding.add(thread_id)
                try:
                    unsubscribed, _ = await self.rpc('thread/unsubscribe', {'threadId': thread_id}, ordered=False)
                    if unsubscribed.get('result', {}).get('status') != 'unsubscribed':
                        raise RuntimeError('The native thread could not be unloaded safely')
                    selected = self.selected_params({**thread['params'], 'threadId': thread_id}, model, provider)
                    resumed, _ = await self.rpc('thread/resume', selected, ordered=False)
                    if resumed.get('result', {}).get('modelProvider') != provider:
                        raise RuntimeError('Native provider selection was not applied')
                    self.remember(thread['params'], resumed['result'])
                    thread = self.threads[thread_id]
                finally:
                    self.rebinding.discard(thread_id)
            value = {**params, 'model': model}
            collaboration = params.get('collaborationMode') or thread['collaboration']
            if collaboration is not None:
                value['collaborationMode'] = {**collaboration, 'settings': {**collaboration['settings'], 'model': model}}
            thread['model'] = model
            thread['busy'] = True
            return value

    async def handle(self, request):
        identifier = request['id']
        delivered = None
        try:
            method = request['method']
            params = request.get('params') or {}
            original = dict(params)
            self.refresh_enabled()
            selection = None
            if method in {'config/batchWrite', 'config/value/write'}:
                edits = params.get('edits', [params])
                if not self.enabled and any(edit.get('keyPath') == 'model' and edit.get('value') in self.local_aliases for edit in edits):
                    raise RuntimeError('Hormuz is disabled; the ChatGPT default was preserved')
                if self.enabled:
                    method, params, selection = self.filter_selection_write(method, params)
            if self.enabled and method == 'thread/start' and params.get('model') is None and self.selection:
                params = {**params, 'model': self.selection['model']}
            if method in {'thread/start', 'thread/resume', 'thread/fork'} and params.get('model') in self.local_aliases:
                if not self.enabled:
                    raise RuntimeError('Hormuz is disabled; select a native account model')
                params = self.selected_params(params, params['model'], self.provider_id)
            if method == 'turn/start':
                params = await self.prepare_turn(params)
            response, delivered = await self.rpc(method, params)
            if 'result' in response and selection is not None:
                self.selection = selection
                if selection:
                    self.write_document(self.selection_path, {'schema_id': 'hormuz.codex-app-selection',
                        'schema_version': 1, 'edits': selection})
                else:
                    self.selection_path.unlink(missing_ok=True)
            if 'result' in response and method in {'thread/start', 'thread/resume', 'thread/fork'}:
                self.remember(original, response['result'])
            if 'error' in response and method == 'turn/start' and params['threadId'] in self.threads:
                self.threads[params['threadId']]['busy'] = False
            if 'result' in response and method == 'config/read' and self.enabled:
                if not Path(self.manifest['catalog_path']).exists():
                    self.write_view([])
                response['result']['config']['model_catalog_json'] = self.manifest['catalog_path']
                response['result']['config'].update(self.selection)
            if 'result' in response and method == 'config/read' and not self.enabled:
                if self.original_catalog is None:
                    response['result']['config'].pop('model_catalog_json', None)
                else:
                    response['result']['config']['model_catalog_json'] = self.original_catalog
            if 'result' in response and method == 'model/list':
                response['result']['data'] = [model for model in response['result']['data'] if model['model'] not in self.local_aliases]
                self.native_aliases.update(model['model'] for model in response['result']['data'])
            if 'result' in response and method == 'model/list' and self.enabled:
                async with self.catalog_lock:
                    models = await asyncio.to_thread(self.local_catalog)
                data = response['result']['data']
                if {model['model'] for model in data} & {model['model'] for model in models}:
                    raise ValueError('Local and native model identities overlap')
                self.local_aliases.update(model['model'] for model in models)
                response['result']['data'] = data + models
                self.write_view(response['result']['data'])
            await self.emit({**response, 'id': identifier})
        except (OSError, ValueError, KeyError, subprocess.SubprocessError, asyncio.TimeoutError, RuntimeError):
            await self.emit({'id': identifier, 'error': {'code': -32000,
                'message': 'Hormuz app bridge could not complete this request; no model request was retried.'}})
        finally:
            if delivered is not None and not delivered.done():
                delivered.set_result(None)

    async def read_native(self):
        while line := await self.native.stdout.readline():
            message = json.loads(line)
            if 'method' not in message and message.get('id') in self.pending:
                future, delivered = self.pending[message['id']]
                if not future.done():
                    future.set_result(message)
                    if delivered is not None:
                        await delivered
            elif 'method' not in message and str(message.get('id', '')).startswith(self.prefix):
                continue
            else:
                if message.get('method') == 'turn/started':
                    thread = self.threads.get(message['params']['threadId'])
                    if thread is not None:
                        thread['busy'] = True
                if message.get('method') == 'turn/completed':
                    thread = self.threads.get(message['params']['threadId'])
                    if thread is not None:
                        thread['busy'] = False
                if (message.get('method') in {'thread/closed', 'thread/status/changed'}
                        and message.get('params', {}).get('threadId') in self.rebinding):
                    continue
                await self.emit(message)

    async def read_client(self, reader):
        while line := await reader.readline():
            message = json.loads(line)
            if 'method' in message and 'id' in message:
                if len(self.handlers) >= 32:
                    await self.emit({'id': message['id'], 'error': {'code': -32000, 'message': 'App bridge request queue is full'}})
                    continue
                handler = asyncio.create_task(self.handle(message))
                self.handlers.add(handler)
                handler.add_done_callback(self.handlers.discard)
            else:
                await self.send_native(message)

    async def run(self):
        # Startup must remain available even when the optional local service is
        # stopped. Do this before reading stdin or emitting any protocol frames:
        # the app first needs its native organization-policy preflight.
        try:
            catalog = await asyncio.to_thread(self.prepare_engine_catalog)
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            os.execv(self.manifest['native_executable'],
                     [self.manifest['native_executable'], *self.native_args])
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(limit=FRAME_LIMIT)
        input_transport, _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer)
        protocol = asyncio.streams.FlowControlMixin(loop=loop)
        output_transport, _ = await loop.connect_write_pipe(lambda: protocol, sys.stdout.buffer)
        self.output = asyncio.StreamWriter(output_transport, protocol, None, loop)
        self.native = await asyncio.create_subprocess_exec(self.manifest['native_executable'],
            '-c', 'model_catalog_json=' + json.dumps(str(catalog)), *self.native_args,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, limit=FRAME_LIMIT, start_new_session=True)
        workers = [asyncio.create_task(self.read_client(reader)), asyncio.create_task(self.read_native())]
        try:
            done, _ = await asyncio.wait(workers, return_when=asyncio.FIRST_COMPLETED)
            for worker in done:
                await worker
        finally:
            input_transport.close()
            for worker in workers + list(self.handlers):
                worker.cancel()
            await asyncio.gather(*workers, *self.handlers, return_exceptions=True)
            self.native.stdin.close()
            try:
                await asyncio.wait_for(self.native.wait(), timeout=5)
            except asyncio.TimeoutError:
                os.killpg(self.native.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(self.native.wait(), timeout=5)
                except asyncio.TimeoutError:
                    os.killpg(self.native.pid, signal.SIGKILL)
                    await self.native.wait()
            try:
                os.killpg(self.native.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            output_transport.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, add_help=False, allow_abbrev=False)
    parser.add_argument('--manifest', required=True, type=Path)
    args, native_args = parser.parse_known_args(argv)
    os.umask(0o077)
    try:
        manifest = read_manifest(args.manifest)
        if 'app-server' not in native_args or not manifest['enabled']:
            os.execv(manifest['native_executable'], [manifest['native_executable'], *native_args])

        async def run():
            asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
            await Bridge(manifest, native_args).run()

        asyncio.run(run())
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, asyncio.IncompleteReadError):
        print('Hormuz app bridge unavailable; use hormuz off to restore the native Codex setup.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
