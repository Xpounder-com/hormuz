"""Generation-bound discovery of one explicit chat owner; no app settings."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import time

from .connected_client import (ConnectedClientError, ConnectedClientLifetime,
    _control_directory, _directory, _lock_descriptor, _read, _write)
from .contracts import validate_contract
from .compaction_formats import strict_json_loads


@contextmanager
def _owner_record(normal_home):
    import fcntl
    normal_home = normal_home.expanduser().resolve()
    directory = _control_directory()
    _directory(directory)
    key = hashlib.sha256(str(normal_home).encode()).hexdigest()
    path = directory / ('chat-owner-' + key + '.json')
    descriptor = _lock_descriptor(directory/'chat-owners.lock')
    try:
        deadline = time.monotonic()+12
        while True:
            try:
                fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ConnectedClientError('busy')
                time.sleep(0.05)
        raw = _read(path,private=True)
        record = None
        if raw is not None:
            try:
                record = strict_json_loads(raw)
                validate_contract(record)
                if record['schema_id'] != 'hormuz.chat-owner' or record['normal_home'] != str(normal_home):
                    raise ValueError()
            except (ValueError,KeyError,TypeError,RecursionError):
                raise ConnectedClientError('state_invalid') from None
        yield path,record,raw
    finally:
        os.close(descriptor)


def register_owner(normal_home,router_home,generation):
    record = {'schema_id':'hormuz.chat-owner','schema_version':1,
              'normal_home':str(normal_home),'router_home':str(router_home),'generation':generation}
    validate_contract(record)
    with _owner_record(normal_home) as (path,previous,raw):
        if previous == record:
            return
        if previous is not None and previous['router_home'] != str(router_home):
            prior_home = Path(previous['router_home'])
            if not prior_home.is_dir():
                raise ConnectedClientError('state_invalid')
            status = ConnectedClientLifetime(prior_home).status()
            if status.get('phase') != 'disconnected' or status.get('router') != 'stopped':
                raise ConnectedClientError('busy')
        _write(path,json.dumps(record),expected=raw,compare=True)


def disconnect_owner(normal_home,*,generation=None):
    from .codex_app_bridge_control import disable_bridge
    with _owner_record(normal_home) as (path,record,raw):
        if record is None:
            return disable_bridge(normal_home,generation=generation)
        router_home = Path(record['router_home'])
        if not router_home.is_dir():
            raise ConnectedClientError('state_invalid')
        if generation is not None and generation != record['generation']:
            raise ConnectedClientError('generation_mismatch')
        lifetime = ConnectedClientLifetime(router_home)
        if lifetime.status().get('generation') != record['generation']:
            raise ConnectedClientError('generation_mismatch')
        failure = None
        result = None
        try:
            result = lifetime.disconnect(generation=record['generation'])
        except (ConnectedClientError,OSError) as error:
            if isinstance(error,ConnectedClientError) and error.code == 'connected_client_generation_mismatch':
                raise
            failure = error
        bridge_result = None
        try:
            bridge_result = disable_bridge(normal_home,generation=generation,
                expected_owner=(router_home,record['generation']))
        except (ConnectedClientError,OSError) as error:
            failure = failure or error
        if failure is not None:
            raise failure
        if _stopped(result) and (bridge_result is None or _stopped(bridge_result)):
            if _read(path,private=True) != raw:
                raise ConnectedClientError('configuration_changed')
            path.unlink()
        return bridge_result if bridge_result is not None and not _stopped(bridge_result) else result


def _stopped(result):
    return (result is not None and result.get('phase') == 'disconnected' and result.get('router') == 'stopped'
            and result.get('local_runtime',{}).get('state','stopped') == 'stopped')
