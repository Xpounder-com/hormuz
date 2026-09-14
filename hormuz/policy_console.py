"""Isolated policy-administration listener; contains no inference routes.

Run with ``python -m hormuz.policy_console --config /private/hormuz.json``.
Route /console and /v1/admin at the configured public origin to this listener.
Only this process receives the policy-control DSN. The gateway captures metadata
beside the single-host session database and retains runtime-only DB authority.
"""
from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import ThreadingHTTPServer
import os
import threading

from ._config_builder import build_policy_console_config
from .auth import Authenticator
from .console import ConsoleService
from .console_http import handle_console_request
from .policy_control import PolicyControlService
from .policy_impact import ImpactStore, impact_path
from .policy_impact_control import PolicyImpactControl
from .policy_repository import PolicyControlError
from .server import GatewayRequestHandler
from .session import SessionBroker
from .session_http import SessionRequestLimit
from .session_store import SQLiteSessionStore
from .store_router import create_usage_store


class PolicyConsoleHandler(GatewayRequestHandler):
    timeout = 10

    def do_GET(self):
        handle_console_request(self)

    def do_POST(self):
        handle_console_request(self)

    def do_OPTIONS(self):
        self.close_connection = True
        self._send_error("not_found", "Route not found", HTTPStatus.NOT_FOUND)


class PolicyConsoleServer(ThreadingHTTPServer):
    daemon_threads = False
    request_queue_size = 16

    def __init__(self, config, *, address=("127.0.0.1", 8788), environ):
        settings = config.session_broker
        if not (settings.enabled and settings.console_enabled and settings.policy_impact_enabled and config.policy_control.mode == "postgresql"):
            raise PolicyControlError("impact_configuration_required")
        self.config = config
        self._slots = threading.BoundedSemaphore(8)
        self.console_request_limit = SessionRequestLimit()
        store = SQLiteSessionStore(settings.database_path, master_key=settings.master_key, audience=settings.public_base_url,
                                   access_ttl_seconds=settings.access_ttl_seconds, absolute_ttl_seconds=settings.absolute_ttl_seconds,
                                   enrollment_ttl_seconds=settings.enrollment_ttl_seconds, trusted_parent_path=settings.trusted_parent_path)
        broker = SessionBroker(config, Authenticator(config), store)
        organizations = tuple(sorted(set(config.organization_ids) | set(broker.directory.managed_organization_ids())))
        usage = create_usage_store(config, environ=environ, read_only=True, organization_ids=organizations)
        self.console = ConsoleService(broker, usage)
        self.policy_console = PolicyImpactControl(config=config, sessions=self.console.sessions,
            controller=PolicyControlService(config, environ=environ, organization_ids=organizations),
            store=ImpactStore(impact_path(settings.database_path), trusted_parent_path=settings.trusted_parent_path))
        super().__init__(address, PolicyConsoleHandler)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Serve the isolated Hormuz policy administrator console")
    parser.add_argument("--config", required=True)
    parser.add_argument("--port", type=int, default=8788)
    args = parser.parse_args(argv)
    config = build_policy_console_config(args.config, environ=os.environ)
    # The controller receives a small environment view, never an operator's
    # inference key, migration credential, or break-glass credential.
    environment = {
        config.usage_storage.postgres_dsn_env: os.environ.get(config.usage_storage.postgres_dsn_env, ""),
        config.policy_control.postgres_control_dsn_env: os.environ.get(config.policy_control.postgres_control_dsn_env, ""),
    }
    server = PolicyConsoleServer(config, address=("127.0.0.1", args.port), environ=environment)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
