"""Check and bump the independent runtime and skills-plugin release versions."""
from pathlib import Path
import argparse
import ast
import json
import re
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_FILES = ("pyproject.toml", "hormuz/__init__.py")
PLUGIN_FILES = ("plugin.json", ".claude-plugin/plugin.json", "gemini-extension.json")
NUMBER = r"(?:0|[1-9][0-9]*)"
VERSION = re.compile(rf"({NUMBER})\.({NUMBER})\.({NUMBER})(?:(a|b|rc)({NUMBER}))?")


def parse_version(value, component):
    match = VERSION.fullmatch(value) if isinstance(value, str) else None
    if match is None or (component == "plugin" and match[4] is not None):
        raise ValueError(f"Invalid canonical {component} version: {value!r}")
    return match


def read_metadata(root):
    texts = {name: (root / name).read_text(encoding="utf-8")
             for name in (*RUNTIME_FILES, *PLUGIN_FILES)}
    runtime = tomllib.loads(texts["pyproject.toml"])["project"]["version"]
    assignments = [node.value for node in ast.parse(texts["hormuz/__init__.py"]).body
                   if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "__version__"
                           for target in node.targets)]
    if len(assignments) != 1 or not isinstance(assignments[0], ast.Constant):
        raise ValueError("hormuz/__init__.py must declare one literal __version__")
    if assignments[0].value != runtime:
        raise ValueError("hormuz/__init__.py version differs from pyproject.toml")
    plugin = json.loads(texts["plugin.json"])["version"]
    for name in PLUGIN_FILES[1:]:
        if json.loads(texts[name])["version"] != plugin:
            raise ValueError(f"{name} version differs from plugin.json")
    parse_version(runtime, "runtime")
    parse_version(plugin, "plugin")
    return texts, {"runtime": runtime, "plugin": plugin}


def check_versions(root=ROOT):
    """Read static release metadata without importing or executing the runtime."""
    return read_metadata(Path(root))[1]


def replace_version(text, pattern, old, new):
    matches = list(re.finditer(pattern, text))
    if len(matches) != 1 or matches[0][3] != old:
        raise ValueError("Expected one editable version declaration")
    match = matches[0]
    return text[:match.start(3)] + new + text[match.end(3):]


def next_version(old, component, part, pre):
    match = parse_version(old, component)
    numbers = [int(match[index]) for index in (1, 2, 3)]
    stage, ordinal = match[4], match[5]
    if component == "plugin" and (stage or pre or part not in ("major", "minor", "patch")):
        raise ValueError("plugin supports only stable major, minor and patch bumps")
    stages = {"alpha": "a", "beta": "b", "rc": "rc"}
    if part in ("major", "minor", "patch"):
        index = ("major", "minor", "patch").index(part)
        numbers[index] += 1
        numbers[index + 1:] = [0] * (2 - index)
        suffix = stages[pre] + "1" if pre else ""
    elif part == "final":
        if stage is None or pre:
            raise ValueError("final requires an existing prerelease and no --pre")
        suffix = ""
    else:
        if stage is None:
            raise ValueError("prerelease requires an existing prerelease; start one with a numeric bump and --pre")
        desired = stages[pre] if pre else stage
        if ("a", "b", "rc").index(desired) < ("a", "b", "rc").index(stage):
            raise ValueError("prerelease stages cannot move backwards")
        suffix = desired + str(int(ordinal) + 1 if desired == stage else 1)
    return ".".join(map(str, numbers)) + suffix


def bump_version(root, component, part, dry_run=False, pre=None):
    texts, versions = read_metadata(root)
    old = versions[component]
    new = next_version(old, component, part, pre)
    if component == "plugin":
        for name in PLUGIN_FILES:
            manifest = json.loads(texts[name])
            manifest["version"] = new
            texts[name] = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
        files = PLUGIN_FILES
    else:
        project = re.search(r"(?ms)^\[project\][ \t]*\n(.*?)(?=^\[|\Z)", texts["pyproject.toml"])
        if project is None:
            raise ValueError("Expected a [project] metadata table")
        updated_project = replace_version(project[1], r"(?m)^([ \t]*version\s*=\s*)(['\"])([^'\"\n]*)(\2)", old, new)
        texts["pyproject.toml"] = (texts["pyproject.toml"][:project.start(1)] + updated_project
                                  + texts["pyproject.toml"][project.end(1):])
        texts["hormuz/__init__.py"] = replace_version(
            texts["hormuz/__init__.py"], r"(?m)^(__version__\s*=\s*)(['\"])([^'\"\n]*)(\2)", old, new)
        files = RUNTIME_FILES
    if not dry_run:
        for name in files:
            (root / name).write_text(texts[name], encoding="utf-8")
    return {"component": component, "from": old, "to": new, "dry_run": dry_run,
            "files": list(files)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root (defaults to this checkout)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="validate both release streams and their mirrors")
    bump = commands.add_parser("bump", help="update one release stream")
    bump.add_argument("component", choices=("runtime", "plugin"))
    bump.add_argument("part", choices=("major", "minor", "patch", "prerelease", "final"))
    bump.add_argument("--dry-run", action="store_true", help="report the change without writing files")
    bump.add_argument("--pre", choices=("alpha", "beta", "rc"), help="start or promote a runtime prerelease stage")
    args = parser.parse_args()
    try:
        if args.command == "check":
            result = {**check_versions(args.root), "status": "consistent"}
        else:
            result = bump_version(args.root, args.component, args.part, args.dry_run, args.pre)
    except (OSError, ValueError, KeyError, TypeError, SyntaxError) as error:
        parser.exit(2, f"version: {error}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
