"""Static source hygiene for the approved local-router product scope."""
from pathlib import Path
import ast
import importlib
import json
import re
import sys
import tomllib
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    for name in ('apps', 'clients', 'deploy', 'website', 'marketing', 'experiments'):
        if (ROOT / name).exists():
            raise SystemExit('Retired product source: ' + name)
    workflows = sorted(p.name for p in (ROOT / '.github/workflows').glob('*.yml'))
    if workflows != ['ci.yml']:
        raise SystemExit('Unscoped delivery workflow')
    metadata = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    if 'optional-dependencies' in metadata['project']:
        raise SystemExit('Unscoped packaging extra')
    for path in sorted((ROOT / 'hormuz').rglob('*.py')):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                imports = [node.module or '']
            else:
                continue
            if any(re.search(r'(^|\.)(?:postgres\w*|psycopg\w*|jwt|hosted|work_billing)(\.|$)', name) for name in imports):
                raise SystemExit('Retired runtime dependency: ' + str(path.relative_to(ROOT)))
        module = '.'.join(path.relative_to(ROOT).with_suffix('').parts)
        if module.endswith('.__init__'):
            module = module[:-9]
        if module != 'hormuz.__main__':
            importlib.import_module(module)
    from hormuz import __version__
    if __version__ != metadata['project']['version']:
        raise SystemExit('Package version mismatch')
    example = json.loads((ROOT / 'config.example.json').read_text())
    if example['route_admission'] != {'mode': 'local_only', 'local_routes': {}}:
        raise SystemExit('Example must fail closed until real qualification')
    for path in [*ROOT.glob('*.md'), *(ROOT / 'docs').rglob('*.md')]:
        for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', path.read_text()):
            if re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', target) or target.startswith('#'):
                continue
            if not (path.parent / unquote(target.split('#')[0])).exists():
                raise SystemExit(f'Broken local link in {path.relative_to(ROOT)}: {target}')
    print('Local-router source, package imports, versions, example and documentation links verified')


if __name__ == '__main__':
    main()
