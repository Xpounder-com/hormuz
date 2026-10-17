"""Freeze one new instruction treatment after a zero-inference native capture."""

import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import shutil
import subprocess
import time


DIRECTORY = Path(__file__).resolve().parent
REPOSITORY = DIRECTORY.parents[2]
ROOT = Path("/private/tmp/hormuz-537-native-v6-20261016")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def read(path):
    if path.stat().st_size > 2 * 1024 * 1024:
        raise RuntimeError("Preparation record exceeds two MiB")
    return json.loads(path.read_text())


def save(path, value):
    if path.exists():
        raise RuntimeError("Preserve existing freeze output: " + str(path))
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    path.chmod(0o600)


def main():
    if (ROOT / "frozen-manifest.json").exists() or (ROOT / "episode-started.json").exists():
        raise RuntimeError("This treatment is already frozen or consumed")
    spec = importlib.util.spec_from_file_location("v6_runner", DIRECTORY / "development-runner.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    previous = read(DIRECTORY.parent / "local-repair-20261016-v5/frozen-manifest.json")
    preflight = read(ROOT / "preflight.json")
    preparation = read(DIRECTORY / "preparation-evidence.json")
    if not (preflight["model_inferences"] == 0 and preflight["captured_submissions"] == 1
            and preflight["settings_restored"] and preflight["owned_listeners_closed"]
            and preparation["passed"] and preparation["native_model_inferences"] == 0):
        raise RuntimeError("Preparation or zero-inference cleanup failed")
    corpus = read(ROOT / "corpus.json")
    subject = json.loads(json.dumps(previous["plan"]["subject"]))
    profile = read(ROOT / "request-profile.json")
    subject["request_profile_sha256"] = sha(canonical(profile))
    old_profile = read(DIRECTORY.parent / "local-repair-20261016-v5/request-profile.json")
    changed = sorted(key for key in set(profile) | set(old_profile) if profile.get(key) != old_profile.get(key))
    if changed != ["input_instructions"]:
        raise RuntimeError("The instruction treatment changed other request controls")
    if any(tool["type"] != "function" for tool in profile["tools"]):
        raise RuntimeError("The frozen runtime permits only inline function tools")
    plan = {"criteria_version": "local-repair.v1", "subject": subject,
            "cases": [{key: row[key] for key in ("case_id", "partition", "family", "task_sha256", "checks_sha256")}
                      for row in corpus["entries"]]}
    plan_sha256 = sha(canonical(plan))
    save(ROOT / "coding-plan.json", plan)
    first = corpus["entries"][0]
    if first["case_id"] != "dev-clamp" or len(plan["cases"]) != 16:
        raise RuntimeError("Only the declared development case is authorized")
    probe = {"schema_id": "hormuz.coding-probe-plan", "schema_version": 2, "campaign_id": corpus["campaign_id"],
             "plan_sha256": plan_sha256, "case_id": first["case_id"], "task_sha256": first["task_sha256"],
             "checks_sha256": first["checks_sha256"], "model": read(ROOT / "setup-profile.json")["model"],
             "subject": subject, "first_input_sha256": sha(canonical(runner.semantic_input(read(ROOT / "first-input.json")))),
             "max_requests": 8, "max_elapsed_seconds": 90}
    save(ROOT / "probe-plan.json", probe)
    save(ROOT / "candidate.json", {"document_version": 2, "candidate_id": "codex0160-m5-32g-qwen3-4b-shell-patch-v6",
        "status": "experimental_unqualified", "subject": subject, "assets": read(ROOT / "pins.json"),
        "hormuz_runtime": read(ROOT / "runtime-equivalence.json"), "automatic_admission_effect": "none",
        "coding_receipt_emitted": False})
    save(ROOT / "treatment.json", {"document_version": 1, "parent_campaign": "local-repair-20261016-v5",
        "intentional_change": "explicit developer guidance for existing exec_command shell-patch syntax",
        "changed_request_profile_fields": changed,
        "developer_instructions_sha256": sha((ROOT / "developer-instructions.txt").read_bytes()),
        "unchanged": ["client executable", "host", "llama.cpp archive/executable", "model bytes", "136 Hormuz runtime files",
                      "base instructions", "tool schemas", "generation controls", "512-token limit", "90-second deadline",
                      "first development task and checker", "local-only policy", "conversation affinity", "shadow-only authority"],
        "incidental_changes": ["private filesystem paths and native message/conversation identifiers", "fresh held-out inventory"],
        "causal_limit": "One stochastic development observation cannot establish a general causal effect or qualification."})
    shutil.copyfile(DIRECTORY / "development-runner.py", ROOT / "development-runner.py")
    artifacts = []
    paths = [ROOT / name for name in ("pins.json", "setup-profile.json", "corpus.json", "developer-instructions.txt",
        "first-input.json", "request-profile.json", "captured-request.json", "wire-observation.json", "prompt.txt", "oracle.py",
        "runtime-equivalence.json", "host-verification.json", "preflight.json", "coding-plan.json", "probe-plan.json",
        "candidate.json", "treatment.json", "development-runner.py")]
    paths += [ROOT / "project/solution.py", runner.SOURCE_PACKAGE, DIRECTORY / "prepare-corpus.py",
              DIRECTORY / "validate-preparation.py", DIRECTORY / "preparation-evidence.json", Path(__file__).resolve()]
    paths += sorted(path for path in (ROOT / "installed").rglob("*") if path.is_file() and path.suffix != ".pyc")
    for path in paths:
        data = path.read_bytes()
        artifacts.append({"path": str(path), "sha256": sha(data), "bytes": len(data)})
    expected = {row["path"].split("/installed/", 1)[1]: row["sha256"]
                for row in previous["artifacts"] if "/installed/" in row["path"]}
    actual = {row["path"].split("/installed/", 1)[1]: row["sha256"] for row in artifacts if "/installed/" in row["path"]}
    if actual != expected:
        raise RuntimeError("Runtime equivalence drifted before freezing")
    manifest = {"document_version": 4, "campaign_id": corpus["campaign_id"], "qualification_issue": 537,
        "source_commit": previous["source_commit"],
        "experiment_source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPOSITORY, text=True, timeout=3).strip(),
        "validation_main_commit": "90d4b541dd227d468eeafd1ba298062d715cdafc",
        "coding_evidence_schema_version": 2, "distribution": "exact source package reconstructed and matched to all 136 v5 runtime hashes",
        "python_version": platform.python_version(), "python_executable": str(runner.PYTHON), "plan": plan,
        "plan_sha256": plan_sha256, "runner_sha256": sha((ROOT / "development-runner.py").read_bytes()),
        "assets": read(ROOT / "pins.json"), "frozen_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "budget": {"authorized_case": "dev-clamp", "native_episodes": 1, "max_requests": 8, "max_elapsed_seconds": 90,
                   "transport_retries": 0, "quality_retries": 0, "held_out_episodes_authorized": 0},
        "native_controls": {**previous["native_controls"],
            "sandbox_workspace_write.writable_roots": [str(ROOT / "tmp")],
            "developer_instructions_sha256": sha((ROOT / "developer-instructions.txt").read_bytes())},
        "history": "Original failures and invalid treatments, including v5's failed development repair, remain unchanged. V5 informs the new development guidance; twelve fresh held-out tasks remain unrun and untuned.",
        "freeze_rule": "After inference, any change requires a new campaign and fresh held-out inventory. Preserve every failure and invalid treatment.",
        "automatic_admission_effect": "none", "coding_receipt_emitted": False, "artifacts": artifacts}
    save(ROOT / "frozen-manifest.json", manifest)
    print(json.dumps({"frozen_manifest_sha256": sha((ROOT / "frozen-manifest.json").read_bytes()),
        "plan_sha256": plan_sha256, "artifacts": len(artifacts), "runtime_files_match_v5": len(actual),
        "native_model_inferences": 0, "authorized_native_episodes": 1}))


if __name__ == "__main__":
    main()
