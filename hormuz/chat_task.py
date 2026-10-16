"""Explicit text tasks through an already-owned router; no Codex startup hook."""
import http.client
import json
import os
from pathlib import Path
import sys
import time
import tomllib
from urllib.parse import urlsplit
from urllib.request import Request

from .connected_client import ConnectedClientError, ConnectedClientLifetime
from .contracts import validate_contract
from .local_transport import open_local_upstream

MAX_PROMPT = 65536
MAX_RESPONSE = 2 * 1024 * 1024


class ChatTaskError(RuntimeError):
    pass


def add_commands(commands):
    chat = commands.add_parser('chat', help='Use Hormuz for an explicit task without changing the Codex model')
    operations = chat.add_subparsers(dest='chat_command', required=True)
    for name in ('models', 'ask'):
        command = operations.add_parser(name)
        command.add_argument('--router-home', required=True, help='Separate, already connected Hormuz control home')
        if name == 'ask':
            command.add_argument('--model', help='Qualified local alias; omit to use the configured router default')
            command.add_argument('--prompt-file', required=True, help='UTF-8 task file, or - for standard input')
            command.add_argument('--max-output-tokens', type=int, default=2048, choices=range(1, 8193), metavar='1..8192')
            command.add_argument('--effort', choices=['low', 'medium', 'high', 'max'], default='max')
            command.add_argument('--timeout-seconds', type=int, default=180, choices=range(1, 361), metavar='1..360')


def run(args):
    submitted = False
    try:
        prompt = None
        if args.chat_command == 'ask':
            if args.prompt_file == '-':
                raw = sys.stdin.buffer.read(MAX_PROMPT + 1)
            else:
                with Path(args.prompt_file).expanduser().open('rb') as source:
                    raw = source.read(MAX_PROMPT + 1)
            if not raw.strip() or len(raw) > MAX_PROMPT:
                raise ChatTaskError('prompt_limit')
            prompt = raw.decode('utf-8')
        home = Path(args.router_home).expanduser().resolve()
        if home == (Path.home() / '.codex').resolve() or home == Path(os.environ.get('CODEX_HOME', '~/.codex')).expanduser().resolve():
            raise ChatTaskError('separate_router_home_required')
        lifetime = ConnectedClientLifetime(home)
        status = lifetime.status()
        if status.get('phase') != 'connected' or status.get('router') != 'ready':
            raise ChatTaskError('router_unavailable')
        if status.get('configuration') != 'installed':
            raise ChatTaskError('router_ownership_changed')
        config = tomllib.loads((home / 'config.toml').read_text())
        provider_id = config['model_provider']
        if provider_id != 'hormuz_' + status['generation']:
            raise ChatTaskError('router_ownership_changed')
        provider = config['model_providers'][provider_id]
        origin = urlsplit(provider['base_url'])
        if (origin.scheme != 'http' or origin.hostname != '127.0.0.1' or origin.path != '/v1'
                or origin.query or origin.fragment or origin.username or origin.password):
            raise ChatTaskError('owned_loopback_router_required')
        token = lifetime.local_token(status['generation']) if 'auth' in provider else os.environ[provider['env_key']]
        deadline = time.monotonic() + (args.timeout_seconds if args.chat_command == 'ask' else 6)

        def request(path, payload=None):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('chat_deadline')
            headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                       'User-Agent': 'Hormuz explicit-chat-task', 'X-Hormuz-Routing-Mode': 'local_only'}
            message = Request(provider['base_url'] + path, headers=headers,
                              data=None if payload is None else json.dumps(payload).encode())
            with open_local_upstream(message, timeout=min(4, remaining), absolute_timeout=remaining,
                                     response_timeout=remaining) as response:
                body = response.read(MAX_RESPONSE + 1)
                if response.status != 200:
                    raise ChatTaskError('router_request_rejected')
                if len(body) > MAX_RESPONSE:
                    raise ChatTaskError('response_limit')
                return json.loads(body), response.headers

        state, _ = request('/work/state')
        if state.get('schema_id') != 'hormuz.ai-work-state' or state.get('schema_version') != 3:
            raise ChatTaskError('router_ownership_changed')
        admission, _ = request('/router/state')
        validate_contract(admission)
        qualified = {(route['model'], route['destination'], route['generation']) for route in admission['routes']
                     if {'responses', 'text', 'codex_http', 'codex_llama_v1'} <= set(route['capabilities'])}
        routes = [route for route in state['connection']['routes'] if route.get('locality') == 'local'
                  and route.get('protocol') == 'openai' and route.get('eligible') is True
                  and (route['model'], route['destination'], route['generation']) in qualified]
        if args.chat_command == 'models':
            result = {'schema_id': 'hormuz.chat-models', 'schema_version': 1,
                      'default_model': config['model'], 'models': [route['model'] for route in routes]}
        else:
            model = args.model or config['model']
            selected = [route for route in routes if route['model'] == model]
            if len(selected) != 1:
                raise ChatTaskError('model_not_qualified')
            route = selected[0]
            submitted = True
            body, headers = request('/responses', {'model': model, 'stream': False, 'store': False,
                'input': prompt, 'max_output_tokens': args.max_output_tokens, 'reasoning': {'effort': args.effort}})
            if (headers.get('X-Hormuz-Destination-Locality') != 'local'
                    or headers.get('X-Hormuz-Destination') != route['destination']
                    or headers.get('X-Hormuz-Destination-Generation') != route['generation']
                    or headers.get('X-Hormuz-Requested-Model') != model
                    or headers.get('X-Hormuz-Routed-Model') != route['upstream_model']
                    or headers.get('X-Hormuz-Failover') is not None):
                raise ChatTaskError('execution_selection_changed')
            if body.get('status') != 'completed':
                raise ChatTaskError('task_incomplete')
            text = ''.join(block['text'] for item in body.get('output', []) if item.get('type') == 'message'
                           for block in item.get('content', []) if block.get('type') == 'output_text')
            result = {'schema_id': 'hormuz.chat-task', 'schema_version': 1, 'text': text, 'model': model,
                      'routed_model': route['upstream_model'], 'locality': 'local', 'destination': route['destination'],
                      'generation': route['generation'], 'work_id': headers.get('X-Hormuz-Work-Id'),
                      'selection': 'model' if args.model else 'router_default', 'reasoning_effort': args.effort}
        validate_contract(result)
        print(json.dumps(result))
        return 0
    except (ChatTaskError, ConnectedClientError, OSError, ValueError, KeyError, TypeError, http.client.HTTPException) as error:
        code = str(error) if isinstance(error, ChatTaskError) else 'task_unavailable'
        result = {'schema_id': 'hormuz.chat-error', 'schema_version': 1, 'error': {'code': code},
                  'submission': 'possibly_submitted' if submitted else 'not_submitted', 'retried': False}
        validate_contract(result)
        print(json.dumps(result), file=sys.stderr)
        return 2
