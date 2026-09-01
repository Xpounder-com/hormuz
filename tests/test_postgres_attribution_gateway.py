from dataclasses import replace
import os
from pathlib import Path
from unittest import mock

from hormuz.config import UsageStorageConfig

if __package__:
    from ._attribution_gateway_fixture import AttributionGatewayAssertions
    from ._portfolio_fixture import registry_config
    from ._postgres_fixture import PostgresTestCase
else:
    from _attribution_gateway_fixture import AttributionGatewayAssertions
    from _portfolio_fixture import registry_config
    from _postgres_fixture import PostgresTestCase


class PostgresAttributionGatewayTests(AttributionGatewayAssertions, PostgresTestCase):
    def setUp(self):
        super().setUp()
        environment = {"HORMUZ_POSTGRES_DSN": self.runtime_dsn}
        patch = mock.patch.dict(os.environ, environment)
        patch.start()
        self.addCleanup(patch.stop)
        config = replace(registry_config(Path("/unused/native-attribution")), usage_storage=UsageStorageConfig(
            backend="postgresql", postgres_schema=self.schema, postgres_runtime_role=self.runtime_role))
        self.setup_gateway(config, environment=environment)

    def test_postgres_native_success_bodies_models_and_header_stripping(self):
        self.check_native_success_bodies_models_and_header_stripping()

    def test_postgres_rejection_precedes_budget_policy_and_provider(self):
        self.check_rejection_precedes_budget_policy_and_provider()

    def test_postgres_failed_atomic_attribution_commit_rolls_back_before_egress(self):
        self.check_failed_atomic_attribution_commit_rolls_back_before_egress()

    def test_postgres_unbounded_output_never_egresses_under_active_work_budget(self):
        self.check_unbounded_output_never_egresses_under_active_work_budget()

    def test_postgres_provider_side_input_never_egresses_under_active_work_budget(self):
        self.check_provider_side_input_never_egresses_under_active_work_budget()

    def test_postgres_unbound_identity_cannot_bypass_an_active_work_budget(self):
        self.check_unbound_identity_cannot_bypass_an_active_work_budget()

    def test_postgres_unbound_identity_uses_database_clock(self):
        self.check_postgres_unbound_identity_uses_database_clock()

    def test_postgres_scope_change_before_atomic_reservation_never_egresses(self):
        self.check_scope_change_before_atomic_reservation_never_egresses()

    def test_postgres_unattributed_and_nonaccounted_behavior(self):
        self.check_unattributed_default_and_nonaccounted_behavior()

    def test_postgres_identity_cannot_gain_attribution_authority(self):
        self.check_unauthenticated_or_unbound_identity_cannot_lookup()

    def test_postgres_admin_http_correction_contract(self):
        self.check_admin_http_matches_versioned_correction_contract()
