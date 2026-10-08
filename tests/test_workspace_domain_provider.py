import unittest
from unittest import mock
from dataclasses import replace

from hormuz.config import SessionBrokerConfig
from hormuz._workspace_domain_provider import RenderDomainProvider
from hormuz.workspace_store import WorkspaceError
from hormuz.workspace_domains import normalize_hostname


class DomainProviderTests(unittest.TestCase):
    def setUp(self):
        settings = replace(SessionBrokerConfig(), workspace_domain_target="fixture.onrender.com", workspace_domain_service_id="srv-fixture123", workspace_domain_api_key="synthetic-domain-key")
        self.provider = RenderDomainProvider(settings)

    def test_existing_provider_record_is_reused_and_unverified_dns_is_refreshed(self):
        record = {"id": "cdm-fixture", "name": "ai.customer.com", "domainType": "subdomain", "verificationStatus": "unverified", "redirectForName": ""}
        with mock.patch("hormuz._workspace_domain_provider._json_request", side_effect=[record, {}]) as request:
            self.assertEqual(self.provider.ensure("ai.customer.com"), ("cdm-fixture", False))
            self.assertTrue(request.call_args.args[0].endswith("/ai.customer.com/verify"))
            self.assertEqual(request.call_args.kwargs["method"], "POST")

    def test_create_recovers_by_durable_name_instead_of_creating_another_record(self):
        record = {"id": "cdm-fixture", "name": "ai.customer.com", "domainType": "subdomain", "verificationStatus": "verified", "redirectForName": ""}
        with mock.patch("hormuz._workspace_domain_provider._json_request", side_effect=[None, [record], record]) as request:
            self.assertEqual(self.provider.ensure("ai.customer.com"), ("cdm-fixture", True))
            self.assertEqual(request.call_args_list[1].kwargs["value"], {"name": "ai.customer.com"})
            self.assertEqual(request.call_count, 3)

    def test_apex_redirect_and_wrong_provider_record_are_refused(self):
        base = {"id": "cdm-fixture", "name": "ai.customer.com", "domainType": "subdomain", "verificationStatus": "verified", "redirectForName": ""}
        for change in ({"domainType": "apex"}, {"name": "other.customer.com"}, {"redirectForName": "customer.com"}):
            with self.subTest(change=change), mock.patch("hormuz._workspace_domain_provider._json_request", return_value={**base, **change}):
                with self.assertRaises(WorkspaceError):
                    self.provider.ensure("ai.customer.com")

    def test_dns_ownership_does_not_accept_cname_or_another_challenge(self):
        with mock.patch.object(self.provider, "records", return_value=['"hormuz-verification=other"']):
            self.assertFalse(self.provider.owned("ai.customer.com", "challenge"))
        with mock.patch.object(self.provider, "records", return_value=['"hormuz-verification=challenge"']):
            self.assertTrue(self.provider.owned("ai.customer.com", "challenge"))

    def test_tls_probe_refuses_private_mixed_and_missing_addresses(self):
        for addresses in ([], ["127.0.0.1"], ["169.254.169.254"], ["8.8.8.8", "10.0.0.1"], ["::1"]):
            with self.subTest(addresses=addresses), mock.patch.object(self.provider, "records", side_effect=[addresses, []]), mock.patch("socket.create_connection") as connect:
                self.assertIsNone(self.provider.tls_probe("ai.customer.com", "domain", "nonce"))
                connect.assert_not_called()

    def test_hostname_rejects_urls_ips_wildcards_static_hosts_and_www_pairing(self):
        for hostname in ("https://ai.customer.com", "127.0.0.1", "*.customer.com", "ai.customer.com:443", "ai.customer.com.", "customer.com", "usehormuz.github.io", "www.customer.com", "ai.localhost", "ai.customer.com/path"):
            with self.subTest(hostname=hostname), self.assertRaises(WorkspaceError):
                normalize_hostname(hostname)
        self.assertEqual(normalize_hostname("AI.Customer.COM"), "ai.customer.com")
