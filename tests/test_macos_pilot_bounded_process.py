"""Finite synthetic local children only; no app, provider, custody or collector."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ADAPTER = Path(__file__).resolve().parents[1]/"tools/macos_pilot_bounded_process.pl"


class MacPilotBoundedProcessTests(unittest.TestCase):
    def run_adapter(self, command, *, seconds=3, limit=4096, adapter=ADAPTER):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); out=root/"stdout"; err=root/"stderr"
            result=subprocess.run(["/usr/bin/perl",str(adapter),str(seconds),str(limit),str(out),str(err),"--",*command],
                env={"PATH":"/usr/bin:/bin","HOME":str(root),"TMPDIR":str(root)},
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=12,check=False)
            receipt=json.loads((root/"stdout.process.json").read_text())
            self.assertEqual(result.stdout,b"")
            self.assertEqual(receipt["retry_count"],0)
            self.assertLessEqual(receipt["accepted_output_bytes"],limit)
            self.assertLessEqual(receipt["cleanup_drained_bytes"],65536)
            self.assertTrue(receipt["process_reaped"])
            self.assertTrue(receipt["owned_group_absent"])
            self.assertTrue(receipt["captured_pipes_closed"])
            self.assertFalse(receipt["signal_after_reap"])
            self.assertIsNone(receipt["cleanup_error"])
            return result,receipt,out.read_bytes(),err.read_bytes()

    def test_successful_exec_is_observed_and_captures_are_exact_private_files(self):
        result,receipt,out,err=self.run_adapter(["/usr/bin/perl","-e",'print STDOUT "ok"; print STDERR "detail";'])
        self.assertEqual(result.returncode,0)
        self.assertEqual((out,err),(b"ok",b"detail"))
        self.assertEqual((receipt["stdout_bytes"],receipt["stderr_bytes"]),(2,6))
        self.assertTrue(receipt["exec_readiness_observed"])
        self.assertTrue(receipt["group_custody_observed"])
        self.assertTrue(receipt["parent_acknowledged"])
        self.assertIsNone(receipt["capture_failure"])

    def test_pre_ack_interrupt_cannot_exec_or_spawn_an_unowned_worker(self):
        # Deliver a real signal at the one deterministic pre-ack boundary in a
        # private source copy. The production adapter has no test/environment hook.
        boundary="        # Custody is accepted before A; asynchronous failure cannot race exec.\n"
        source=ADAPTER.read_text()
        self.assertEqual(source.count(boundary),1)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); adapter=root/"adapter.pl"; marker=root/"executed"
            adapter.write_text(source.replace(boundary,boundary+"        kill('TERM', $$) if $capture && $ready && !$acknowledged;\n"))
            adapter.chmod(0o600)
            program='open(my $f, ">", $ARGV[0]) or die; print {$f} "executed"; close($f); my $p=fork(); die unless defined $p; sleep 30;'
            result,receipt,out,err=self.run_adapter(["/usr/bin/perl","-e",program,str(marker)],adapter=adapter)
            self.assertEqual(result.returncode,124)
            self.assertEqual(receipt["capture_failure"],"interrupted")
            self.assertTrue(receipt["group_custody_observed"])
            self.assertFalse(receipt["parent_acknowledged"])
            self.assertFalse(receipt["exec_readiness_observed"])
            self.assertFalse(marker.exists())
            self.assertEqual((out,err),(b"",b""))

    def test_nonzero_child_exit_is_preserved_after_full_group_cleanup(self):
        result,receipt,_,_=self.run_adapter(["/usr/bin/perl","-e","exit 7;"])
        self.assertEqual((result.returncode,receipt["child_exit_code"]),(7,7))
        self.assertIsNone(receipt["capture_failure"])

    def test_output_overrun_is_bounded_and_cannot_pass(self):
        result,receipt,out,_=self.run_adapter(["/usr/bin/perl","-e",'print "x" x 1024;'],limit=128)
        self.assertEqual(result.returncode,124)
        self.assertEqual(receipt["capture_failure"],"output_limit")
        self.assertEqual(len(out),128)

    def test_exec_failure_never_satisfies_readiness(self):
        result,receipt,_,_=self.run_adapter(["/hormuz-nonexistent-synthetic-executable"])
        self.assertEqual(result.returncode,124)
        self.assertEqual(receipt["capture_failure"],"startup_failed")
        self.assertFalse(receipt["exec_readiness_observed"])

    def test_deadline_closes_owned_forked_worker_with_no_name_or_parent_sweep(self):
        program='''use POSIX (); my $worker=fork(); die unless defined $worker;
if (!$worker) { $SIG{TERM}=sub { POSIX::_exit(0); }; sleep 30; POSIX::_exit(0); }
$SIG{TERM}=sub { waitpid($worker,0); POSIX::_exit(0); }; print "ready"; sleep 30;'''
        result,receipt,_,_=self.run_adapter(["/usr/bin/perl","-e",program],seconds=2)
        self.assertEqual(result.returncode,124)
        self.assertEqual(receipt["capture_failure"],"work_deadline")
