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
                        '-p', 'test_*workflow.py', '-f', '-q'],
                       # The expanded suite takes about 491s on CI. Leave a
                       # bounded margin inside the ten-minute packaging job.
                       cwd=temporary, env=environment, check=True, timeout=540)
    print('Installed-wheel workflow passed outside the source checkout')


if __name__ == '__main__':
    main()
