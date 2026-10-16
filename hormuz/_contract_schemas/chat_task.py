"""V1 receipts for explicit, local-only Hormuz tasks in an existing chat."""
import re
from .common import ContractValidationError, _exact_keys

TASK_FIELDS = {'schema_id', 'schema_version', 'text', 'model', 'routed_model', 'locality', 'destination',
               'generation', 'work_id', 'selection', 'reasoning_effort'}
MODELS_FIELDS = {'schema_id', 'schema_version', 'default_model', 'models'}
ERROR_FIELDS = {'schema_id', 'schema_version', 'error', 'submission', 'retried'}
ERROR_CODES = {'prompt_limit', 'separate_router_home_required', 'router_unavailable', 'router_ownership_changed',
               'owned_loopback_router_required', 'router_request_rejected', 'response_limit', 'model_not_qualified',
               'execution_selection_changed', 'task_incomplete', 'task_unavailable'}


def label(value):
    return isinstance(value, str) and 0 < len(value) <= 4096 and '\x00' not in value


def validate_task(value):
    _exact_keys(value, TASK_FIELDS)
    if (not all(label(value[key]) for key in ('model', 'routed_model', 'destination'))
            or value['locality'] != 'local' or value['selection'] not in {'model', 'router_default'}
            or value['reasoning_effort'] not in {'low', 'medium', 'high', 'max'}
            or not isinstance(value['text'], str) or len(value['text']) > 2 * 1024 * 1024
            or not isinstance(value['generation'], str) or re.fullmatch('[a-f0-9]{64}', value['generation']) is None
            or value['work_id'] is not None and not label(value['work_id'])):
        raise ContractValidationError('Invalid explicit chat task receipt')


def validate_models(value):
    _exact_keys(value, MODELS_FIELDS)
    if (not label(value['default_model']) or not isinstance(value['models'], list)
            or len(value['models']) > 128 or not all(label(model) for model in value['models'])
            or len(set(value['models'])) != len(value['models'])):
        raise ContractValidationError('Invalid explicit chat model list')


def validate_error(value):
    _exact_keys(value, ERROR_FIELDS)
    _exact_keys(value['error'], {'code'})
    if (value['error']['code'] not in ERROR_CODES or value['submission'] not in {'not_submitted', 'possibly_submitted'}
            or value['retried'] is not False):
        raise ContractValidationError('Invalid explicit chat failure receipt')
