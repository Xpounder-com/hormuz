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


def verify_agent_package():
    manifests = {name: json.loads((ROOT / name).read_text()) for name in (
        'plugin.json', 'gemini-extension.json', '.claude-plugin/plugin.json',
        '.agents/plugins/marketplace.json', '.claude-plugin/marketplace.json')}
    portable = manifests['plugin.json']
    identity = portable['name'], portable['version']
    if identity[0] != 'hormuz' or not re.fullmatch(r'\d+\.\d+\.\d+', identity[1]):
        raise SystemExit('Invalid portable plugin identity')
    if portable['$schema'] != 'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json':
        raise SystemExit('Unsupported portable plugin schema')
    for name in ('gemini-extension.json', '.claude-plugin/plugin.json'):
        if (manifests[name]['name'], manifests[name]['version']) != identity:
            raise SystemExit('Agent plugin identity mismatch: ' + name)
    codex = manifests['.agents/plugins/marketplace.json']
    legacy = manifests['.claude-plugin/marketplace.json']
    if codex['name'] != 'hormuz' or legacy['name'] != 'hormuz':
        raise SystemExit('Marketplace name differs from installation commands')
    if len(codex['plugins']) != 1 or len(legacy['plugins']) != 1:
        raise SystemExit('Unexpected marketplace entry')
    if (codex['plugins'][0]['name'] != identity[0]
            or legacy['plugins'][0]['name'] != identity[0]
            or codex['plugins'][0]['source'] != {'source': 'local', 'path': './'}
            or legacy['plugins'][0]['source'] != './'):
        raise SystemExit('Marketplace must resolve the shared root plugin')
    overlay = portable['extensions']['com.openai']
    if overlay['onboardingSkill'] != './skills/hormuz/SKILL.md':
        raise SystemExit('Onboarding must use the shared skill')
    for name, manifest in manifests.items():
        if re.search(r'"(?:hooks|mcpServers|apps|agents|commands|settings|lspServers)"',
                     json.dumps(manifest)):
            raise SystemExit('Skills-only package has executable wiring: ' + name)
    for name in ('mcp.json', '.mcp.json', '.app.json', 'hooks', 'commands', 'agents', 'GEMINI.md'):
        if (ROOT / name).exists():
            raise SystemExit('Unexpected agent activation content: ' + name)
    skill_paths = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / 'skills').rglob('SKILL.md'))
    expected = ['hormuz', 'hormuz-diagnose', 'hormuz-explain', 'hormuz-insights', 'hormuz-maintain', 'hormuz-model', 'hormuz-policy', 'hormuz-qualify']
    if skill_paths != sorted('skills/' + name + '/SKILL.md' for name in expected):
        raise SystemExit('Expected onboarding and focused shared CLI skills')
    skill = (ROOT / 'skills/hormuz/SKILL.md').read_text()
    if not skill.startswith('---\nname: hormuz\ndescription: ') or '\n---\n' not in skill[4:]:
        raise SystemExit('Skill frontmatter must identify its invocation')


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
    verify_agent_package()
    for path in [*ROOT.glob('*.md'), *(ROOT / 'docs').rglob('*.md'), *(ROOT / 'skills').rglob('*.md')]:
        for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', path.read_text()):
            if re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', target) or target.startswith('#'):
                continue
            if not (path.parent / unquote(target.split('#')[0])).exists():
                raise SystemExit(f'Broken local link in {path.relative_to(ROOT)}: {target}')
    print('Local-router source, imports, versions, example, agent package and links verified')


if __name__ == '__main__':
    main()
