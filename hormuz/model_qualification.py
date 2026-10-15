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
import re
import stat
import subprocess
import time
from urllib.request import Request

from .local_transport import open_local_upstream

from .config import GatewayConfig
from .connected_client import _qualify_client
from .local_history_files import export_private, local_path, read_private
from .plugin_policy import CapabilityError

SUITE = 'responses-local-smoke-v1'
MAX_RESPONSE = 262144
CHECKS = ('task_quality', 'structured_output', 'tool_call', 'tool_result')
LIMITS = {'maximum_requests': 4, 'maximum_output_tokens_per_request': 128,
          'probe_deadline_seconds': 12, 'retries': 0}
SCOPE = 'synthetic_box_count_envelope_only'
BINDING = 'user_supplied_assets_not_endpoint_attestation'


def _integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def _label(value):
    # Configuration permits model identifiers longer than 256 characters.
    # The private report reader bounds the whole document, so do not impose an
    # unrelated per-string limit that the producer does not enforce.
    return isinstance(value, str) and bool(value.strip())


def _validate_report(report):
    """Validate user-owned evidence before considering whether it is current."""
    required = {'schema_id', 'schema_version', 'result', 'checks', 'setup', 'recorded_at',
                'expires_at', 'scope', 'route_authorized', 'runtime_binding', 'limits'}
    if (not isinstance(report, dict)
            or report['schema_id'] != 'hormuz.model-qualification'
            or type(report['schema_version']) is not int or report['schema_version'] not in (1, 2)
            or set(report) != required | ({'execution'} if report['schema_version'] == 2 else set())
            or report['result'] not in ('passed', 'failed')
            or report['scope'] != SCOPE or report['route_authorized'] is not False
            or report['runtime_binding'] != BINDING
            or not _integer(report['recorded_at'], 0, int(time.time()))
            or not _integer(report['expires_at'], report['recorded_at'] + 1,
                            report['recorded_at'] + 7 * 86400 + 1)):
        raise CapabilityError('qualification_invalid')
    setup = report['setup']
    hashes = {'configuration_sha256', 'client_sha256', 'runtime_sha256', 'model_sha256'}
    if (not isinstance(setup, dict) or set(setup) != hashes | {
            'model_bytes', 'model_alias', 'model_revision', 'system', 'machine', 'suite'}
            or any(not isinstance(setup[key], str) or re.fullmatch('[a-f0-9]{64}', setup[key]) is None
                   for key in hashes)
            or not _integer(setup['model_bytes'], 0, 64 * 1024**3)
            or not all(_label(setup[key]) for key in ('model_alias', 'system', 'machine'))
            or setup['model_revision'] is not None and not _label(setup['model_revision'])
            or setup['suite'] != SUITE):
        raise CapabilityError('qualification_invalid')
    limits = report['limits']
    if (not isinstance(limits, dict) or set(limits) != set(LIMITS)
            or any(type(limits[key]) is not int or limits[key] != value for key, value in LIMITS.items())):
        raise CapabilityError('qualification_invalid')
    checks = report['checks']
    if not isinstance(checks, list) or not 1 <= len(checks) <= 6:
        raise CapabilityError('qualification_invalid')
    names = []
    for check in checks:
        if (not isinstance(check, dict) or set(check) - {'name', 'passed', 'code'}
                or not isinstance(check.get('name'), str) or type(check.get('passed')) is not bool
                or check['name'] in names):
            raise CapabilityError('qualification_invalid')
        name = check['name']
        if name in CHECKS:
            if len(names) >= len(CHECKS) or name != CHECKS[len(names)] or 'code' in check:
                raise CapabilityError('qualification_invalid')
        elif name == 'transport':
            if (check['passed'] is not False or not isinstance(check.get('code'), str)
                    or re.fullmatch('[a-z_]{1,80}', check['code']) is None
                    or 'setup_unchanged' in names):
                raise CapabilityError('qualification_invalid')
        elif name == 'setup_unchanged':
            if check['passed'] is not False or 'code' in check:
                raise CapabilityError('qualification_invalid')
        else:
            raise CapabilityError('qualification_invalid')
        names.append(name)
    complete = names[:4] == list(CHECKS)
    if (not complete and 'transport' not in names
            or report['result'] == 'passed' and not (names == list(CHECKS) and all(check['passed'] for check in checks))
            or report['result'] == 'failed' and all(check['passed'] for check in checks)
            or complete and not checks[2]['passed'] and checks[3]['passed']):
        raise CapabilityError('qualification_invalid')
    if report['schema_version'] == 2:
        execution = report['execution']
        if execution is None and report['result'] == 'failed':
            return
        if (not isinstance(execution, dict) or set(execution) != {
                'model_alias', 'destination', 'generation', 'upstream_model', 'model_revision'}
                or execution['model_alias'] != setup['model_alias']
                or execution['model_revision'] != setup['model_revision']
                or not all(_label(execution[key]) for key in ('destination', 'upstream_model'))
                or not isinstance(execution['generation'], str)
                or re.fullmatch('[a-f0-9]{64}', execution['generation']) is None):
            raise CapabilityError('qualification_invalid')


def _json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise CapabilityError('qualification_invalid')
        value[key] = item
    return value


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


def _identity(args, config):
    actors = list(config.identities_by_actor.values())
    if args.actor:
        actors = [item for item in actors if item.actor_id == args.actor]
    if len(actors) != 1:
        raise CapabilityError('actor_required')
    identity = actors[0]
    policy = config.resolved_policy(identity)
    if (identity.allowed_clients and 'codex' not in identity.allowed_clients
            or policy.allowed_clients is not None and 'codex' not in policy.allowed_clients
            or policy.allowed_models is not None and args.model not in policy.allowed_models):
        raise CapabilityError('model_not_allowed')
    return identity


def _router_token(args, config):
    identity = _identity(args, config)
    token = next((token for token, owner in config.identities_by_token.items()
                  if owner.actor_id == identity.actor_id), None)
    if token is None or config.ingress.mode != 'local':
        raise CapabilityError('direct_static_router_auth_required')
    if config.listen.host not in {'127.0.0.1', '::1', 'localhost'}:
        raise CapabilityError('loopback_router_required')
    return token


def _read_probe(response, deadline):
    data = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CapabilityError('qualification_timeout')
        response.set_read_timeout(min(3, remaining))
        block = response.read1(min(65536, MAX_RESPONSE + 1 - len(data)))
        if not block:
            return data
        data.extend(block)
        if len(data) > MAX_RESPONSE:
            raise CapabilityError('response_limit')


def _router_execution(args, config, token, deadline):
    """Resolve the requested alias from authenticated, live destination status."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CapabilityError('qualification_timeout')
    # The router's strict work endpoint requires the bound numeric Host value.
    host = '127.0.0.1' if config.listen.host == 'localhost' else config.listen.host
    host = '[' + host + ']' if ':' in host else host
    request = Request(f'http://{host}:{config.listen.port}/v1/work/state',
                      headers={'Authorization': 'Bearer ' + token})
    with open_local_upstream(request, timeout=min(3, remaining), absolute_timeout=remaining) as response:
        data = _read_probe(response, deadline)
        if response.status != 200:
            raise CapabilityError('execution_unverified')
    value = json.loads(data)
    if (not isinstance(value, dict) or value.get('schema_id') != 'hormuz.ai-work-state'
            or type(value.get('schema_version')) is not int or value['schema_version'] != 3
            or not isinstance(value.get('connection'), dict)
            or not isinstance(value['connection'].get('routes'), list)):
        raise CapabilityError('execution_unverified')
    routes = [route for route in value['connection']['routes']
              if isinstance(route, dict) and route.get('model') == args.model]
    configured = config.model_routes[args.model]
    if len(routes) != 1:
        raise CapabilityError('execution_unverified')
    route = routes[0]
    if (route.get('destination') != configured.destination_id
            or route.get('upstream_model') != configured.upstream_model
            or route.get('model_revision') != configured.model_revision
            or route.get('protocol') != configured.protocol or route.get('locality') != 'local'
            or route.get('eligible') is not True or not isinstance(route.get('generation'), str)
            or re.fullmatch('[a-f0-9]{64}', route['generation']) is None):
        raise CapabilityError('execution_selection_changed')
    return {'model_alias': args.model, 'destination': route['destination'],
            'generation': route['generation'], 'upstream_model': route['upstream_model'],
            'model_revision': route['model_revision']}


def _memory_bytes():
    try:
        return os.sysconf('SC_PHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
    except (ValueError, OSError):
        if platform.system() == 'Darwin':
            try:
                result = subprocess.run(['/usr/sbin/sysctl', '-n', 'hw.memsize'],
                                        capture_output=True, text=True, timeout=2,
                                        env={'PATH': '/usr/bin:/bin'})
            except (OSError, subprocess.SubprocessError):
                return None
            if result.returncode == 0 and result.stdout.strip().isdigit():
                return int(result.stdout.strip())
        return None


def qualify(args):
    config = GatewayConfig.load(args.config)
    # Shadow classification adds provider calls outside this suite's fixed
    # request envelope. Qualification cannot suppress a selected configuration.
    if config.shadow_router is not None:
        raise CapabilityError('qualification_shadow_router_unsupported')
    setup = _setup(args, config)
    output = local_path(Path(args.output))
    if output.exists() or output.is_symlink():
        raise CapabilityError('output_exists')
    token = _router_token(args, config)
    checks = []
    # One request at a time; at most four requests, no retries or redirects.
    deadline = time.monotonic() + 12
    execution = None
    last_work_id = None
    def submit(payload, *, work_id=None):
        nonlocal last_work_id
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CapabilityError('qualification_timeout')
        host = '[' + config.listen.host + ']' if ':' in config.listen.host else config.listen.host
        request = Request(f'http://{host}:{config.listen.port}/v1/responses',
                          data=json.dumps({'model': args.model, 'stream': False, 'store': False,
                                           'max_output_tokens': 128, **payload}).encode(),
                          headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                                   'User-Agent': 'codex_cli_rs/0.147.0', 'X-Hormuz-Routing-Mode': 'local_only',
                                   **({'X-Hormuz-Work-Id': work_id} if work_id else {})})
        with open_local_upstream(request, timeout=min(3, remaining), absolute_timeout=remaining) as response:
            data = _read_probe(response, deadline)
            if response.status != 200:
                raise CapabilityError('router_request_rejected')
            if response.headers.get('X-Hormuz-Destination-Locality') != 'local':
                raise CapabilityError('local_execution_unverified')
            if response.headers.get('X-Hormuz-Routed-Model') != config.model_routes[args.model].upstream_model:
                raise CapabilityError('model_selection_changed')
            if (response.headers.get('X-Hormuz-Destination') != execution['destination']
                    or response.headers.get('X-Hormuz-Destination-Generation') != execution['generation']
                    or response.headers.get('X-Hormuz-Requested-Model') != args.model):
                raise CapabilityError('execution_selection_changed')
            last_work_id = response.headers.get('X-Hormuz-Work-Id')
            value = json.loads(data)
            if not isinstance(value, dict) or value.get('status') != 'completed':
                raise CapabilityError('response_incomplete')
            return value
    try:
        execution = _router_execution(args, config, token, deadline)
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
        if execution != _router_execution(args, config, token, deadline):
            raise CapabilityError('execution_selection_changed')
    except (OSError, http.client.HTTPException, ValueError, TypeError, KeyError) as error:
        checks.append({'name': 'transport', 'passed': False,
                       'code': error.code if isinstance(error, CapabilityError) else 'probe_unavailable'})
    # Any setup mutation during the probes invalidates the result.
    if setup != _setup(args, config):
        checks.append({'name': 'setup_unchanged', 'passed': False})
    passed = len(checks) == 4 and all(item['passed'] for item in checks)
    recorded_at = int(time.time())
    report = {'schema_id': 'hormuz.model-qualification', 'schema_version': 2,
              'result': 'passed' if passed else 'failed', 'checks': checks,
              'setup': setup, 'execution': execution,
              'recorded_at': recorded_at, 'expires_at': recorded_at + 7 * 86400,
              'scope': SCOPE, 'route_authorized': False,
              'runtime_binding': BINDING, 'limits': dict(LIMITS)}
    export_private(output, json.dumps(report, sort_keys=True, allow_nan=False).encode())
    return report


def recommend(args):
    config = GatewayConfig.load(args.config)
    setup = _setup(args, config)
    _identity(args, config)
    raw = read_private(local_path(Path(args.qualification)))
    if raw is None:
        raise CapabilityError('qualification_unavailable')
    try:
        report = json.loads(raw, object_pairs_hook=_json_object)
        _validate_report(report)
    except (ValueError, TypeError, KeyError, RecursionError):
        raise CapabilityError('qualification_invalid')
    current = (report['schema_version'] == 2 and report['setup'] == setup
               and report['expires_at'] > time.time() and report.get('result') == 'passed')
    if current:
        try:
            token = _router_token(args, config)
            current = report['execution'] == _router_execution(args, config, token, time.monotonic() + 3)
        except (OSError, http.client.HTTPException, ValueError, TypeError, KeyError):
            current = False
    memory = _memory_bytes()
    exceeds_memory = memory is not None and setup["model_bytes"] > memory
    # File-size fit is only a lower bound; do not claim inference memory fit.
    return {'schema_id': 'hormuz.model-recommendation', 'schema_version': 1,
            'model_alias': args.model, 'recommendation': 'model_exceeds_system_memory' if exceeds_memory else 'consider_for_tested_envelope' if current else 'requalify',
            'evidence_current': current, 'scope': 'synthetic_box_count_envelope_only',
            'system_memory_bytes': memory, 'model_file_bytes': setup['model_bytes'],
            'inference_memory_fit': 'file_exceeds_physical_memory' if exceeds_memory else 'unverified', 'route_authorized': False,
            'downloads_started': False, 'evidence_trust': 'user_owned_local_report'}
