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
        subprocess.run([sys.executable, '-c',
                        'from pathlib import Path; import hormuz; '
                        'assert Path(hormuz.__file__).resolve().is_relative_to(Path("installed").resolve())'],
                       cwd=temporary, env=environment, check=True, timeout=10)
        # unittest timings include setup and cleanup. Python 3.11 runs the same
        # complete suite without the duration flag introduced in Python 3.12.
        durations = ['--durations', '20'] if sys.version_info >= (3, 12) else []
        for pattern, timeout in (('test_*fast.py', 60), ('test_*workflow.py', 1200)):
            print(f'Running installed-wheel tests: {pattern}', flush=True)
            subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests',
                            '-p', pattern, '-f', '-v', *durations],
                           # Both layers must pass; retain a finite deadline for
                           # each and the encompassing CI job's 22-minute cap.
                           cwd=temporary, env=environment, check=True, timeout=timeout)
    print('Installed-wheel fast tests and workflows passed outside the source checkout')


if __name__ == '__main__':
    main()
