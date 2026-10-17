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
        required = {'hormuz/codex-0160-base-instructions.json', 'hormuz/codex-0160-LICENSE.txt',
                    'hormuz/local-setup-codex-0160-llama-b11617-qwen3-4b-v1.json',
                    'hormuz/local-setup-codex-app-0162-llama-b11617-qwen3-4b-v3.json'}
        if not required <= set(names):
            raise SystemExit('Wheel is missing Codex CLI/app compatibility assets')
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
        subprocess.run([sys.executable, '-c',
                        'from pathlib import Path; import hormuz; '
                        'assert Path(hormuz.__file__).resolve().is_relative_to(Path("installed").resolve())'],
                       cwd=temporary, env=environment, check=True, timeout=10)
        subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests',
                        '-p', 'test_*workflow.py', '-f', '-v'],
                       # Main's 408-test installed job took 1118 seconds.
                       # The expanded 453-test suite retains every case within
                       # a finite twenty-five-minute budget, leaving
                       # two minutes for build, artifacts and job cleanup.
                       cwd=temporary, env=environment, check=True, timeout=1500)
    print('Installed-wheel workflow passed outside the source checkout')


if __name__ == '__main__':
    main()
