from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest import mock

from hormuz.policy_control import PolicyControlService
from hormuz.policy_repository import PolicyActivation, PolicyAdministrator, PolicyControlError


class PolicyControlServiceTests(unittest.TestCase):
    def test_apply_canonicalizes_the_file_before_entering_the_repository(self) -> None:
        service = object.__new__(PolicyControlService)
        service._config = SimpleNamespace(organization_ids={"xpounder"})
        service._repository = mock.Mock()
        caller = PolicyAdministrator(
            organization_id="xpounder",
            authentication_kind="static",
            actor_id="alice",
        )
        document = mock.sentinel.document
        activation = PolicyActivation(
            organization_id="xpounder",
            version_id="sha256:" + "a" * 64,
            generation=1,
            activated_at=mock.sentinel.activated_at,
            activated_by_kind="static",
            activated_by_identity_key="static:" + "b" * 64,
            action="policy_activated",
        )
        order: list[str] = []

        def authenticate(**_kwargs: object) -> PolicyAdministrator:
            order.append("authenticate")
            return caller

        def load(*_args: object, **_kwargs: object) -> object:
            order.append("load")
            return document

        def apply(**_kwargs: object) -> PolicyActivation:
            order.append("repository")
            return activation

        service._repository.apply.side_effect = apply
        with (
            mock.patch.object(service, "_authenticated_administrator", side_effect=authenticate),
            mock.patch("hormuz.policy_control.load_policy_document", side_effect=load),
        ):
            result = service.apply(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                policy_path="candidate.json",
                if_active_version_id="sha256:" + "c" * 64,
            )

        self.assertIs(result, activation)
        self.assertLess(order.index("load"), order.index("repository"))
        service._repository.apply.assert_called_once_with(
            organization_id="xpounder",
            caller=caller,
            document=document,
            expected_active_version_id="sha256:" + "c" * 64,
        )

        service._repository.reset_mock()
        with (
            mock.patch.object(service, "_authenticated_administrator", return_value=caller),
            mock.patch(
                "hormuz.policy_control.load_policy_document",
                side_effect=PolicyControlError("policy_document_invalid"),
            ),
            self.assertRaises(PolicyControlError) as raised,
        ):
            service.apply(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                policy_path="invalid.json",
            )
        self.assertEqual(raised.exception.code, "policy_document_invalid")
        service._repository.apply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
