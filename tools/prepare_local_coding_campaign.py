"""Offline campaign preparation; never starts Codex, llama.cpp, or model inference."""
import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'docs/campaigns/local-repair-20261016-v3/manifest.json'
FAMILIES = {'arithmetic', 'text', 'collections', 'validation_state'}
CHECKER = '''import runpy, sys
oracle = sys.argv.pop(1)
sys.argv[0] = oracle
try:
    runpy.run_path(oracle, run_name="__main__")
except AssertionError:
    sys.exit(1)
except BaseException:
    sys.exit(2)
'''


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    data = path.read_bytes()
    if len(data) > 1024 * 1024:
        raise ValueError('Preparation input exceeds one MiB')
    return json.loads(data)


def read_bound(record):
    path = (ROOT / record['path']).resolve()
    if not path.is_relative_to(ROOT) or digest(path.read_bytes()) != record['sha256']:
        raise ValueError('Frozen preparation input changed: ' + record['path'])
    return read_json(path)


def check(oracle, solution, directory):
    try:
        process = subprocess.run([sys.executable, '-I', '-B', '-c', CHECKER, str(oracle), str(solution)],
                                 cwd=directory, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 env={'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8', 'HOME': str(directory)},
                                 timeout=2)
        return {'exit_code': process.returncode,
                'assertion_failure': process.returncode == 1,
                'termination': 'completed'}
    except subprocess.TimeoutExpired:
        return {'exit_code': None, 'assertion_failure': False, 'termination': 'timeout'}


def prepare(output, reference_directory=None):
    manifest = read_json(MANIFEST)
    candidate = read_bound(manifest['candidate'])
    profile = read_bound(manifest['setup_profile'])
    corpus = read_bound(manifest['corpus'])
    if profile.get('coding_evidence') is not None or candidate['status'] != 'experimental_unqualified':
        raise ValueError('Preparation must not assert coding qualification')
    rows = corpus['entries']
    if len(rows) != 16 or manifest['inventory'] != [
            {key: row[key] for key in ('case_id', 'partition', 'family', 'task_sha256', 'checks_sha256')}
            for row in rows]:
        raise ValueError('Frozen campaign inventory changed')
    distribution = Counter((row['partition'], row['family']) for row in rows)
    expected = {(partition, family): number for family in FAMILIES
                for partition, number in (('development', 1), ('held_out', 3))}
    if dict(distribution) != expected or len({row['case_id'] for row in rows}) != 16:
        raise ValueError('Campaign needs one development and three held-out cases per family')
    if len({row['task_sha256'] for row in rows}) != 16:
        raise ValueError('Campaign reuses a task')
    for retained in manifest['retained_evidence']:
        if digest((ROOT / retained['path']).read_bytes()) != retained['sha256']:
            raise ValueError('Historical evidence changed: ' + retained['path'])
    for row in rows:
        if type(row['case_id']) is not str or re.fullmatch(r'[a-z0-9-]{1,64}', row['case_id']) is None:
            raise ValueError('Invalid case identifier')
        if set(row['files']) != {'solution.py'}:
            raise ValueError('Only the declared solution.py may be materialized')
        if max(len(row['checks'].encode()), len(row['files']['solution.py'].encode())) > 16 * 1024:
            raise ValueError('Case source exceeds sixteen KiB')
        task = json.dumps({'goal': row['goal'], 'files': row['files']}, sort_keys=True,
                          separators=(',', ':'), ensure_ascii=False).encode()
        if digest(task) != row['task_sha256'] or digest(row['checks'].encode()) != row['checks_sha256']:
            raise ValueError('Task or checker digest mismatch: ' + row['case_id'])
        ast.parse(row['files']['solution.py'])
        ast.parse(row['checks'])
    output = output.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    report = {'campaign_id': manifest['campaign_id'], 'candidate_id': candidate['candidate_id'],
              'manifest_sha256': digest(MANIFEST.read_bytes()),
              'corpus_sha256': manifest['corpus']['sha256'], 'python_version': sys.version.split()[0],
              'stage': 'offline_preparation', 'native_episodes_performed': 0,
              'coding_receipt_emitted': False, 'automatic_admission_effect': 'none',
              'request_profile_binding': 'pending native preflight',
              'native_start_conditions': manifest['native_start_conditions'], 'cases': []}
    for row in rows:
        directory = output / row['case_id']
        project = directory / 'project'
        project.mkdir(mode=0o700, parents=True)
        solution = project / 'solution.py'
        solution.write_text(row['files']['solution.py'])
        solution.chmod(0o600)
        oracle = directory / 'oracle.py'
        oracle.write_text(row['checks'])
        oracle.chmod(0o444)
        result = {'case_id': row['case_id'], 'partition': row['partition'], 'family': row['family'],
                  'checks_sha256': row['checks_sha256'], 'baseline': check(oracle, solution, project)}
        if reference_directory is not None:
            reference = reference_directory.resolve() / (row['case_id'] + '.py')
            result['reference_sha256'] = digest(reference.read_bytes())
            result['reference'] = check(oracle, reference, project)
        result['checker_unchanged'] = digest(oracle.read_bytes()) == row['checks_sha256']
        result['prepared'] = (result['baseline']['assertion_failure'] and result['checker_unchanged']
                              and (reference_directory is None or result['reference']['exit_code'] == 0))
        report['cases'].append(result)
    report['passed'] = all(row['prepared'] for row in report['cases'])
    report['baseline_assertion_failures'] = sum(row['baseline']['assertion_failure'] for row in report['cases'])
    report['reference_checks_passed'] = (None if reference_directory is None else
                                        sum(row['reference']['exit_code'] == 0 for row in report['cases']))
    path = output / 'preparation.json'
    path.write_text(json.dumps(report, indent=2) + '\n')
    path.chmod(0o600)
    return report, path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New private preparation directory')
    parser.add_argument('--reference-directory', type=Path,
                        help='Optional independent implementations kept outside model workspaces')
    args = parser.parse_args()
    try:
        report, path = prepare(args.output, args.reference_directory)
    except (OSError, ValueError, KeyError, SyntaxError) as error:
        print(json.dumps({'error': 'campaign_preparation_failed', 'detail': str(error)}), file=sys.stderr)
        return 2
    print(json.dumps({'passed': report['passed'], 'report': str(path),
                      'baseline_assertion_failures': report['baseline_assertion_failures'],
                      'reference_checks_passed': report['reference_checks_passed'],
                      'native_episodes_performed': 0, 'coding_receipt_emitted': False}, indent=2))
    return 0 if report['passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
