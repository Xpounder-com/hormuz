"""Customer-visible measurement, authority, and consent boundaries."""
import unittest

from hormuz import work_pages


class WorkPagesTests(unittest.TestCase):
    def state(self):
        return {'principal': {'organization_id': 'customer-a', 'actor_id': 'member-1', 'can_manage_plans': True, 'can_mutate_work': True, 'csrf_token': 'synthetic-csrf'},
                'works': [], 'plans': [], 'totals': {},
                'connection': {'endpoint': 'https://gateway.example.test', 'routes': [], 'application_access_enabled': False},
                'billing': {'status': 'self_hosted'}, 'onboarding': {'state': 'not_configured', 'identity_verified': True, 'application_access_enabled': False}}

    def job(self):
        return {'work_id': 'work-1', 'repository': 'example/repo', 'actor_id': 'member-1', 'state': 'active',
                'title': 'Repair CI', 'objective': 'speed', 'completion_condition': 'github.check.passed.v1', 'costs': {}, 'attempts': [], 'observations': []}

    def test_missing_cost_timing_and_outcomes_remain_unknown(self):
        state = self.state(); state['works'] = [self.job()]
        rendered = work_pages.dashboard(state)
        self.assertIn('Provider-confirmed attempt subset', rendered)
        self.assertIn('Unknown captured attempts still unconfirmed', rendered)
        self.assertIn('Gateway overhead</dt><dd>Unknown', rendered)
        self.assertIn('No comparable candidate evidence', rendered)
        self.assertIn('Outcome remains unknown.', rendered)
        self.assertNotIn('Maximum estimated request cost', rendered)

    def test_read_only_grant_cannot_render_mutation_controls(self):
        state = self.state(); state['principal']['can_mutate_work'] = False; state['works'] = [self.job()]
        rendered = work_pages.dashboard(state)
        for action in ('/v1/work/policies', '/v1/work/jobs"', '/actions', '/observations'):
            self.assertNotIn('action="' + action, rendered)
        self.assertIn('read only', rendered)
        self.assertIn('/v1/work/support/receipt', rendered)

    def test_qualification_pairs_only_eligible_routes_and_operator_controls(self):
        state = self.state()
        state['onboarding'].update(state='qualification_required', can_request_qualification=True, choices=[
            {'model': 'approved-openai', 'protocol': 'openai', 'client': 'codex', 'credential_configured': True, 'eligible': True},
            {'model': 'unconfigured-anthropic', 'protocol': 'anthropic', 'client': 'claude-code', 'credential_configured': False, 'eligible': False}])
        rendered = work_pages.dashboard(state)
        self.assertIn('name="model" value="approved-openai"', rendered)
        self.assertIn('name="client" value="codex"', rendered)
        self.assertNotIn('value="unconfigured-anthropic"', rendered)
        self.assertNotIn('action="/v1/work/activation/review"', rendered)
        self.assertNotIn('action="/v1/work/billing/checkout"', rendered)
        state['onboarding'].update(can_review_qualification=True, can_checkout=True, can_reset=True, can_reverify=True)
        rendered = work_pages.dashboard(state)
        for endpoint in ('activation/review', 'billing/checkout', 'activation/reset', 'activation/reverify'):
            self.assertIn('action="/v1/work/' + endpoint + '"', rendered)
        for forbidden in ('name="customer_id"', 'name="price_id"', 'name="subscription_id"', 'name="amount_paid"'):
            self.assertNotIn(forbidden, rendered)
        self.assertIn('does not cancel the Stripe subscription', rendered)

    def test_forecasts_show_coverage_holds_and_estimate_conditions(self):
        state = self.state(); state['plans'] = [{'scope_type': 'workspace', 'scope_id': 'customer-a', 'version': 1,
            'budget_microusd': 10_000_000, 'remaining_microusd': 7_000_000, 'committed_microusd': 2_000_000, 'objective': 'cost',
            'provider_confirmed_cost_microusd': 1_000_000, 'unconfirmed_attempts': 2,
            'forecast': {'point_microusd': 5_000_000, 'lower_microusd': 3_000_000, 'upper_microusd': 9_000_000,
                'basis': 'observed_daily_run_rate_scenarios_not_confidence_interval', 'observed_days': 3,
                'coverage': 'gateway_captured_work_requests_only', 'uncertainty': ['pending_provider_holds', 'estimated_provider_prices'], 'excludes_holds': True}}]
        rendered = work_pages.dashboard(state)
        for evidence in ('$3.00–$9.00', '3 observed days', 'not confidence interval', 'pending provider holds', 'estimated provider prices', 'Excludes unsettled holds.', 'invoice finality: Not established'):
            self.assertIn(evidence, rendered)

    def test_exploration_inherits_and_warns_about_monthly_scope_cost(self):
        state = self.state(); state['routing'] = {'exploration_enabled': True, 'exploration_aliases': ['economy'], 'exploration_max_cost_microusd': 500_000, 'exploration_rate_percent': 10, 'minimum_samples': 5}
        rendered = work_pages.dashboard(state)
        self.assertIn('value="inherit" selected', rendered)
        self.assertIn('Configured monthly exploration allowance: $0.50', rendered)
        self.assertIn('may incur provider charges', rendered)
        self.assertIn('Monthly exploration allowance', rendered)

    def test_account_totals_do_not_allocate_or_claim_a_final_invoice(self):
        state = self.state(); state['provider_account_costs'] = {'status': 'partial', 'accounts': [
            {'provider': 'openai', 'account_binding_id': 'approved-account', 'provider_reported_amount': '12.34', 'status': 'observed', 'observed_bucket_count': 3, 'missing_bucket_count': 2,
             'selected_snapshot_provenance': [{'evidence_origin': 'provider_cost_api'}], 'query_audit_event_id': 'audit-42'},
            {'provider': 'anthropic', 'provider_reported_amount': None, 'status': 'unavailable'}]}
        rendered = work_pages.dashboard(state)
        self.assertIn('12.34 USD', rendered)
        self.assertIn('not allocated to individual jobs or requests', rendered)
        self.assertIn('provider_cost_api', rendered)
        self.assertIn('audit-42', rendered)
        self.assertIn('Invoice finality: Not established', rendered)
        self.assertIn('Unknown<small>', rendered)

    def test_connector_choices_pair_provider_and_condition_without_provider_secrets(self):
        state = self.state(); job = self.job(); state['works'] = [job]
        job.update(binding_available=True, connector_choices=[{'provider': 'github', 'connector_id': 'approved-github', 'container_ids': ['example/repo']}])
        rendered = work_pages.dashboard(state)
        self.assertIn('action="/v1/work/jobs/work-1/bindings"', rendered)
        self.assertIn('name="provider" value="github"', rendered)
        self.assertIn('name="connector_id" value="approved-github"', rendered)
        self.assertIn('name="completion_condition" value="github.check.passed.v1"', rendered)
        self.assertIn('value="example/repo"', rendered)
        self.assertNotIn('name="webhook_secret"', rendered)
        self.assertIn('href="/v1/work/jobs/work-1"', rendered)

    def test_campaign_confirmation_escapes_labels_excludes_private_ids_and_requires_consent(self):
        rendered = work_pages.acquisition({'csrf_token': 'fixture'}, {'utm_source': '<script>', 'work_id': 'private-work', 'token': 'private-token'})
        self.assertIn('&lt;script&gt;', rendered)
        self.assertNotIn('private-work', rendered)
        self.assertNotIn('private-token', rendered)
        self.assertIn('name="analytics_consent" value="true" required', rendered)
        self.assertNotIn('checked', rendered)
        self.assertIn('Continue without source attribution', rendered)
        state = self.state(); self.assertNotIn('Your consented source', work_pages.dashboard(state))
        state['funnel'] = {'consent': True, 'events': {'first_attributed_work': 1, 'payment_verified': 0}}
        rendered = work_pages.dashboard(state)
        self.assertIn('Current payment verified</dt><dd>0', rendered)
        self.assertNotIn('private-work', rendered)

    def test_customer_text_is_escaped_in_measurement_and_binding_views(self):
        state = self.state(); job = self.job(); state['works'] = [job]
        job.update(title='<script>alert(1)</script>', repository='<img src=x onerror=alert(1)>')
        job['attempts'] = [{'model': '<script>', 'reason': '<script>', 'cache_bypass_reason': 'observed_correction'}]
        job['costs'] = {'attempts': 1}
        rendered = work_pages.dashboard(state, message='<script>')
        self.assertNotIn('<script>', rendered)
        self.assertNotIn('<img src=x', rendered)
        self.assertIn('observed correction', rendered)

    def test_zero_sum_without_any_confirmation_is_not_a_zero_provider_bill(self):
        state = self.state(); state['totals'].update(provider_confirmed_cost_microusd=0, provider_confirmed_attempts=0, unconfirmed_attempts=3)
        rendered = work_pages.dashboard(state)
        self.assertIn('Provider-confirmed attempt subset</h2><p class="metric-value">Unknown', rendered)
        self.assertIn('3 captured attempts still unconfirmed', rendered)
        state['totals']['provider_confirmed_attempts'] = 1
        rendered = work_pages.dashboard(state)
        self.assertIn('Provider-confirmed attempt subset</h2><p class="metric-value">$0.00', rendered)

    def test_login_explains_pending_campaign_confirmation_without_recording_claim(self):
        rendered = work_pages.login(message='Sign in, then confirm source. <script>')
        self.assertIn('Sign in, then confirm source.', rendered)
        self.assertIn('&lt;script&gt;', rendered)
        self.assertIn('href="/workspace"', rendered)
        self.assertNotIn('action="/v1/work/acquisition"', rendered)

    def test_guidance_advances_after_work_is_captured(self):
        state = self.state(); state['works'] = [self.job()]; state['totals']['attempts'] = 1
        state['onboarding'].update(state='active', next_step='create_first_job', application_access_enabled=True)
        rendered = work_pages.dashboard(state)
        self.assertIn('Next requirement: inspect captured work', rendered)
        self.assertIn('Inspect captured jobs', rendered)
        self.assertIn('Start another job', rendered)
        self.assertIn('id="your-jobs"', rendered)
        self.assertNotIn('Create your first job', rendered)
