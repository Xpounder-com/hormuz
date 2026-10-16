"""Disable the app routing overlay before retiring its isolated router owner."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys
import tomllib

from .codex_app_bridge import Bridge, read_manifest
from .connected_client import ConnectedClientError, ConnectedClientLifetime


def install_bridge(home, args):
    from .contracts import validate_contract
    home = home.expanduser().resolve()
    control = Path(args.router_home).expanduser().resolve()
    directory = Path(args.directory).expanduser().absolute()
    native = Path(args.executable).expanduser().resolve()
    launchctl = Path(args.launchctl).expanduser().resolve()
    if (not getattr(args, 'isolated_test', False) or launchctl == Path('/bin/launchctl').resolve()
            or home == (Path.home() / '.codex').resolve()):
        raise ConnectedClientError('app_activation_disabled')
    if home == control or home.is_symlink() or directory.is_symlink():
        raise ConnectedClientError('directory_unsafe')
    status = ConnectedClientLifetime(control).status()
    version = subprocess.run([str(native), '--version'], capture_output=True, text=True, check=True, timeout=6).stdout.strip()
    if version != 'codex-cli 0.162.0-alpha.17.2' or status.get('client_version') != '0.162.0-alpha.17.2':
        raise ConnectedClientError('version_unsupported')
    if status['phase'] != 'connected' or status['router'] != 'ready' or status['configuration'] != 'installed':
        raise ConnectedClientError('router_unavailable')
    original = (home / 'config.toml').read_bytes()
    settings = tomllib.loads(original.decode())
    local = tomllib.loads((control / 'config.toml').read_text())
    if settings.get('model_provider', '').startswith('hormuz_') or settings.get('model') == local['model']:
        raise ConnectedClientError('configuration_changed')
    current = subprocess.run([str(launchctl), 'getenv', 'CODEX_CLI_PATH'], capture_output=True, text=True, timeout=4)
    if current.returncode not in {0, 1}:
        raise ConnectedClientError('client_unavailable')
    previous = current.stdout.strip() if current.returncode == 0 else None
    with native.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    executable = directory / 'codex-app-bridge'
    receipt = {'schema_id': 'hormuz.codex-app-bridge-install', 'schema_version': 1,
        'phase': 'preview' if args.dry_run else 'enabled', 'client_home': str(home), 'control_home': str(control),
        'native_version': '0.162.0-alpha.17.2', 'native_sha256': digest, 'bridge_executable': str(executable),
        'router_generation': status['generation'], 'normal_settings': 'unchanged', 'effective_on_next_app_launch': True}
    validate_contract(receipt)
    manifest_path = home / 'hormuz-app-bridge-v1.json'
    activation_path = home / 'hormuz-app-activation-v1.json'
    if directory.exists():
        if manifest_path.is_file() and activation_path.is_file():
            existing = read_manifest(manifest_path, verify_native=False)
            activation = json.loads(activation_path.read_text())
            if (existing['enabled'] and existing['control_home'] == str(control)
                    and existing['native_sha256'] == digest and activation.get('phase') == 'enabled'
                    and activation.get('bridge_executable') == str(executable) and previous == str(executable)):
                return receipt
        raise ConnectedClientError('setup_changed')
    if args.dry_run:
        return receipt
    directory.mkdir(mode=0o700)
    manifest = {'schema_id': 'hormuz.codex-app-bridge', 'schema_version': 1, 'enabled': False,
        'client_home': str(home), 'control_home': str(control), 'native_executable': str(native),
        'native_sha256': digest, 'native_version': '0.162.0-alpha.17.2', 'catalog_path': str(directory / 'combined-models.json')}
    activation = {'schema_id': 'hormuz.codex-app-activation', 'schema_version': 1, 'phase': 'prepared',
        'client_home': str(home), 'control_home': str(control), 'bridge_executable': str(executable),
        'previous_cli_path': previous, 'launchctl': str(launchctl)}
    Bridge.write_document(manifest_path, manifest)
    Bridge.write_document(activation_path, activation)
    launcher = ('#!' + sys.executable + '\nimport os,sys\nfrom pathlib import Path\n'
        + 'native=' + repr(str(native)) + '\narguments=sys.argv[1:]\n'
        + 'if "app-server" not in arguments: os.execv(native,[native,*arguments])\n'
        + 'try:\n from hormuz.codex_app_bridge import read_manifest,Bridge,main\n'
        + ' manifest=read_manifest(Path(' + repr(str(manifest_path)) + '))\n'
        + ' if not manifest["enabled"]: os.execv(native,[native,*arguments])\n'
        + ' Bridge(manifest,arguments)\n'
        + 'except (ImportError,OSError,ValueError,KeyError):\n os.execv(native,[native,*arguments])\n'
        + 'raise SystemExit(main(["--manifest",' + repr(str(manifest_path)) + ',*arguments]))\n')
    descriptor = os.open(executable, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o700)
    with os.fdopen(descriptor, 'w') as output:
        output.write(launcher)
    try:
        Bridge.write_document(manifest_path, {**manifest, 'enabled': True})
        subprocess.run([str(launchctl), 'setenv', 'CODEX_CLI_PATH', str(executable)], capture_output=True, check=True, timeout=4)
        if (home / 'config.toml').read_bytes() != original:
            raise ConnectedClientError('configuration_changed')
        Bridge.write_document(activation_path, {**activation, 'phase': 'enabled'})
    except (OSError, subprocess.SubprocessError, ConnectedClientError):
        Bridge.write_document(manifest_path, {**manifest, 'enabled': False})
        restore_launch_environment(home, manifest)
        raise
    return receipt


def restore_launch_environment(home, manifest):
    path = home / 'hormuz-app-activation-v1.json'
    if not path.exists():
        return
    metadata = path.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077 or metadata.st_size > 128 * 1024):
        raise ConnectedClientError('state_invalid')
    value = json.loads(path.read_text())
    if (set(value) != {'schema_id', 'schema_version', 'phase', 'client_home', 'control_home',
                      'bridge_executable', 'previous_cli_path', 'launchctl'}
            or value['schema_id'] != 'hormuz.codex-app-activation' or value['schema_version'] != 1
            or value['client_home'] != str(home) or value['control_home'] != manifest['control_home']
            or value['phase'] not in {'prepared', 'enabled', 'disabled'}
            or not isinstance(value['bridge_executable'], str) or not Path(value['bridge_executable']).is_absolute()
            or not isinstance(value['launchctl'], str) or not Path(value['launchctl']).is_absolute()
            or value['previous_cli_path'] is not None and (not isinstance(value['previous_cli_path'], str)
                or len(value['previous_cli_path']) > 4096 or '\x00' in value['previous_cli_path'])):
        raise ConnectedClientError('state_invalid')
    current = subprocess.run([value['launchctl'], 'getenv', 'CODEX_CLI_PATH'], capture_output=True, text=True, timeout=4)
    if current.returncode not in {0, 1}:
        raise ConnectedClientError('restoration_unavailable')
    actual = current.stdout.strip() if current.returncode == 0 else None
    if actual == value['bridge_executable']:
        arguments = (['unsetenv', 'CODEX_CLI_PATH'] if value['previous_cli_path'] is None else
                     ['setenv', 'CODEX_CLI_PATH', value['previous_cli_path']])
        subprocess.run([value['launchctl'], *arguments], capture_output=True, check=True, timeout=4)
    value['phase'] = 'disabled'
    Bridge.write_document(path, value)


def disable_bridge(home, *, generation=None):
    home = home.expanduser().resolve()
    path = home / 'hormuz-app-bridge-v1.json'
    if not path.exists() and not path.is_symlink():
        return None
    try:
        manifest = read_manifest(path, verify_native=False)
        if Path(manifest['client_home']).resolve() != home:
            raise ConnectedClientError('state_invalid')
        lifetime = ConnectedClientLifetime(Path(manifest['control_home']))
        status = lifetime.status()
        if generation is not None and status.get('generation') != generation:
            raise ConnectedClientError('generation_mismatch')
        Bridge.write_document(path, {**manifest, 'enabled': False})
        restore_launch_environment(home, manifest)
        return lifetime.disconnect(generation=generation)
    except (ValueError, KeyError, TypeError):
        raise ConnectedClientError('state_invalid') from None
