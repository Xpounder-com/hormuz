"""Run the approved public workflow against an installed wheel outside the source tree."""
from pathlib import Path
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wheel', type=Path)
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        required = {'koto/__init__.py', 'koto/__main__.py',
                    'hormuz/codex-0160-base-instructions.json', 'hormuz/codex-0160-LICENSE.txt',
                    'hormuz/local-setup-codex-0160-llama-b11617-qwen3-4b-v1.json',
                    'hormuz/local-setup-codex-app-0162-llama-b11617-qwen3-4b-v3.json'}
        if not required <= set(names):
            raise SystemExit('Wheel is missing Koto entry points or Codex CLI/app compatibility assets')
        if any('/migrations/postgresql/' in name or name.startswith(('clients/', 'apps/', 'deploy/'))
               or name.endswith('.css') or name in {'hormuz/postgres.py', 'hormuz/work_billing.py',
               'hormuz/hosted.py', 'hormuz/commands/agent.py'} for name in names):
            raise SystemExit('Wheel contains retired product material')
    with tempfile.TemporaryDirectory(prefix='hormuz-wheel-workflow-') as directory:
        temporary = Path(directory)
        site = temporary / 'installed'
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-index', '--no-deps', '--target',
                        str(site), str(wheel)], check=True, timeout=60)
        environment = {**os.environ, 'PYTHONPATH': str(site), 'PYTHONDONTWRITEBYTECODE': '1'}
        shutil.copytree(ROOT / 'tests', temporary / 'tests', ignore=shutil.ignore_patterns('__pycache__'))
        # Maintainer CLIs are tested as source tools, never shipped in the wheel.
        (temporary / 'tools').mkdir()
        for name in ('version.py', 'verify_router_scope.py'):
            shutil.copy2(ROOT / 'tools' / name, temporary / 'tools' / name)
        subprocess.run([sys.executable, '-c',
                        'from pathlib import Path; import koto, hormuz; '
                        'assert koto.GatewayConfig is hormuz.GatewayConfig; '
                        'assert all(Path(module.__file__).resolve().is_relative_to(Path("installed").resolve()) '
                        'for module in (koto, hormuz))'],
                       cwd=temporary, env=environment, check=True, timeout=10)
        scripts = site / ('Scripts' if os.name == 'nt' else 'bin')
        for name in ('koto', 'hormuz'):
            executable = scripts / (name + '.exe' if os.name == 'nt' else name)
            help_result = subprocess.run([str(executable), '--help'], cwd=temporary, env=environment,
                                         capture_output=True, text=True, check=True, timeout=10)
            if 'usage: koto ' not in help_result.stdout:
                raise SystemExit('Installed CLI does not expose the Koto identity: ' + name)
        # unittest timings include setup and cleanup. Python 3.11 runs the same
        # complete suite without the duration flag introduced in Python 3.12.
        durations = ['--durations', '20'] if sys.version_info >= (3, 12) else []
        subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests',
                        '-p', 'test_*workflow.py', '-f', '-v', *durations],
                       # Retain main's finite twenty-five-minute suite budget,
                       # leaving two minutes for installation and job cleanup.
                       cwd=temporary, env=environment, check=True, timeout=1500)
    print('Installed-wheel workflow passed outside the source checkout')


if __name__ == '__main__':
    main()
