"""Codex 0.160.0 model metadata for actor-visible qualified local aliases.

The regular OpenAI data list remains authoritative for other API clients.
Metadata describes the admitted HTTP profile, not model quality or hardware fit.
"""
from functools import lru_cache
import json
from pathlib import Path


@lru_cache(maxsize=1)
def _base_instructions():
    # Preserve this pinned client's own fallback instructions, including its
    # tool/permission guidance. The accompanying JSON records source and digest.
    return json.loads(Path(__file__).with_name('codex-0160-base-instructions.json').read_text())['text']


def qualified_models(config, aliases):
    qualifications = dict(config.route_admission.local_routes)
    result = []
    for alias in aliases:
        route = config.model_routes[alias]
        if (alias not in qualifications
                or not {'responses', 'text', 'streaming', 'codex_http_0160'} <= set(route.capabilities)):
            continue
        result.append({
            'slug': alias, 'display_name': alias,
            'description': 'Operator-qualified local HTTP profile; coding quality is not certified.',
            'base_instructions': _base_instructions(),
            'default_reasoning_level': 'none',
            'supported_reasoning_levels': [{'effort': 'none', 'description': 'Reasoning is disabled for this qualified profile.'}],
            'shell_type': 'unified_exec' if 'tools' in route.capabilities else 'disabled',
            'visibility': 'list', 'supported_in_api': True, 'priority': 0,
            'include_skills_usage_instructions': False, 'include_plugin_usage_instructions': False,
            'include_apps_usage_instructions': False,
            'supports_reasoning_summary_parameter': False, 'default_reasoning_summary': 'none',
            'support_verbosity': False, 'default_verbosity': None,
            'apply_patch_tool_type': None,
            'truncation_policy': {'mode': 'bytes', 'limit': 10000},
            'experimental_supported_tools': [], 'input_modalities': ['text'],
        })
    return result
