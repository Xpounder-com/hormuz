from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from . import __version__
from .context import (
    ContextError,
    ContextPackRequest,
    ContextPrincipal,
    ContextRecord,
    build_context_pack,
    estimate_record_tokens,
)


CORPUS_SCHEMA = "hormuz.context-benchmark-corpus.v1"
REFERENCE_SCHEMA = "hormuz.context-benchmark-references.v1"
RESULT_SCHEMA = "hormuz.context-benchmark-result.v1"
DEFAULT_CORPUS_PATH = Path(__file__).resolve().parent / "benchmark_data" / "context-corpus.v1.json"
DEFAULT_REFERENCES_PATH = (
    Path(__file__).resolve().parent / "benchmark_data" / "context-references.v1.json"
)
MAX_BENCHMARK_FILE_BYTES = 32 * 1024 * 1024
BASELINES = ("no_memory", "full_history", "simple_lexical", "hormuz_governed")
_TERMS = re.compile(r"[a-z0-9]+")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")

REGRESSION_THRESHOLDS: dict[str, tuple[str, float]] = {
    "corpus.leakage_review_failures": ("max", 0),
    "hormuz_governed.authorization_leak_task_rate": ("max", 0),
    "hormuz_governed.lifecycle_stale_task_rate": ("max", 0),
    "hormuz_governed.token_budget_violations": ("max", 0),
    "hormuz_governed.determinism_failures": ("max", 0),
    "hormuz_governed.p95_latency_ms": ("max", 500),
}

RELEASE_THRESHOLDS: dict[str, tuple[str, float]] = {
    **REGRESSION_THRESHOLDS,
    "hormuz_governed.precision": ("min", 0.90),
    "hormuz_governed.recall": ("min", 0.95),
    "hormuz_governed.useful_pack_rate": ("min", 0.90),
    "hormuz_governed.stale_selection_task_rate": ("max", 0),
    "hormuz_governed.dependency_stale_challenge_rate": ("max", 0),
    "hormuz_governed.malicious_challenge_selection_rate": ("max", 0),
    "hormuz_governed.contradiction_challenge_selection_rate": ("max", 0),
    "hormuz_governed.p95_latency_ms": ("max", 500),
}


class ContextBenchmarkError(ValueError):
    pass


@dataclass(frozen=True)
class BenchmarkTask:
    task_id: str
    category: str
    challenge: str
    ci: bool
    query: str
    token_budget: int
    max_items: int
    policy_version: str
    as_of: datetime
    principal: ContextPrincipal
    repository_revision: str
    memory_snapshot_sha256: str
    records: tuple[ContextRecord, ...]


@dataclass(frozen=True)
class BenchmarkReference:
    task_id: str
    relevant_record_ids: frozenset[str]
    unauthorized_record_ids: frozenset[str]
    lifecycle_stale_record_ids: frozenset[str]
    dependency_stale_record_ids: frozenset[str]
    malicious_record_ids: frozenset[str]
    contradictory_record_ids: frozenset[str]
    reference_outcome: str
    reference_outcome_sha256: str


@dataclass(frozen=True)
class LoadedBenchmark:
    corpus_id: str
    corpus_sha256: str
    reference_sha256: str
    seed: int
    generated_at: str
    tasks: tuple[BenchmarkTask, ...]
    references: dict[str, BenchmarkReference]
    leakage_failures: tuple[str, ...]


@dataclass(frozen=True)
class Selection:
    ids: tuple[str, ...]
    estimated_tokens: int
    pack_id: str | None = None
    manifest: dict[str, Any] | None = None


Selector = Callable[[BenchmarkTask], Selection]


def run_benchmark(
    corpus_path: str | Path,
    references_path: str | Path,
    *,
    profile: str = "report",
    ci_subset: bool = False,
    iterations: int = 1,
) -> tuple[dict[str, Any], int]:
    if profile not in {"report", "regression", "release"}:
        raise ContextBenchmarkError("benchmark profile must be report, regression, or release")
    if isinstance(iterations, bool) or not isinstance(iterations, int) or not 1 <= iterations <= 100:
        raise ContextBenchmarkError("benchmark iterations must be between 1 and 100")
    loaded = load_benchmark(corpus_path, references_path, ci_subset=ci_subset)
    if not loaded.tasks:
        raise ContextBenchmarkError("benchmark selection contains no tasks")

    selectors: dict[str, Selector] = {
        "no_memory": _select_no_memory,
        "full_history": _select_full_history,
        "simple_lexical": _select_simple_lexical,
        "hormuz_governed": _select_hormuz,
    }
    selections: dict[str, dict[str, Selection]] = {name: {} for name in BASELINES}
    latencies: dict[str, list[float]] = {name: [] for name in BASELINES}
    determinism_failures: dict[str, set[str]] = {name: set() for name in BASELINES}

    for task in loaded.tasks:
        for baseline, selector in selectors.items():
            first: Selection | None = None
            for _iteration in range(iterations):
                started = time.perf_counter_ns()
                try:
                    selected = selector(task)
                except ContextError as error:
                    raise ContextBenchmarkError(
                        f"benchmark task {task.task_id} failed in {baseline}: {error}"
                    ) from error
                elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
                latencies[baseline].append(elapsed_ms)
                if first is None:
                    first = selected
                elif selected != first:
                    determinism_failures[baseline].add(task.task_id)
            if first is None:  # pragma: no cover - iterations is validated positive
                raise ContextBenchmarkError("benchmark selector produced no result")
            selections[baseline][task.task_id] = first

    metrics = {
        baseline: _aggregate_metrics(
            loaded,
            selections[baseline],
            latencies[baseline],
            determinism_failures[baseline],
        )
        for baseline in BASELINES
    }
    thresholds = _thresholds(profile)
    threshold_results = _evaluate_thresholds(loaded, metrics, thresholds)
    passed = all(item["passed"] for item in threshold_results)
    status = "reported" if profile == "report" else ("passed" if passed else "failed")

    task_evidence: list[dict[str, Any]] = []
    for task in loaded.tasks:
        reference = loaded.references[task.task_id]
        task_evidence.append(
            {
                "task_id": task.task_id,
                "category": task.category,
                "challenge": task.challenge,
                "repository_revision": task.repository_revision,
                "memory_snapshot_sha256": task.memory_snapshot_sha256,
                "relevant_record_count": len(reference.relevant_record_ids),
                "selected": {
                    baseline: {
                        "record_ids": list(selections[baseline][task.task_id].ids),
                        "estimated_tokens": selections[baseline][task.task_id].estimated_tokens,
                        "pack_id": selections[baseline][task.task_id].pack_id,
                    }
                    for baseline in BASELINES
                },
            }
        )

    result: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA,
        "status": status,
        "profile": profile,
        "generated_at": datetime.now().astimezone().isoformat(),
        "runner": {
            "hormuz_version": __version__,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "iterations": iterations,
        },
        "corpus": {
            "schema_version": CORPUS_SCHEMA,
            "corpus_id": loaded.corpus_id,
            "corpus_sha256": loaded.corpus_sha256,
            "reference_sha256": loaded.reference_sha256,
            "seed": loaded.seed,
            "generated_at": loaded.generated_at,
            "task_count": len(loaded.tasks),
            "category_counts": _count_values(task.category for task in loaded.tasks),
            "challenge_counts": _count_values(task.challenge for task in loaded.tasks),
            "ci_subset": ci_subset,
            "leakage_review_method": "separate-outcome exact-text and SHA-256 v1",
            "leakage_review_failures": len(loaded.leakage_failures),
            "leakage_failure_task_ids": list(loaded.leakage_failures),
        },
        "baselines": metrics,
        "thresholds": threshold_results,
        "contract_observations": {
            "authorization_before_governed_ranking": True,
            "repository_revision_in_pack_manifest": False,
            "contradiction_outcome_explicit": False,
            "dependency_invalidation_automatic": False,
            "malicious_context_quarantine": False,
            "note": (
                "False values are current measured contract gaps, not benchmark failures hidden by the runner."
            ),
        },
        "tasks": task_evidence,
    }
    return result, 0 if profile == "report" or passed else 2


def load_benchmark(
    corpus_path: str | Path,
    references_path: str | Path,
    *,
    ci_subset: bool = False,
) -> LoadedBenchmark:
    corpus_file = Path(corpus_path).expanduser().resolve()
    reference_file = Path(references_path).expanduser().resolve()
    corpus_raw, _corpus_bytes = _read_json_file(corpus_file, "benchmark corpus")
    reference_raw, reference_bytes = _read_json_file(reference_file, "benchmark references")
    if not isinstance(corpus_raw, dict) or corpus_raw.get("schema_version") != CORPUS_SCHEMA:
        raise ContextBenchmarkError(f"benchmark corpus must use {CORPUS_SCHEMA}")
    if not isinstance(reference_raw, dict) or reference_raw.get("schema_version") != REFERENCE_SCHEMA:
        raise ContextBenchmarkError(f"benchmark references must use {REFERENCE_SCHEMA}")
    _require_exact_fields(
        corpus_raw,
        {"schema_version", "corpus_id", "seed", "generated_at", "tasks"},
        "benchmark corpus",
    )
    _require_exact_fields(
        reference_raw,
        {"schema_version", "corpus_id", "corpus_sha256", "outcomes"},
        "benchmark references",
    )
    corpus_id = _required_string(corpus_raw, "corpus_id", "benchmark corpus")
    if reference_raw.get("corpus_id") != corpus_id:
        raise ContextBenchmarkError("benchmark reference corpus_id does not match the corpus")
    expected_corpus_sha = reference_raw.get("corpus_sha256")
    corpus_sha = hashlib.sha256(_canonical_json_bytes(corpus_raw)).hexdigest()
    if expected_corpus_sha != corpus_sha:
        raise ContextBenchmarkError("benchmark reference corpus_sha256 does not match the corpus")
    seed = corpus_raw.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ContextBenchmarkError("benchmark corpus seed must be a non-negative integer")
    generated_at = _required_string(corpus_raw, "generated_at", "benchmark corpus")
    _parse_datetime(generated_at, "benchmark corpus generated_at")

    tasks_raw = corpus_raw.get("tasks")
    outcomes_raw = reference_raw.get("outcomes")
    if not isinstance(tasks_raw, list) or not isinstance(outcomes_raw, list):
        raise ContextBenchmarkError("benchmark tasks and outcomes must be arrays")
    tasks_by_id: dict[str, BenchmarkTask] = {}
    for index, value in enumerate(tasks_raw):
        task = _parse_task(value, index)
        if task.task_id in tasks_by_id:
            raise ContextBenchmarkError(f"duplicate benchmark task_id: {task.task_id}")
        tasks_by_id[task.task_id] = task

    references: dict[str, BenchmarkReference] = {}
    for index, value in enumerate(outcomes_raw):
        reference = _parse_reference(value, index)
        if reference.task_id in references:
            raise ContextBenchmarkError(f"duplicate benchmark outcome task_id: {reference.task_id}")
        references[reference.task_id] = reference
    if set(tasks_by_id) != set(references):
        missing = sorted(set(tasks_by_id) ^ set(references))
        raise ContextBenchmarkError("benchmark task/outcome IDs differ: " + ", ".join(missing))

    leakage_failures: list[str] = []
    for task_id, task in tasks_by_id.items():
        reference = references[task_id]
        record_ids = {record.record_id for record in task.records}
        labeled_ids = (
            reference.relevant_record_ids
            | reference.unauthorized_record_ids
            | reference.lifecycle_stale_record_ids
            | reference.dependency_stale_record_ids
            | reference.malicious_record_ids
            | reference.contradictory_record_ids
        )
        unknown_ids = sorted(labeled_ids - record_ids)
        if unknown_ids:
            raise ContextBenchmarkError(
                f"benchmark outcome {task_id} references unknown records: {', '.join(unknown_ids)}"
            )
        normalized_outcome = _normalize_text(reference.reference_outcome)
        for record in task.records:
            candidate = _normalize_text(record.title + " " + record.content)
            if normalized_outcome and normalized_outcome in candidate:
                leakage_failures.append(task_id)
                break
        if any(
            hashlib.sha256(record.content.encode("utf-8")).hexdigest()
            == reference.reference_outcome_sha256
            for record in task.records
        ):
            leakage_failures.append(task_id)

    selected_tasks = tuple(
        task
        for task in tasks_by_id.values()
        if not ci_subset or task.ci
    )
    return LoadedBenchmark(
        corpus_id=corpus_id,
        corpus_sha256=corpus_sha,
        reference_sha256=hashlib.sha256(reference_bytes).hexdigest(),
        seed=seed,
        generated_at=generated_at,
        tasks=selected_tasks,
        references=references,
        leakage_failures=tuple(sorted(set(leakage_failures))),
    )


def write_benchmark_result(value: dict[str, Any], output: str, *, force: bool = False) -> None:
    serialized = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if output == "-":
        sys.stdout.write(serialized)
        return
    path = Path(output).expanduser().absolute()
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | (os.O_TRUNC if force else os.O_EXCL)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as error:
        raise ContextBenchmarkError(f"benchmark output already exists: {path}") from error
    except OSError as error:
        raise ContextBenchmarkError(f"cannot create benchmark output: {path}") from error
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ContextBenchmarkError(f"cannot write benchmark output: {path}") from error


def _parse_task(value: object, index: int) -> BenchmarkTask:
    label = f"benchmark tasks[{index}]"
    if not isinstance(value, dict):
        raise ContextBenchmarkError(f"{label} must be an object")
    _require_exact_fields(
        value,
        {
            "task_id",
            "category",
            "challenge",
            "ci",
            "query",
            "token_budget",
            "max_items",
            "policy_version",
            "as_of",
            "principal",
            "repository_snapshot",
            "memory_snapshot_sha256",
            "records",
        },
        label,
    )
    task_id = _required_string(value, "task_id", label)
    category = _required_string(value, "category", label)
    challenge = _required_string(value, "challenge", label)
    ci = value.get("ci")
    if not isinstance(ci, bool):
        raise ContextBenchmarkError(f"{label}.ci must be a boolean")
    query = _required_string(value, "query", label)
    token_budget = _positive_integer(value.get("token_budget"), f"{label}.token_budget", 1_000_000)
    max_items = _positive_integer(value.get("max_items"), f"{label}.max_items", 100)
    policy_version = _required_string(value, "policy_version", label)
    as_of = _parse_datetime(value.get("as_of"), f"{label}.as_of")
    principal_raw = value.get("principal")
    if not isinstance(principal_raw, dict):
        raise ContextBenchmarkError(f"{label}.principal must be an object")
    _require_exact_fields(
        principal_raw,
        {"organization_id", "team_id", "actor_id", "clearance", "repository_id", "branch"},
        f"{label}.principal",
    )
    try:
        principal = ContextPrincipal(
            organization_id=_required_string(principal_raw, "organization_id", f"{label}.principal"),
            team_id=_required_string(principal_raw, "team_id", f"{label}.principal"),
            actor_id=_required_string(principal_raw, "actor_id", f"{label}.principal"),
            clearance=_required_string(principal_raw, "clearance", f"{label}.principal"),
            repository_id=_required_string(principal_raw, "repository_id", f"{label}.principal"),
            branch=_required_string(principal_raw, "branch", f"{label}.principal"),
        )
    except ContextError as error:
        raise ContextBenchmarkError(f"invalid {label}.principal: {error}") from error
    snapshot = value.get("repository_snapshot")
    if not isinstance(snapshot, dict):
        raise ContextBenchmarkError(f"{label}.repository_snapshot must be an object")
    _require_exact_fields(
        snapshot,
        {"repository_id", "branch", "revision"},
        f"{label}.repository_snapshot",
    )
    if snapshot.get("repository_id") != principal.repository_id or snapshot.get("branch") != principal.branch:
        raise ContextBenchmarkError(f"{label} repository snapshot does not match principal scope")
    repository_revision = _required_string(snapshot, "revision", f"{label}.repository_snapshot")
    if not _SHA256.fullmatch(repository_revision):
        raise ContextBenchmarkError(f"{label}.repository_snapshot.revision must be a SHA-256 value")
    records_raw = value.get("records")
    if not isinstance(records_raw, list) or not records_raw:
        raise ContextBenchmarkError(f"{label}.records must be a non-empty array")
    if any(not isinstance(item, dict) for item in records_raw):
        raise ContextBenchmarkError(f"{label}.records must contain objects")
    memory_snapshot_sha256 = value.get("memory_snapshot_sha256")
    calculated_memory_sha = hashlib.sha256(_canonical_json_bytes(records_raw)).hexdigest()
    if memory_snapshot_sha256 != calculated_memory_sha:
        raise ContextBenchmarkError(f"{label}.memory_snapshot_sha256 does not match records")
    records: list[ContextRecord] = []
    try:
        for item in records_raw:
            record = ContextRecord.from_dict(item)
            if record.source_revision != f"git:{repository_revision}":
                raise ContextBenchmarkError(
                    f"{label} record {record.record_id} is not frozen to repository revision"
                )
            records.append(record)
    except ContextError as error:
        raise ContextBenchmarkError(f"invalid {label} record: {error}") from error
    try:
        ContextPackRequest(
            query=query,
            principal=principal,
            token_budget=token_budget,
            policy_version=policy_version,
            max_items=max_items,
            as_of=as_of,
        )
    except ContextError as error:
        raise ContextBenchmarkError(f"invalid {label} request: {error}") from error
    return BenchmarkTask(
        task_id=task_id,
        category=category,
        challenge=challenge,
        ci=ci,
        query=query,
        token_budget=token_budget,
        max_items=max_items,
        policy_version=policy_version,
        as_of=as_of,
        principal=principal,
        repository_revision=repository_revision,
        memory_snapshot_sha256=calculated_memory_sha,
        records=tuple(records),
    )


def _parse_reference(value: object, index: int) -> BenchmarkReference:
    label = f"benchmark outcomes[{index}]"
    if not isinstance(value, dict):
        raise ContextBenchmarkError(f"{label} must be an object")
    fields = {
        "task_id",
        "relevant_record_ids",
        "unauthorized_record_ids",
        "lifecycle_stale_record_ids",
        "dependency_stale_record_ids",
        "malicious_record_ids",
        "contradictory_record_ids",
        "reference_outcome",
        "reference_outcome_sha256",
    }
    _require_exact_fields(value, fields, label)
    reference_outcome = _required_string(value, "reference_outcome", label)
    expected_sha = hashlib.sha256(reference_outcome.encode("utf-8")).hexdigest()
    if value.get("reference_outcome_sha256") != expected_sha:
        raise ContextBenchmarkError(f"{label}.reference_outcome_sha256 does not match outcome")
    return BenchmarkReference(
        task_id=_required_string(value, "task_id", label),
        relevant_record_ids=_string_set(value, "relevant_record_ids", label, require_nonempty=True),
        unauthorized_record_ids=_string_set(value, "unauthorized_record_ids", label),
        lifecycle_stale_record_ids=_string_set(value, "lifecycle_stale_record_ids", label),
        dependency_stale_record_ids=_string_set(value, "dependency_stale_record_ids", label),
        malicious_record_ids=_string_set(value, "malicious_record_ids", label),
        contradictory_record_ids=_string_set(value, "contradictory_record_ids", label),
        reference_outcome=reference_outcome,
        reference_outcome_sha256=expected_sha,
    )


def _select_no_memory(task: BenchmarkTask) -> Selection:
    return Selection(ids=(), estimated_tokens=0)


def _select_full_history(task: BenchmarkTask) -> Selection:
    return _bounded_selection(task.records, task.token_budget, task.max_items)


def _select_simple_lexical(task: BenchmarkTask) -> Selection:
    terms = set(_TERMS.findall(task.query.lower()))
    ranked: list[tuple[int, str, ContextRecord]] = []
    for record in task.records:
        text = " ".join((record.title, record.content, " ".join(record.tags))).lower()
        score = sum(text.count(term) for term in terms)
        if score > 0:
            ranked.append((score, record.record_id, record))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return _bounded_selection(
        (item[2] for item in ranked),
        task.token_budget,
        task.max_items,
    )


def _select_hormuz(task: BenchmarkTask) -> Selection:
    request = ContextPackRequest(
        query=task.query,
        principal=task.principal,
        token_budget=task.token_budget,
        policy_version=task.policy_version,
        max_items=task.max_items,
        as_of=task.as_of,
    )
    pack = build_context_pack(task.records, request)
    return Selection(
        ids=tuple(item.record.record_id for item in pack.items),
        estimated_tokens=pack.estimated_tokens,
        pack_id=pack.pack_id,
        manifest=pack.to_dict(),
    )


def _bounded_selection(
    records: Iterable[ContextRecord],
    token_budget: int,
    max_items: int,
) -> Selection:
    selected: list[str] = []
    estimated_tokens = 0
    for record in records:
        record_tokens = estimate_record_tokens(record)
        if estimated_tokens + record_tokens > token_budget:
            continue
        selected.append(record.record_id)
        estimated_tokens += record_tokens
        if len(selected) >= max_items:
            break
    return Selection(ids=tuple(selected), estimated_tokens=estimated_tokens)


def _aggregate_metrics(
    loaded: LoadedBenchmark,
    selections: dict[str, Selection],
    latencies: list[float],
    determinism_failures: set[str],
) -> dict[str, Any]:
    true_positive = 0
    selected_total = 0
    relevant_total = 0
    authorization_leak_tasks = 0
    lifecycle_stale_tasks = 0
    stale_tasks = 0
    dependency_challenges = 0
    dependency_selected = 0
    malicious_challenges = 0
    malicious_selected = 0
    contradiction_challenges = 0
    contradiction_selected = 0
    useful_tasks = 0
    token_budget_violations = 0
    compression_ratios: list[float] = []
    for task in loaded.tasks:
        selection = selections[task.task_id]
        reference = loaded.references[task.task_id]
        selected = set(selection.ids)
        relevant = reference.relevant_record_ids
        true_positive += len(selected & relevant)
        selected_total += len(selected)
        relevant_total += len(relevant)
        unauthorized = bool(selected & reference.unauthorized_record_ids)
        lifecycle_stale = bool(selected & reference.lifecycle_stale_record_ids)
        dependency_stale = bool(selected & reference.dependency_stale_record_ids)
        malicious = bool(selected & reference.malicious_record_ids)
        contradiction = bool(selected & reference.contradictory_record_ids)
        authorization_leak_tasks += int(unauthorized)
        lifecycle_stale_tasks += int(lifecycle_stale)
        stale_tasks += int(lifecycle_stale or dependency_stale)
        if reference.dependency_stale_record_ids:
            dependency_challenges += 1
            dependency_selected += int(dependency_stale)
        if reference.malicious_record_ids:
            malicious_challenges += 1
            malicious_selected += int(malicious)
        if reference.contradictory_record_ids:
            contradiction_challenges += 1
            contradiction_selected += int(contradiction)
        hazards = unauthorized or lifecycle_stale or dependency_stale or malicious or contradiction
        useful_tasks += int(bool(relevant) and relevant <= selected and not hazards)
        token_budget_violations += int(selection.estimated_tokens > task.token_budget)
        full_tokens = sum(estimate_record_tokens(record) for record in task.records)
        compression_ratios.append(1 - (selection.estimated_tokens / full_tokens) if full_tokens else 1.0)
    task_count = len(loaded.tasks)
    return {
        "task_count": task_count,
        "selected_record_count": selected_total,
        "precision": _ratio(true_positive, selected_total),
        "recall": _ratio(true_positive, relevant_total),
        "useful_pack_rate": _ratio(useful_tasks, task_count),
        "authorization_leak_task_rate": _ratio(authorization_leak_tasks, task_count),
        "lifecycle_stale_task_rate": _ratio(lifecycle_stale_tasks, task_count),
        "stale_selection_task_rate": _ratio(stale_tasks, task_count),
        "dependency_stale_challenge_rate": _ratio(dependency_selected, dependency_challenges),
        "malicious_challenge_selection_rate": _ratio(malicious_selected, malicious_challenges),
        "contradiction_challenge_selection_rate": _ratio(
            contradiction_selected,
            contradiction_challenges,
        ),
        "mean_compression_ratio": round(sum(compression_ratios) / len(compression_ratios), 6),
        "p50_latency_ms": _percentile(latencies, 50),
        "p95_latency_ms": _percentile(latencies, 95),
        "token_budget_violations": token_budget_violations,
        "determinism_failures": len(determinism_failures),
        "determinism_failure_task_ids": sorted(determinism_failures),
    }


def _thresholds(profile: str) -> dict[str, tuple[str, float]]:
    if profile == "regression":
        return REGRESSION_THRESHOLDS
    if profile == "release":
        return RELEASE_THRESHOLDS
    return {}


def _evaluate_thresholds(
    loaded: LoadedBenchmark,
    metrics: dict[str, dict[str, Any]],
    thresholds: dict[str, tuple[str, float]],
) -> list[dict[str, Any]]:
    values: dict[str, Any] = {
        "corpus.leakage_review_failures": len(loaded.leakage_failures),
    }
    for baseline, baseline_metrics in metrics.items():
        for name, value in baseline_metrics.items():
            values[f"{baseline}.{name}"] = value
    results: list[dict[str, Any]] = []
    for metric, (operator, target) in thresholds.items():
        value = values.get(metric)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ContextBenchmarkError(f"benchmark threshold references non-numeric metric: {metric}")
        passed = value <= target if operator == "max" else value >= target
        results.append(
            {
                "metric": metric,
                "operator": operator,
                "target": target,
                "value": value,
                "passed": passed,
            }
        )
    return results


def _read_json_file(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        size = path.stat().st_size
    except FileNotFoundError as error:
        raise ContextBenchmarkError(f"{label} does not exist: {path}") from error
    except OSError as error:
        raise ContextBenchmarkError(f"cannot inspect {label}: {path}") from error
    if size > MAX_BENCHMARK_FILE_BYTES:
        raise ContextBenchmarkError(f"{label} exceeds the 32 MiB limit")
    try:
        data = path.read_bytes()
        if len(data) > MAX_BENCHMARK_FILE_BYTES:
            raise ContextBenchmarkError(f"{label} exceeds the 32 MiB limit")
        value = json.loads(data, parse_constant=_reject_json_constant)
    except ContextBenchmarkError:
        raise
    except OSError as error:
        raise ContextBenchmarkError(f"cannot read {label}: {path}") from error
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
        raise ContextBenchmarkError(f"{label} is not valid strict JSON") from error
    if not isinstance(value, dict):
        raise ContextBenchmarkError(f"{label} must be a JSON object")
    return value, data


def _require_exact_fields(value: dict[str, Any], fields: set[str], label: str) -> None:
    unknown = sorted(set(value) - fields)
    missing = sorted(fields - set(value))
    if unknown:
        raise ContextBenchmarkError(f"unknown {label} fields: {', '.join(unknown)}")
    if missing:
        raise ContextBenchmarkError(f"missing {label} fields: {', '.join(missing)}")


def _required_string(value: dict[str, Any], field: str, label: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise ContextBenchmarkError(f"{label}.{field} must be a non-empty string")
    return item


def _positive_integer(value: object, label: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ContextBenchmarkError(f"{label} must be an integer between 1 and {maximum}")
    return value


def _parse_datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ContextBenchmarkError(f"{label} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContextBenchmarkError(f"{label} must be a valid ISO-8601 datetime") from error
    if parsed.tzinfo is None:
        raise ContextBenchmarkError(f"{label} must include a timezone")
    return parsed


def _string_set(
    value: dict[str, Any],
    field: str,
    label: str,
    *,
    require_nonempty: bool = False,
) -> frozenset[str]:
    items = value.get(field)
    if not isinstance(items, list) or any(not isinstance(item, str) or not item for item in items):
        raise ContextBenchmarkError(f"{label}.{field} must be an array of non-empty strings")
    result = frozenset(items)
    if len(result) != len(items):
        raise ContextBenchmarkError(f"{label}.{field} cannot contain duplicates")
    if require_nonempty and not result:
        raise ContextBenchmarkError(f"{label}.{field} cannot be empty")
    return result


def _count_values(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil((percentile / 100) * len(ordered)) - 1)
    return round(ordered[index], 6)


def _normalize_text(value: str) -> str:
    return " ".join(_TERMS.findall(value.lower()))


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")
