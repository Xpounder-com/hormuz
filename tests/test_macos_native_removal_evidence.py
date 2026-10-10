"""Pure removal records and mocked collector ownership; never run a native app."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from tools import macos_pilot_operations as operations
from tools import verify_macos_pilot_evidence as pilot
from tests.test_macos_pilot_operations import _fixture, _inputs, _run
from tests import test_macos_pilot_evidence as evidence_tests


ROOT = Path(__file__).resolve().parents[1]
SOURCE = "a" * 40
RUN = "https://github.com/Xpounder-com/hormuz/actions/runs/5"
ARTIFACT = _inputs()["candidate"]
NOW = datetime(2026, 9, 1, 18, tzinfo=timezone.utc)


def native(*, removed=0, retained=2):
    return {
        "schema_id": "hormuz.native-removal-result", "schema_version": 1,
        "status": "complete_with_retained_files" if retained else "complete",
        "session_was_present": False, "pending": False,
        "server_revocation_confirmed": False, "keychain_session_absent": True,
        "generated_setup_absent": True, "removed_files": removed,
        "retained_files": retained, "coordination_locks_retained": True,
        "appearance_reset": False,
    }


def removal(*, retained=2):
    return {
        "schema_id": pilot.NATIVE_REMOVAL_SCHEMA_ID, "schema_version": 1,
        "source_commit": SOURCE, "workflow_run_url": RUN,
        "artifact_sha256": ARTIFACT["archive_sha256"],
        "version": ARTIFACT["version"], "build": ARTIFACT["build"],
        "started_at": "2026-09-01T10:02:00Z", "completed_at": "2026-09-01T10:03:00Z",
        "native_apply": native(removed=4, retained=retained),
        "native_verify": native(retained=retained),
        "prior_server_revocation_denied": True, "prior_sign_out_verified": True,
        "owned_app_process_stopped": True, "installed_bundle_removed": True,
    }


def validate(value, **overrides):
    return pilot.validate_native_removal(value, **{
        "source_commit": SOURCE, "artifact_sha256": ARTIFACT["archive_sha256"],
        "version": ARTIFACT["version"], "build": ARTIFACT["build"],
        "workflow_run_url": RUN, **overrides,
    })


class NativeRemovalEvidenceTests(unittest.TestCase):
    def test_retained_files_are_explicit_and_empty_custody_never_claims_server_logout(self):
        for retained in (0, 2, 4096):
            record = removal(retained=retained)
            self.assertEqual(validate(record), record)
            self.assertFalse(record["native_verify"]["server_revocation_confirmed"])
            self.assertFalse(record["native_apply"]["session_was_present"])

    def test_pending_or_missing_observations_are_not_removal_proof(self):
        for name in ("native_apply", "native_verify"):
            for field, bad in (("pending", True), ("keychain_session_absent", False),
                               ("generated_setup_absent", False), ("coordination_locks_retained", False),
                               ("appearance_reset", True)):
                record = removal(); record[name][field] = bad
                with self.subTest(name=name, field=field), self.assertRaises(pilot.MacPilotEvidenceError):
                    validate(record)
        for field in ("prior_server_revocation_denied", "prior_sign_out_verified",
                      "owned_app_process_stopped", "installed_bundle_removed"):
            record = removal(); record[field] = False
            with self.subTest(field=field), self.assertRaises(pilot.MacPilotEvidenceError):
                validate(record)

    def test_revocation_and_read_only_verification_are_distinct(self):
        for was_present, confirmed in ((False, True), (True, False)):
            record = removal()
            record["native_apply"].update(session_was_present=was_present,
                                           server_revocation_confirmed=confirmed)
            with self.assertRaisesRegex(pilot.MacPilotEvidenceError, "revocation_invalid"):
                validate(record)
        record = removal()
        record["native_apply"].update(session_was_present=True, server_revocation_confirmed=True)
        validate(record)
        for field, bad in (("session_was_present", True), ("server_revocation_confirmed", True),
                           ("removed_files", 1)):
            record = removal(); record["native_verify"][field] = bad
            with self.subTest(field=field), self.assertRaises(pilot.MacPilotEvidenceError):
                validate(record)

    def test_private_extra_fields_counts_and_retention_changes_fail_closed(self):
        for name in (None, "native_apply", "native_verify"):
            record = removal(); target = record if name is None else record[name]
            target["session_token"] = "synthetic-private-value"
            with self.assertRaises(pilot.MacPilotEvidenceError): validate(record)
        for count in (True, -1, 4097, 2.0, "2"):
            record = removal(); record["native_apply"]["removed_files"] = count
            with self.subTest(count=count), self.assertRaises(pilot.MacPilotEvidenceError): validate(record)
        record = removal(); record["native_verify"]["retained_files"] = 3
        with self.assertRaisesRegex(pilot.MacPilotEvidenceError, "retention_changed"): validate(record)
        record = removal(); record["native_apply"]["status"] = "complete"
        with self.assertRaisesRegex(pilot.MacPilotEvidenceError, "status_invalid"): validate(record)
        record = removal(); record["schema_version"] = True
        with self.assertRaises(pilot.MacPilotEvidenceError): validate(record)

    def test_source_candidate_run_and_timeline_must_match(self):
        for field, bad in (("source_commit", "b" * 40), ("artifact_sha256", "b" * 64),
                           ("version", "99.0.0"), ("build", "99"),
                           ("workflow_run_url", RUN[:-1] + "6"),
                           ("completed_at", "2026-09-01T10:01:59Z"),
                           ("completed_at", "2026-09-01T12:00:00Z")):
            record = removal(); record[field] = bad
            with self.subTest(field=field), self.assertRaises(pilot.MacPilotEvidenceError): validate(record)
        with self.assertRaises(pilot.MacPilotEvidenceError): validate(removal(), source_commit=None)

    def test_authentication_uses_separate_single_member_artifact_of_same_run(self):
        record = removal(); run = _run(5, 3, SOURCE, pilot.MACOS_PILOT_OPERATIONS_WORKFLOW)
        created = datetime(2026, 9, 1, 10, 5, tzinfo=timezone.utc)
        candidate_created = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
        candidate = {**ARTIFACT, "source_commit": SOURCE}
        with patch.object(pilot, "_authenticate_github_run", return_value=run) as authenticate, \
                patch.object(pilot, "_authenticate_run_json_artifact", return_value=(record, created)) as artifact:
            pilot._authenticate_native_removal(record, candidate, RUN, NOW, candidate_created)
        authenticate.assert_called_once_with(RUN, SOURCE, pilot.MACOS_PILOT_OPERATIONS_WORKFLOW, "native_removal")
        artifact.assert_called_once_with(run, SOURCE, "hormuz-macos-native-removal-3-1", "removal.json", "native_removal")

    def test_authenticated_artifact_cannot_substitute_types_or_later_creation(self):
        record = removal(); run = _run(5, 3, SOURCE, pilot.MACOS_PILOT_OPERATIONS_WORKFLOW)
        candidate = {**ARTIFACT, "source_commit": SOURCE}
        altered = copy.deepcopy(record); altered["native_apply"]["keychain_session_absent"] = 1
        for observed, created, expected in (
            (altered, datetime(2026, 9, 1, 10, 5, tzinfo=timezone.utc), "artifact_mismatch"),
            (record, datetime(2026, 9, 1, 10, 2, tzinfo=timezone.utc), "timeline_invalid"),
        ):
            with patch.object(pilot, "_authenticate_github_run", return_value=run), \
                    patch.object(pilot, "_authenticate_run_json_artifact", return_value=(observed, created)), \
                    self.assertRaisesRegex(pilot.MacPilotEvidenceError, expected):
                pilot._authenticate_native_removal(record, candidate, RUN, NOW,
                    datetime(2026, 9, 1, 10, tzinfo=timezone.utc))

    def test_companion_read_refuses_symlink_hardlink_wrong_owner_and_overrun(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); path = root / "removal.json"
            path.write_text(json.dumps(removal()))
            self.assertEqual(json.loads(pilot._read_bounded_regular(path, 32768, "removal", require_owner=True)), removal())
            link = root / "link.json"; link.symlink_to(path)
            with self.assertRaises(pilot.MacPilotEvidenceError): pilot._read_bounded_regular(link, 32768, "removal", require_owner=True)
            hard = root / "hard.json"; os.link(path, hard)
            with self.assertRaises(pilot.MacPilotEvidenceError): pilot._read_bounded_regular(path, 32768, "removal", require_owner=True)
            hard.unlink()
            with patch.object(pilot.os, "getuid", return_value=os.getuid()+1), self.assertRaises(pilot.MacPilotEvidenceError):
                pilot._read_bounded_regular(path, 32768, "removal", require_owner=True)
            path.write_bytes(b"x" * 32769)
            with self.assertRaises(pilot.MacPilotEvidenceError): pilot._read_bounded_regular(path, 32768, "removal", require_owner=True)

    def test_assembly_keeps_legacy_records_and_writes_only_explicit_companion(self):
        fixture = _fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            records = {"inputs": _inputs(), "arm64": fixture["clean_machine_runs"][0],
                       "lifecycle": fixture["lifecycle"], "codex": fixture["client_auth_recovery"][0],
                       "claude": fixture["client_auth_recovery"][1], "removal": removal()}
            for name, value in records.items(): (root / (name+".json")).write_text(json.dumps(value))
            out = root / "out"; out.mkdir()
            args = ["assemble", "--inputs", str(root/"inputs.json"), "--arm64-record", str(root/"arm64.json"),
                    "--lifecycle", str(root/"lifecycle.json"), "--codex-record", str(root/"codex.json"),
                    "--claude-record", str(root/"claude.json"), "--source-commit", SOURCE,
                    "--workflow-run-url", RUN, "--output", str(out/"operations.json")]
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()): self.assertEqual(operations.main(args), 0)
            legacy = json.loads((out/"operations.json").read_text()); (out/"operations.json").unlink()
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(operations.main(args + ["--removal",str(root/"removal.json"), "--removal-output",str(out/"removal.json")]), 0)
            self.assertEqual(json.loads((out/"operations.json").read_text()), legacy)
            self.assertEqual(json.loads((out/"removal.json").read_text()), removal())
            self.assertEqual({p.name for p in out.iterdir()}, {"operations.json", "removal.json"})
            self.assertNotIn("native_removal", legacy)

    def test_invalid_or_missing_companion_creates_no_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); args = ["assemble", "--inputs",str(root/"unused.json"),
                "--arm64-record",str(root/"unused.json"), "--lifecycle",str(root/"unused.json"),
                "--codex-record",str(root/"unused.json"), "--claude-record",str(root/"unused.json"),
                "--source-commit",SOURCE,"--workflow-run-url",RUN,"--output",str(root/"operations.json"),
                "--removal",str(root/"removal.json")]
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()): self.assertEqual(operations.main(args),1)
            self.assertEqual(list(root.iterdir()),[])

    def test_synthetic_companion_cannot_qualify_or_change_legacy_claim(self):
        helper = evidence_tests.MacPilotEvidenceTests()
        baseline = helper._validate(*helper._inputs())
        original = pilot.validate_evidence
        def explicit(value, **kwargs):
            return original(value, removal_evidence=removal(), **kwargs)
        with patch.object(pilot, "validate_evidence", side_effect=explicit), \
                patch.object(pilot, "_authenticate_native_removal") as authenticate:
            result = helper._validate(*helper._inputs())
        authenticate.assert_not_called()
        self.assertFalse(result.pop("native_removal_qualified"))
        self.assertEqual(result, baseline)
        self.assertFalse(result["ready_for_controlled_external_pilot"])


class NativeRemovalCollectorOwnershipTests(unittest.TestCase):
    def run_mocked_stop(self, scenario):
        source = (ROOT/"tools/collect_macos_session_and_clients.sh").read_text()
        names = ("capture_owned_app", "owned_app_matches_snapshot", "stop_owned_app")
        functions = "\n".join(re.search(r"^"+name+r"\(\) \{.*?^\}\n", source, re.M|re.S).group() for name in names)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root/"alive").write_text("yes")
            scripts = {
                "pgrep": '[[ "$SCENARIO" != observation_failure ]] || exit 2\n[[ -f "$MOCK_ROOT/alive" ]] || exit 1\nprintf "37\\n"\n[[ "$SCENARIO" != ambiguous ]] || printf "38\\n"',
                "id": 'printf "501\\n"',
                "ps": 'case "$*" in *uid=*) printf "501\\n";; *lstart=*) if [[ "$SCENARIO" == changed && -f "$MOCK_ROOT/birth-seen" ]]; then printf "changed\\n"; else printf "fixed-birth\\n"; touch "$MOCK_ROOT/birth-seen"; fi;; *command=*) printf "/Applications/Hormuz.app/Contents/MacOS/Hormuz\\n";; esac',
                "kill": 'if [[ "$1" == -0 ]]; then [[ -f "$MOCK_ROOT/alive" ]]; else printf "%s %s\\n" "$1" "$2" >> "$MOCK_ROOT/signals"; [[ "$SCENARIO" == timeout ]] || rm "$MOCK_ROOT/alive"; fi',
                "sleep": ':',
            }
            for name, body in scripts.items():
                path = root/name; path.write_text("#!/bin/bash\nset -eu\n"+body+"\n"); path.chmod(0o700)
            for original, name in (("/usr/bin/pgrep","pgrep"),("/bin/ps","ps"),("/usr/bin/id","id"),
                                   ("/bin/kill","kill"),("/bin/sleep","sleep")):
                functions = functions.replace(original, '"'+str(root/name)+'"')
            program = 'set -euo pipefail\nfail() { printf "%s\\n" "$1" >&2; exit 1; }\n'+functions+ \
                '\nverify_installed_bundle() { :; }\nrunning_app_matches() { [[ "$SCENARIO" != unowned ]]; }\nOWNED_APP_PID=""\nstop_owned_app\n'
            result = subprocess.run(["/bin/bash","-c",program], env={"PATH":"/usr/bin:/bin",
                "MOCK_ROOT":str(root),"SCENARIO":scenario}, capture_output=True,text=True,timeout=5)
            return result, (root/"signals").read_text() if (root/"signals").exists() else ""

    def test_exact_owned_process_stops_once_without_name_based_signal(self):
        result, signals = self.run_mocked_stop("owned")
        self.assertEqual(result.returncode,0,msg=result.stderr)
        self.assertEqual(signals,"-TERM 37\n")

    def test_unowned_ambiguous_changed_or_unobservable_process_is_never_signalled(self):
        for scenario, reason in (("unowned","app_process_unowned"),("ambiguous","app_process_ambiguous"),
                                 ("changed","app_process_identity_changed"),("observation_failure","app_process_observation_failed")):
            result, signals = self.run_mocked_stop(scenario)
            with self.subTest(scenario=scenario):
                self.assertNotEqual(result.returncode,0); self.assertIn(reason,result.stderr); self.assertEqual(signals,"")

    def test_owned_gui_shutdown_timeout_fails_without_force_killing(self):
        result, signals = self.run_mocked_stop("timeout")
        self.assertNotEqual(result.returncode,0); self.assertIn("app_process_not_stopped",result.stderr)
        self.assertEqual(signals,"-TERM 37\n")


class NativeRemovalQuarantineTests(unittest.TestCase):
    def run_quarantine(self, scenario):
        source=(ROOT/"tools/collect_macos_session_and_clients.sh").read_text()
        names=("quarantines_safe_to_remove","cleanup","quarantine_installed_bundle")
        functions="\n".join(re.search(r"^"+name+r"\(\) \{.*?^\}\n",source,re.M|re.S).group() for name in names)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); installed=root/"installed.app"; installed.mkdir()
            work=root/"owned-work";work.mkdir();reference=root/"reference.app";reference.mkdir()
            (installed/"original").write_text("verified bytes");(reference/"original").write_text("verified bytes")
            stat_mock=root/"stat";stat_mock.write_text('''#!/bin/bash
set -eu
if [[ "$2" == %d ]]; then printf '1\\n';
elif [[ "$SCENARIO" == identity_changed && "$3" == *replaced-* ]]; then printf '1:11\\n';
else printf '1:10\\n'; fi
''');stat_mock.chmod(0o700)
            mv_mock=root/"mv";mv_mock.write_text('''#!/bin/bash
set -eu
/bin/mv "$1" "$2"
if [[ "$SCENARIO" == content_changed ]]; then printf 'unknown bytes' > "$2/changed"; fi
''');mv_mock.chmod(0o700)
            functions=functions.replace("/Applications/Hormuz.app",str(installed))
            functions=functions.replace("/usr/bin/stat",'"'+str(stat_mock)+'"').replace("/bin/mv",'"'+str(mv_mock)+'"')
            program='''set -euo pipefail
fail() { printf '%s\\n' "$1" >&2; exit 1; }
QUARANTINE_RECOVERY_REQUIRED=false
QUARANTINE_PATHS=("" "" "" "" "" "" "" "")
QUARANTINE_REFERENCES=("" "" "" "" "" "" "" "")
QUARANTINE_IDENTITIES=("" "" "" "" "" "" "" "")
BUNDLE_SEQUENCE=0
'''+functions+'''
verify_installed_bundle() { INSTALLED_REFERENCE="$REFERENCE"; }
trap cleanup EXIT
quarantine_installed_bundle
if [[ "$SCENARIO" == changed_before_cleanup ]]; then printf 'unknown bytes' > "$WORK_ROOT/replaced-1.app/changed"; fi
'''
            result=subprocess.run(["/bin/bash","-c",program],env={"PATH":"/usr/bin:/bin",
                "SCENARIO":scenario,"WORK_ROOT":str(work),"REFERENCE":str(reference)},
                capture_output=True,text=True,timeout=5)
            retained=work.exists(); moved=work/"replaced-1.app"
            contents={p.name:p.read_text() for p in moved.iterdir()} if moved.is_dir() else {}
            private_recovery=(work/"RECOVERY_REQUIRED_PRIVATE.txt").exists()
            self.assertNotIn(str(work),result.stderr)
            return result,retained,contents,private_recovery

    def test_post_move_content_or_identity_mismatch_is_preserved_for_private_recovery(self):
        for scenario in ("content_changed","identity_changed","changed_before_cleanup"):
            result,retained,contents,private_recovery=self.run_quarantine(scenario)
            with self.subTest(scenario=scenario):
                self.assertNotEqual(result.returncode,0)
                self.assertTrue(retained);self.assertTrue(private_recovery)
                self.assertEqual(contents["original"],"verified bytes")
                self.assertIn("quarantined_bundle_requires_recovery",result.stderr)

    def test_unchanged_verified_quarantine_is_removed_only_from_owned_work_root(self):
        result,retained,contents,private_recovery=self.run_quarantine("owned")
        self.assertEqual(result.returncode,0,msg=result.stderr)
        self.assertFalse(retained);self.assertEqual(contents,{})
        self.assertFalse(private_recovery)
