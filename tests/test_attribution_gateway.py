from pathlib import Path
import tempfile
import unittest

if __package__:
    from ._attribution_gateway_fixture import AttributionGatewayAssertions
    from ._portfolio_fixture import registry_config
else:
    from _attribution_gateway_fixture import AttributionGatewayAssertions
    from _portfolio_fixture import registry_config


class AttributionGatewayTests(AttributionGatewayAssertions, unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.setup_gateway(registry_config(Path(temporary.name)))

    def test_native_success_bodies_models_and_header_stripping(self):
        self.check_native_success_bodies_models_and_header_stripping()

    def test_rejection_precedes_budget_policy_and_provider(self):
        self.check_rejection_precedes_budget_policy_and_provider()

    def test_failed_attribution_commit_retains_uncertain_hold(self):
        self.check_failed_attribution_commit_never_egresses_or_frees_uncertain_hold()

    def test_scope_change_after_reservation_never_egresses(self):
        self.check_scope_change_after_reservation_never_egresses()

    def test_unattributed_and_nonaccounted_behavior(self):
        self.check_unattributed_default_and_nonaccounted_behavior()

    def test_identity_cannot_gain_attribution_authority(self):
        self.check_unauthenticated_or_unbound_identity_cannot_lookup()

    def test_admin_http_correction_contract(self):
        self.check_admin_http_matches_versioned_correction_contract()
