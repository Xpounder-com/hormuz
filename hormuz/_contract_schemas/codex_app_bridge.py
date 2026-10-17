"""Versioned receipt for the reversible native app-server launch adapter."""
from pathlib import Path
import re

from .common import ContractValidationError, _exact_keys

FIELDS = {'schema_id', 'schema_version', 'phase', 'client_home', 'control_home', 'native_version',
          'native_sha256', 'bridge_executable', 'router_generation', 'normal_settings', 'effective_on_next_app_launch'}


def validate_install(value):
    _exact_keys(value, FIELDS)
    if (value['phase'] not in {'preview', 'enabled'} or value['native_version'] != '0.162.0-alpha.17.2'
            or value['normal_settings'] != 'unchanged' or value['effective_on_next_app_launch'] is not True
            or type(value['native_sha256']) is not str or re.fullmatch('[a-f0-9]{64}', value['native_sha256']) is None
            or type(value['router_generation']) is not str or re.fullmatch('[a-f0-9]{32}', value['router_generation']) is None
            or any(type(value[key]) is not str or len(value[key]) > 4096 or '\x00' in value[key]
                   or not Path(value[key]).is_absolute() for key in ('client_home', 'control_home', 'bridge_executable'))
            or value['client_home'] == value['control_home']):
        raise ContractValidationError('Invalid Codex app bridge installation receipt')
