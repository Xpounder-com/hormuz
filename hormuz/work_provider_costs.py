"""Read existing account-bound provider aggregates without pricing individual work.

The finance owner authenticates account selection, collection provenance and
reader roles. Its aggregate observations retain their original finality and
attestation boundaries. This adapter performs no provider/credential collection.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .finance_account_binding import FinanceAccountCandidate, select_finance_account
from .finance_collection import FinanceCollectionError
from .finance_collection_repository import create_finance_collection_repository
from .portfolio_config import authorize
from .portfolio_wire import PortfolioError


def account_cost_view(config, identity, now, *, repository_factory=create_finance_collection_repository):
    view = {"status": "unavailable", "reason": "account_cost_evidence_not_configured", "accounts": [],
            "scope": "organization_provider_accounts", "currency": "USD",
            "allocation": "not_allocated_to_jobs_or_requests", "invoice_finality": False,
            "coverage": "account_bound_provider_reported_aggregates"}
    if config is None or getattr(config, "portfolio_control", None) is None:
        return view
    try:
        principal = authorize(config.portfolio_control, identity)
        if "portfolio_admin" not in principal.roles:
            view["reason"] = "provider_evidence_authority_not_configured"
            return view
    except PortfolioError:
        view["reason"] = "provider_evidence_authority_not_configured"
        return view
    current = datetime.fromtimestamp(now, timezone.utc)
    end = current.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end.replace(day=1)
    view.update(period_start_at=start.isoformat().replace("+00:00", "Z"),
                period_end_at=end.isoformat().replace("+00:00", "Z"))
    if start == end:
        view["reason"] = "no_complete_utc_day_in_month"
        return view
    candidates = []
    for protocol in ("openai", "anthropic"):
        upstream = getattr(config, "upstreams", {}).get(protocol)
        if upstream is None:
            continue
        selected = select_finance_account(organization_id=identity.organization_id,
            protocol=protocol, base_url=upstream.base_url,
            identity=getattr(upstream, "finance_identity", None),
            bindings=getattr(config, "finance_account_bindings", None))
        if isinstance(selected, FinanceAccountCandidate):
            candidates.append((protocol, selected))
    if not candidates:
        return view
    try:
        repository = repository_factory(config)
    except FinanceCollectionError:
        view["reason"] = "provider_evidence_unavailable"
        return view
    for protocol, selected in candidates:
        row = {"provider": protocol, "account_binding_id": selected.binding.binding_id,
               "account_binding_version": selected.binding.binding_version,
               "status": "unavailable", "reason": "provider_evidence_unavailable",
               "provider_reported_amount": None, "gateway_estimated_amount": None,
               "currency": "USD", "invoice_finality": False,
               "allocation": "not_allocated_to_jobs_or_requests"}
        try:
            preview, missing, provenance, audit_id, reconciliation = repository.account_reconciliation_report_evidence(
                principal, account_binding_id=selected.binding.binding_id,
                account_binding_version=selected.binding.binding_version,
                collection_profile=protocol + ".organization-costs.v1",
                start_at=view["period_start_at"], end_at=view["period_end_at"], currency="USD")
            cost = preview.provider_cost
            row.update(status="observed" if cost.known_subtotal is not None else "unavailable",
                reason=None if cost.known_subtotal is not None else "provider_cost_not_observed",
                provider_reported_amount=cost.known_subtotal,
                gateway_estimated_amount=preview.gateway_estimate.known_subtotal,
                cost_basis=cost.cost_basis, provider_finality=cost.provider_final,
                invoice_finality=cost.invoice_final,
                numeric_selection_state=cost.numeric_selection_state,
                observed_bucket_count=cost.observed_bucket_count,
                empty_bucket_count=cost.empty_bucket_count, missing_bucket_count=cost.missing_bucket_count,
                observation_count=cost.observation_count,
                as_of_commit_sequence=preview.as_of_commit_sequence,
                terminal_attempts_missing_sidecar=missing, selected_snapshot_provenance=list(provenance),
                query_audit_event_id=audit_id, account_reconciliation=dict(reconciliation))
        except FinanceCollectionError:
            pass
        view["accounts"].append(row)
    observed = sum(row["status"] == "observed" for row in view["accounts"])
    view["status"] = "observed" if observed == len(view["accounts"]) else "partial" if observed else "unavailable"
    view["reason"] = None if observed else "provider_evidence_unavailable"
    return view
