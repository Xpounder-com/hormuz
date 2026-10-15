"""Bounded Responses probes through the configured router; no route enrollment.

A report is user-owned evidence about a tiny task envelope. It is not proof of
which binary serves an endpoint and cannot grant admission or change policy.
"""
import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import stat
import time

from .config import GatewayConfig
from .connected_client import _qualify_client
from .local_history_files import export_private, local_path, read_private
from .plugin_policy import CapabilityError

SUITE = 'responses-local-smoke-v1'
MAX_RESPONSE = 262144


def _digest(path):
    descriptor = os.open(Path(path).expanduser(), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    deadline = time.monotonic() + 10
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024**3:
            raise CapabilityError('asset_unsafe')
        digest = hashlib.sha256()
        while block := stream.read(1024 * 1024):
            digest.update(block)
            if time.monotonic() > deadline:
                raise CapabilityError('asset_hash_timeout')
        after = os.fstat(stream.fileno())
        if (info.st_size, info.st_mtime_ns, info.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise CapabilityError('asset_changed')
        return digest.hexdigest(), info.st_size


def _setup(args, config):
    route = config.model_routes.get(args.model)
    if route is None or route.protocol != 'openai' or config.upstreams[route.destination_id].locality != 'local':
        raise CapabilityError('local_responses_route_required')
    _qualify_client(str(Path(args.client_executable).expanduser()))
    runtime, _ = _digest(args.runtime_executable)
    model, model_bytes = _digest(args.model_file)
    client, _ = _digest(args.client_executable)
    source, _ = _digest(args.config)
    return {'configuration_sha256': source, 'client_sha256': client,
            'runtime_sha256': runtime, 'model_sha256': model,
            'model_bytes': model_bytes, 'model_alias': args.model,
            'model_revision': route.model_revision, 'system': platform.system(),
            'machine': platform.machine(), 'suite': SUITE}


def _text(value):
    return ''.join(block.get('text', '') for item in value.get('output', [])
                   if isinstance(item, dict) and item.get('type') == 'message'
                   for block in item.get('content', []) if isinstance(block, dict)
                   and block.get('type') == 'output_text' and isinstance(block.get('text'), str))


def qualify(args):
    config = GatewayConfig.load(args.config)
    setup = _setup(args, config)
    output = local_path(Path(args.output))
    if output.exists() or output.is_symlink():
        raise CapabilityError('output_exists')
    actors = list(config.identities_by_actor.values())
    if args.actor:
        actors = [item for item in actors if item.actor_id == args.actor]
    if len(actors) != 1:
        raise CapabilityError('actor_required')
    identity = actors[0]
    token = next((token for token, owner in config.identities_by_token.items() if owner.actor_id == identity.actor_id), None)
    if token is None or config.ingress.mode != 'local':
        raise CapabilityError('direct_static_router_auth_required')
    if config.listen.host not in {'127.0.0.1', '::1', 'localhost'}:
        raise CapabilityError('loopback_router_required')
    checks = []
    # One request at a time; at most four requests, no retries or redirects.
    connection = http.client.HTTPConnection(config.listen.host, config.listen.port, timeout=3)
    deadline = time.monotonic() + 12
    last_work_id = None
    def submit(payload, *, work_id=None):
        nonlocal last_work_id
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CapabilityError('qualification_timeout')
        connection.timeout = min(3, remaining)
        if connection.sock:
            connection.sock.settimeout(connection.timeout)
        connection.request('POST', '/v1/responses', json.dumps({'model': args.model, 'stream': False,
                           'store': False, 'max_output_tokens': 128, **payload}),
                           {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                            'User-Agent': 'codex_cli_rs/0.147.0', 'X-Hormuz-Routing-Mode': 'local_only',
                            **({'X-Hormuz-Work-Id': work_id} if work_id else {})})
        transport_socket = connection.sock
        with connection.getresponse() as response:
            # Bounded total read time even if a peer drip-feeds bytes.
            data = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CapabilityError('qualification_timeout')
                if transport_socket:
                    transport_socket.settimeout(min(3, remaining))
                block = response.read1(min(65536, MAX_RESPONSE + 1 - len(data)))
                if not block:
                    break
                data.extend(block)
                if len(data) > MAX_RESPONSE:
                    raise CapabilityError('response_limit')
            if response.status != 200:
                raise CapabilityError('router_request_rejected')
            if response.getheader('X-Hormuz-Destination-Locality') != 'local':
                raise CapabilityError('local_execution_unverified')
            if response.getheader('X-Hormuz-Routed-Model') != config.model_routes[args.model].upstream_model:
                raise CapabilityError('model_selection_changed')
            last_work_id = response.getheader('X-Hormuz-Work-Id')
            value = json.loads(data)
            if not isinstance(value, dict) or value.get('status') != 'completed':
                raise CapabilityError('response_incomplete')
            return value
    try:
        result = submit({'input': 'Summarize this fixture using only its number: There are seven red boxes. Reply with the digit only.'})
        checks.append({'name': 'task_quality', 'passed': _text(result).strip() == '7'})
        result = submit({'input': 'Extract the box count: There are seven red boxes. Return JSON with count 7.',
                         'text': {'format': {'type': 'json_schema', 'name': 'box_count', 'strict': True,
                                  'schema': {'type': 'object', 'properties': {'count': {'type': 'integer'}},
                                             'required': ['count'], 'additionalProperties': False}}}})
        try:
            structured = json.loads(_text(result))
        except ValueError:
            structured = None
        checks.append({'name': 'structured_output', 'passed': structured == {'count': 7}})
        tool = {'type': 'function', 'name': 'lookup_count',
                'parameters': {'type': 'object', 'properties': {'item': {'type': 'string'}},
                               'required': ['item'], 'additionalProperties': False}, 'strict': True}
        messages = [{'role': 'user', 'content': 'Summarize this fixture using lookup_count for item boxes; do not guess the count.'}]
        result = submit({'input': messages, 'tools': [tool],
                         'tool_choice': {'type': 'function', 'name': 'lookup_count'}})
        calls = [item for item in result.get('output', []) if isinstance(item, dict) and item.get('type') == 'function_call']
        valid = False
        if len(calls) == 1:
            call = calls[0]
            try:
                valid = (call.get('name') == 'lookup_count' and json.loads(call.get('arguments', '')) == {'item': 'boxes'}
                         and isinstance(call.get('call_id'), str) and 0 < len(call['call_id']) <= 128)
            except (ValueError, TypeError):
                pass
        checks.append({'name': 'tool_call', 'passed': valid})
        if valid:
            result = submit({'input': [*messages, calls[0], {'type': 'function_call_output',
                             'call_id': calls[0]['call_id'], 'output': '7'},
                             {'role': 'user', 'content': 'Reply with the digit only.'}], 'tools': [tool]}, work_id=last_work_id)
            checks.append({'name': 'tool_result', 'passed': _text(result).strip() == '7'})
        else:
            checks.append({'name': 'tool_result', 'passed': False})
    except (OSError, http.client.HTTPException, ValueError, TypeError, KeyError) as error:
        checks.append({'name': 'transport', 'passed': False,
                       'code': error.code if isinstance(error, CapabilityError) else 'probe_unavailable'})
    finally:
        connection.close()
    # Any setup mutation during the probes invalidates the result.
    if setup != _setup(args, config):
        checks.append({'name': 'setup_unchanged', 'passed': False})
    passed = len(checks) == 4 and all(item['passed'] for item in checks)
    report = {'schema_id': 'hormuz.model-qualification', 'schema_version': 1,
              'result': 'passed' if passed else 'failed', 'checks': checks,
              'setup': setup, 'recorded_at': int(time.time()), 'expires_at': int(time.time()) + 7 * 86400,
              'scope': 'synthetic_box_count_envelope_only', 'route_authorized': False,
              'runtime_binding': 'user_supplied_assets_not_endpoint_attestation',
              'limits': {'maximum_requests': 4, 'maximum_output_tokens_per_request': 128,
                         'probe_deadline_seconds': 12, 'retries': 0}}
    export_private(output, json.dumps(report, sort_keys=True, allow_nan=False).encode())
    return report


def recommend(args):
    config = GatewayConfig.load(args.config)
    setup = _setup(args, config)
    raw = read_private(local_path(Path(args.qualification)))
    if raw is None:
        raise CapabilityError('qualification_unavailable')
    report = json.loads(raw)
    if not isinstance(report, dict) or report.get('schema_id') != 'hormuz.model-qualification' or report.get('schema_version') != 1:
        raise CapabilityError('qualification_invalid')
    current = (report.get('setup') == setup and type(report.get('expires_at')) is int
               and report['expires_at'] > time.time() and report.get('result') == 'passed')
    try:
        memory = os.sysconf('SC_PHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
    except (ValueError, OSError):
        memory = None
    # File-size fit is only a lower bound; do not claim inference memory fit.
    return {'schema_id': 'hormuz.model-recommendation', 'schema_version': 1,
            'model_alias': args.model, 'recommendation': 'consider_for_tested_envelope' if current else 'requalify',
            'evidence_current': current, 'scope': 'synthetic_box_count_envelope_only',
            'system_memory_bytes': memory, 'model_file_bytes': setup['model_bytes'],
            'inference_memory_fit': 'unverified', 'route_authorized': False,
            'downloads_started': False, 'evidence_trust': 'user_owned_local_report'}
