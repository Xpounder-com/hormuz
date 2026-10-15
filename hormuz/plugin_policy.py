"""Stage explicit policy changes without replacing a live connection owner."""
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import tempfile

from .config import GatewayConfig
from ._config_input import load_configuration_input
from .local_history_files import export_private, local_path


class CapabilityError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def configure(args):
    source = Path(args.config).expanduser().resolve()
    output = local_path(Path(args.output))
    if output.exists() or output.is_symlink():
        raise CapabilityError("output_exists")
    if output.parent.resolve() != source.parent:
        raise CapabilityError("output_must_share_configuration_directory")
    raw = load_configuration_input(source)
    changes = {}
    if args.local_only:
        raw.setdefault("route_admission", {})["mode"] = "local_only"
        changes["routing_mode"] = "local_only"
    if args.monthly_budget_usd is not None:
        try:
            amount = Decimal(args.monthly_budget_usd)
            if not amount.is_finite() or not 0 < amount <= Decimal("1000000") or amount.as_tuple().exponent < -6:
                raise InvalidOperation
        except InvalidOperation:
            raise CapabilityError("budget_invalid") from None
        raw.setdefault("policies", {}).setdefault("organization", {})["monthly_budget_usd"] = float(amount)
        changes["monthly_budget_usd"] = str(amount)
    if not changes:
        raise CapabilityError("policy_change_required")
    payload = json.dumps(raw, indent=2, sort_keys=True, allow_nan=False).encode()
    # Validate at the new configuration's actual relative-path base, without
    # publishing or modifying the selected configuration or live client.
    descriptor, temporary = tempfile.mkstemp(prefix=".hormuz-policy-", dir=source.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
        GatewayConfig.load(temporary)
    finally:
        os.unlink(temporary)
    if not args.dry_run:
        export_private(output, payload)
    return {"schema_id": "hormuz.policy-proposal", "schema_version": 1,
            "changes": changes, "activation": "not_active" if args.dry_run else "requires_reconnect",
            "scope": "intercepted_requests_only", "budget_basis": "configured_rate_card_estimate",
            "source_preserved": True, "history_consent_transferred": False,
            "next_action": "disconnect_then_connect_proposed_configuration"}
