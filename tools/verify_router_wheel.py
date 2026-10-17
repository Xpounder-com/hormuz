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
                    'hormuz/local-setup-codex-0160-llama-b11617-qwen3-4b-v1.json'}
        if not required <= set(names):
            raise SystemExit('Wheel is missing Codex 0.160.0 compatibility assets')
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
        # The maintainer CLI is tested as a source tool, never shipped in the wheel.
        (temporary / 'tools').mkdir()
        shutil.copy2(ROOT / 'tools/version.py', temporary / 'tools/version.py')
        subprocess.run([sys.executable, '-c',
                        'from pathlib import Path; import hormuz; '
                        'assert Path(hormuz.__file__).resolve().is_relative_to(Path("installed").resolve())'],
                       cwd=temporary, env=environment, check=True, timeout=10)
        subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests',
                        '-p', 'test_*workflow.py', '-f', '-v'],
                       # The 392-test installed CI suite passed in 1046 seconds.
                       # Additional benchmark and managed-runtime cases need
                       # a finite twenty-minute budget, leaving
                       # two minutes for build, artifacts and job cleanup.
                       cwd=temporary, env=environment, check=True, timeout=1200)
    print('Installed-wheel workflow passed outside the source checkout')


if __name__ == '__main__':
    main()
