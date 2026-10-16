"""Bounded, content-free inspection of pinned Codex configuration precedence.

Never executes launch arguments, reads auth caches, or changes project trust.
The result describes selected files and supplied flags, not a running session.
"""
import copy
import json
import re
from pathlib import Path
import tomllib

from .connected_client import ConnectedClientError, _read
from .client_versions import SUPPORTED_CODEX_VERSION

ROUTING_KEYS = ('model', 'model_provider', 'model_providers', 'web_search', 'openai_base_url')
PROJECT_DENIED = frozenset({'openai_base_url', 'chatgpt_base_url', 'apps_mcp_product_sku',
                          'model_provider', 'model_providers', 'notify', 'profile', 'profiles',
                          'experimental_realtime_webrtc_call_base_url', 'experimental_realtime_ws_base_url', 'otel'})


class OverrideInspectionError(ValueError):
    def __init__(self, source):
        self.source = source
        super().__init__('override_inspection_unavailable')


def _merge(base, layer):
    for key, value in layer.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)


def _document(path):
    text = _read(path)
    return tomllib.loads(text) if text is not None else {}


def _root(project, config):
    ancestors = [project, *project.parents]
    if len(ancestors) > 64:
        raise ValueError('inspection_limit')
    markers = config.get('project_root_markers', ['.git'])
    if (not isinstance(markers, list) or len(markers) > 16 or
            any(not isinstance(item, str) or not item or len(item) > 128
                or Path(item).is_absolute() or '..' in Path(item).parts for item in markers)):
        raise ValueError('markers_invalid')
    return next((directory for directory in ancestors if any((directory / marker).exists() for marker in markers)), project)


def _routing(config):
    provider = config.get('model_provider', 'openai')
    providers = config.get('model_providers', {})
    if (not isinstance(provider, str) or not provider or not isinstance(providers, dict)
            or not isinstance(providers.get(provider, {}), dict)
            or config.get('model') is not None and not isinstance(config['model'], str)):
        raise ValueError('routing_invalid')
    return {'model': config.get('model'), 'model_provider': provider,
            'model_providers': providers.get(provider, {}), 'web_search': config.get('web_search'),
            'openai_base_url': config.get('openai_base_url') if provider == 'openai' else None}


def _launch(arguments, client_version):
    if arguments is None:
        return {}, {}, None, None, False, False
    if len(arguments) > 128 or sum(len(value.encode()) for value in arguments) > 16384:
        raise ValueError('launch_limit')
    generic, dedicated = {}, {}
    profile, cwd, remote, oss, local_provider = None, None, False, False, None
    ignore_user = False
    index = 0
    takes_value = {'-m': 'model', '--model': 'model', '-p': 'profile', '--profile': 'profile',
                   '-C': 'cwd', '--cd': 'cwd', '-c': 'config', '--config': 'config',
                   '--local-provider': 'local_provider', '--remote': 'remote',
                   '--sandbox': 'ignore', '-s': 'ignore', '--ask-for-approval': 'ignore',
                   '-a': 'ignore', '--enable': 'ignore', '--disable': 'ignore',
                   '--remote-auth-token-env': 'ignore', '--add-dir': 'ignore',
                   '--image': 'ignore', '-i': 'ignore', '--output-last-message': 'ignore', '-o': 'ignore'}
    switches = {'--strict-config', '--no-alt-screen', '--full-auto',
                '--approve-for-me', '--dangerously-bypass-approvals-and-sandbox',
                '--dangerously-bypass-hook-trust', '--skip-git-repo-check', '--json', '--ephemeral'}
    while index < len(arguments):
        token = arguments[index]
        index += 1
        if token == '--':
            break
        if token in {'exec', 'e', 'resume', 'review'}:
            continue
        if token == '--ignore-user-config' and client_version == '0.160.0':
            ignore_user = True
            continue
        if token == '--ignore-rules' and client_version == '0.160.0':
            continue
        if token == '--search':
            dedicated['web_search'] = 'live'
            continue
        if token == '--oss':
            oss = True
            continue
        if token in switches:
            continue
        flag, separator, inline = token.partition('=')
        if not separator and token[:2] in {'-m', '-c', '-p', '-C'} and len(token) > 2:
            flag, separator, inline = token[:2], '=', token[2:]
        if flag not in takes_value:
            raise ValueError('unsupported_launch_flag')
        if separator:
            value = inline
        elif index < len(arguments):
            value = arguments[index]
            index += 1
        else:
            raise ValueError('launch_value_missing')
        if not value:
            raise ValueError('launch_value_missing')
        kind = takes_value[flag]
        if kind == 'config':
            key, equal, raw = value.partition('=')
            if not equal:
                raise ValueError('launch_config_invalid')
            try:
                parsed = tomllib.loads('_value_=' + raw)
                if set(parsed) != {'_value_'}:
                    raise ValueError('not_a_value')
                parsed_value = parsed['_value_']
            except ValueError:
                parsed_value = raw
            segments = key.strip().split('.')
            if not all(segments):
                raise ValueError('launch_config_invalid')
            cursor = generic
            for segment in segments[:-1]:
                if not isinstance(cursor.get(segment), dict):
                    cursor[segment] = {}
                cursor = cursor[segment]
            cursor[segments[-1]] = parsed_value
        elif kind == 'model':
            dedicated['model'] = value
        elif kind == 'profile':
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value):
                raise ValueError('profile_invalid')
            profile = value
        elif kind == 'cwd':
            cwd = value
        elif kind == 'remote':
            remote = True
        elif kind == 'local_provider':
            local_provider = value
    if oss:
        if local_provider is not None and local_provider not in {'ollama', 'lmstudio'}:
            raise ValueError('local_provider_invalid')
        dedicated['model_provider'] = local_provider or 'oss_default_unresolved'
    return generic, dedicated, profile, cwd, remote, ignore_user


def _repository_root(project):
    root = next((directory for directory in [project, *project.parents] if (directory / '.git').exists()), project)
    git = root / '.git'
    if not git.is_file():
        return root
    text = _read(git)
    if text is None or not text.startswith('gitdir: '):
        return root
    directory = (root / text[8:].strip()).resolve()
    if directory.parent.name != 'worktrees':
        return root
    common = _read(directory / 'commondir')
    return (directory / common.strip()).resolve().parent if common else root


def inspect_overrides(home, project, arguments=None, *, client_version=SUPPORTED_CODEX_VERSION):
    """Inspect selected files/flags only; values stay in this local calculation."""
    baseline = _document(home / 'config.toml')
    try:
        generic, dedicated, profile, cwd, remote, ignore_user = _launch(arguments, client_version)
    except (ValueError, RecursionError):
        raise OverrideInspectionError('launch') from None
    if cwd:
        project = (project / Path(cwd).expanduser()).resolve()
    if not project.is_dir():
        raise ValueError('project_unavailable')
    effective = {} if ignore_user else copy.deepcopy(baseline)
    layers, owners = [], {key: 'launch' for key in ROUTING_KEYS} if ignore_user else {}
    if profile and not ignore_user:
        legacy = baseline.get('profiles', {})
        if baseline.get('profile') == profile or isinstance(legacy, dict) and profile in legacy:
            raise OverrideInspectionError('profile')
        profile_path = home / (profile + '.config.toml')
        if not profile_path.exists():
            raise OverrideInspectionError('profile')
        try:
            document = _document(profile_path)
        except (ValueError, OSError, RecursionError, ConnectedClientError):
            raise OverrideInspectionError('profile') from None
        _merge(effective, document)
        keys = sorted(set(document) & set(ROUTING_KEYS))
        owners.update({key: 'profile' for key in keys})
        layers.append({'source': 'profile', 'state': 'applied', 'keys': keys, 'ignored_keys': []})
    context = copy.deepcopy(effective)
    _merge(context, generic)
    project_denied = PROJECT_DENIED | {'responses_api_metadata'} if client_version == '0.160.0' else PROJECT_DENIED
    root = _root(project, context)
    repository = _repository_root(project)
    directories = [project, *project.parents]
    directories = list(reversed(directories[:directories.index(root) + 1]))
    projects = context.get('projects', {})
    if not isinstance(projects, dict):
        raise ValueError('trust_invalid')
    for index, directory in enumerate(directories):
        path = directory / '.codex/config.toml'
        if not path.parent.exists() and not path.parent.is_symlink():
            continue
        # Optional files may be absent, but a present directory alias must
        # resolve. Non-strict resolution hides symlink loops on Python 3.13+.
        parent = path.parent.resolve(strict=True)
        if not parent.is_dir():
            raise ValueError('project_configuration_directory_invalid')
        if parent == home.resolve() or not path.exists() and not path.is_symlink():
            continue
        lookup = [key for candidate in (directory, root, repository)
                  for key in (str(candidate.resolve()), str(candidate))]
        trust = next((projects[key].get('trust_level') for key in lookup
                      if isinstance(projects.get(key), dict) and 'trust_level' in projects[key]), None)
        if trust is not None and (not isinstance(trust, str) or trust not in {'trusted', 'untrusted'}):
            raise ValueError('trust_invalid')
        if trust != 'trusted':
            layers.append({'source': 'project', 'depth': index, 'state': 'inactive',
                           'trust': 'untrusted' if trust == 'untrusted' else 'not_granted',
                           'keys': [], 'ignored_keys': []})
            continue
        try:
            document = _document(path)
        except (ValueError, OSError, RecursionError, ConnectedClientError):
            raise OverrideInspectionError('project') from None
        ignored = sorted(set(document) & project_denied)
        keys = sorted(set(document) & set(ROUTING_KEYS) - project_denied)
        layer = {key: value for key, value in document.items() if key not in project_denied}
        _merge(effective, layer)
        for key in keys:
            owners[key] = 'project'
        layers.append({'source': 'project', 'depth': index, 'state': 'applied', 'trust': 'trusted',
                       'keys': keys, 'ignored_keys': ignored})
    for layer in (generic, dedicated):
        _merge(effective, layer)
        owners.update({key: 'launch' for key in set(layer) & set(ROUTING_KEYS)})
    if 'profile' in effective:
        # Pinned Codex rejects this legacy selector even when --profile differs.
        raise OverrideInspectionError('launch' if 'profile' in generic else 'profile')
    expected, selected = _routing(baseline), _routing(effective)
    conflicts = [{'source': owners.get('model_provider', owners.get(key, 'user')) if key in {'model_providers', 'openai_base_url'} and selected['model_provider'] != expected['model_provider'] else owners.get(key, 'user'), 'key': key} for key in ROUTING_KEYS if selected[key] != expected[key]]
    project_layers = [item for item in layers if item['source'] == 'project']
    state = ('detected' if any(item['source'] == 'project' for item in conflicts)
             else 'ignored' if any(item['ignored_keys'] for item in project_layers)
             else 'inactive' if project_layers and all(item['state'] == 'inactive' for item in project_layers)
             else 'none')
    return {'schema_id': 'hormuz.client-overrides', 'schema_version': 1,
            'supported_client_version': client_version,
            'scope': 'selected_project_and_supplied_launch', 'project_state': state,
            'layers': layers, 'conflicts': conflicts,
            'launch_state': 'not_supplied' if arguments is None else 'remote' if remote else 'detected' if any(item['source'] == 'launch' for item in conflicts) else 'none',
            'effective_routing': {key: 'unobserved' if remote or selected.get('model_provider') == 'oss_default_unresolved' or key == 'model' and 'model_provider' in dedicated and 'model' not in dedicated else 'overridden' if selected[key] != expected[key] else 'matches' for key in ROUTING_KEYS},
            'running_session': 'unobserved'}
