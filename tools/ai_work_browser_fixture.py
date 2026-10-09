#!/usr/bin/env python3
"""Local-only browser QA: synthetic OIDC, provider, Stripe and signed GitHub.

The runner owns a temp gateway and sends control commands through stdin. No
credential is printed. Nothing contacts Stripe, GitHub or a paid model.
"""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import sys
import time
import threading
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hormuz.config import Identity
from hormuz.github_connector import GitHubOutcomeReceiver
from hormuz.portfolio_repository import create_portfolio_repository
from hormuz.work_billing import STRIPE_VERSION, WorkBilling
from hormuz.work_workflow import WorkWorkflow, GitHubWorkRepository
from tests.test_work_http import WorkHTTPTests
from tests import test_github_connector_runtime as github
from tools.ai_work_proof import FixtureProvider, PROOF_SOURCE_FILES


class BoundedProviderServer(ThreadingHTTPServer):
    """Fixture listener with at most four handlers and bounded socket reads."""

    def __init__(self, *args, **kwargs):
        self._slots = threading.BoundedSemaphore(4)
        super().__init__(*args, **kwargs)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(10)
        return connection, address

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


class BrowserFixture(WorkHTTPTests):
    def configure_gateway(self, config):
        config = super().configure_gateway(config)
        FixtureProvider.calls = []
        provider = BoundedProviderServer(('127.0.0.1', 0), FixtureProvider)
        provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
        self.addCleanup(self._close, provider, provider_thread)
        provider_thread.start()
        upstreams = {**config.upstreams, 'openai': replace(config.upstreams['openai'], base_url=f'http://127.0.0.1:{provider.server_port}')}
        return replace(config, upstreams=upstreams, ai_work=replace(config.ai_work, cache_enabled=True, minimum_samples=3),
                       model_routes={alias: replace(route, input_cost_per_million=1, output_cost_per_million=1) for alias, route in config.model_routes.items()})

    @staticmethod
    def _close(server, thread):
        if thread.is_alive():
            server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main():
    fixture_path = Path(sys.argv[1])
    case = BrowserFixture()
    try:
        case.setUp()
        case.login_console()
        admin = case.admin.membership_id
        external = github.runtime_config(case.root)
        portfolio = replace(external.portfolio_control,
            role_bindings=tuple(replace(row, organization_id='customer-a', actor_id=admin) for row in external.portfolio_control.role_bindings[:1]),
            connectors=tuple(replace(row, organization_id='customer-a') for row in external.portfolio_control.connectors[:1]))
        channels = replace(external.outcome_connectors, github=tuple(replace(row, organization_id='customer-a') for row in external.outcome_connectors.github[:1]))
        case.config = replace(case.config, portfolio_control=portfolio, outcome_connectors=channels, session_broker=replace(case.config.session_broker, public_base_url='https://gateway.fixture.test'),
            ai_work=replace(case.config.ai_work, cache_enabled=True, require_paid=True, billing_price_id='price_BrowserFixture', administrator_actor_ids=(admin,)))
        case.gateway.config = case.config
        case.gateway.session_broker.config = case.config
        case.gateway.work_runtime.config = case.config
        case.gateway.work_runtime.cache_enabled = True
        case.gateway.work_workflow = WorkWorkflow(case.gateway.work_runtime, case.config)
        repository = create_portfolio_repository(case.config)
        receiver = GitHubOutcomeReceiver(case.config, GitHubWorkRepository(repository.outcomes, case.gateway.work_workflow))
        secret = 'whsec_local_browser_fixture_never_real'
        billing = WorkBilling(case.root / 'browser.billing.sqlite3', 'price_BrowserFixture', [], secret, api_key='synthetic-transport-only')
        case.gateway.work_billing = billing
        checkout = {}
        def stripe(path, values, **kwargs):
            if path != '/v1/checkout/sessions':
                raise AssertionError('Unexpected synthetic billing call')
            checkout['reference'] = values['client_reference_id']
            return {'id': 'cs_local_BrowserFixture', 'url': 'https://checkout.stripe.com/c/pay/LocalBrowserFixture', 'livemode': True, 'mode': 'subscription', 'client_reference_id': checkout['reference']}
        billing._stripe = stripe
        _, cookie = case.cookie.split('=', 1)
        name = '__Host-hormuz_console'
        fixture_path.write_text(json.dumps({'url': case.gateway_url, 'public_origin': 'https://gateway.fixture.test', 'cookie': {'name': name, 'value': cookie}, 'token': case.native.access_token, 'source_files': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in PROOF_SOURCE_FILES}}))
        fixture_path.chmod(0o600)
        print(json.dumps({'event': 'ready', 'url': case.gateway_url}), flush=True)
        identity = case.gateway.session_broker.authenticate(case.native.access_token)
        def webhook(kind, item, number):
            now = int(time.time())
            raw = json.dumps({'id': 'evt_Browser' + str(number), 'type': kind, 'created': now, 'api_version': STRIPE_VERSION, 'livemode': True, 'data': {'object': item}}, separators=(',', ':')).encode()
            signature = hmac.new(secret.encode(), str(now).encode() + b'.' + raw, hashlib.sha256).hexdigest()
            status, _, body = case.request('POST', '/v1/work/billing/webhook', raw, {'Host': 'gateway.fixture.test', 'Stripe-Signature': f't={now},v1={signature}', 'Content-Type': 'application/json'})
            if status != 200:
                raise AssertionError((status, body))
        for line in sys.stdin:
            command = json.loads(line)
            operation = command['operation']
            try:
                if operation == 'checkout':
                    # Server-created reference is bound by the actual signed handler.
                    webhook('checkout.session.completed', {'id': 'cs_local_BrowserFixture', 'client_reference_id': checkout['reference'], 'mode': 'subscription', 'status': 'complete', 'payment_status': 'paid', 'customer': 'cus_BrowserFixture', 'subscription': 'sub_BrowserFixture'}, 1)
                    assert not billing.entitled('customer-a')
                elif operation == 'payment':
                    now = int(time.time())
                    webhook('customer.subscription.updated', {'id': 'sub_BrowserFixture', 'customer': 'cus_BrowserFixture', 'status': 'active', 'items': {'data': [{'price': {'id': 'price_BrowserFixture'}, 'quantity': 1, 'current_period_start': now-1, 'current_period_end': now+3600}]}}, 2)
                    webhook('invoice.paid', {'customer': 'cus_BrowserFixture', 'parent': {'subscription_details': {'subscription': 'sub_BrowserFixture'}}, 'status': 'paid', 'amount_paid': 4999, 'lines': {'data': [{'price': {'id': 'price_BrowserFixture'}, 'period': {'start': now-1, 'end': now+3600}}]}}, 3)
                    assert billing.entitled('customer-a')
                elif operation in {'corrected', 'completed'}:
                    case.gateway.work_runtime.observe(identity, command['work_id'], operation, source='workflow', reference='local-fixture/workflow-check')
                elif operation == 'signed_merge':
                    value = github.payload(action='closed')
                    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
                    value['pull_request'].update(merged=True, closed_at=now, updated_at=now)
                    raw = github.encoded(value)
                    receiver.ingest(github.signed(raw), raw)
                    assert case.gateway.work_runtime.get_work(identity, command['work_id'])['state'] == 'completed'
                elif operation == 'stop':
                    print(json.dumps({'event': operation, 'ok': True}), flush=True)
                    break
                else:
                    raise AssertionError('Unknown fixture command')
                print(json.dumps({'event': operation, 'ok': True, 'provider_fixture_calls': len(FixtureProvider.calls)}), flush=True)
            except Exception as error:
                print(json.dumps({'event': operation, 'ok': False, 'error': type(error).__name__ + ': ' + str(error)[:500]}), flush=True)
    finally:
        fixture_path.unlink(missing_ok=True)
        case.doCleanups()


if __name__ == '__main__':
    main()
