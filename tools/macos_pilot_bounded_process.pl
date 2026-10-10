#!/usr/bin/perl
# System-runtime adapter: no CPAN, shell command string, or process-name kill.
use strict;
use warnings;
use POSIX qw(setsid dup2 _exit WNOHANG);
use Fcntl qw(O_WRONLY O_CREAT O_EXCL O_NOFOLLOW F_GETFL F_SETFL O_NONBLOCK F_SETFD FD_CLOEXEC);
use IO::Select;
use Time::HiRes qw(clock_gettime CLOCK_MONOTONIC sleep);
use Errno qw(EINTR EAGAIN EWOULDBLOCK ESRCH EPERM);
use JSON::PP;

umask 0077;
$SIG{CHLD} = 'DEFAULT'; # Never auto-reap the retained child before group signals.
my ($seconds, $limit, $stdout_path, $stderr_path, $separator, @command) = @ARGV;
sub prerequisite_failure { print STDERR "macos_pilot_process_error=invalid_prerequisite\n"; exit 125; }
defined($separator) && $separator eq '--' && @command && $command[0] =~ m{\A/}
    && $seconds =~ /\A[1-9][0-9]{0,2}\z/ && $seconds <= 120
    && $limit =~ /\A[1-9][0-9]{0,5}\z/ && $limit <= 65536
    && $stdout_path ne $stderr_path or prerequisite_failure();
my ($stdout_file, $stderr_file);
sysopen($stdout_file, $stdout_path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600)
    or prerequisite_failure();
sysopen($stderr_file, $stderr_path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600)
    or prerequisite_failure();
binmode($stdout_file); binmode($stderr_file);
pipe(my $out_read, my $out_write) or prerequisite_failure();
pipe(my $err_read, my $err_write) or prerequisite_failure();
pipe(my $ready_read, my $ready_write) or prerequisite_failure();
pipe(my $ack_read, my $ack_write) or prerequisite_failure();
defined(fcntl($ready_write, F_SETFD, FD_CLOEXEC)) or prerequisite_failure();
for my $stream ($ready_read, $out_read, $err_read) {
    my $flags = fcntl($stream, F_GETFL, 0);
    defined($flags) && defined(fcntl($stream, F_SETFL, $flags | O_NONBLOCK))
        or prerequisite_failure();
}
my $started = clock_gettime(CLOCK_MONOTONIC);
my $deadline = $started + $seconds;
my $interrupted = 0;
$SIG{TERM} = $SIG{INT} = $SIG{HUP} = sub { $interrupted = 1; };
$SIG{PIPE} = 'IGNORE'; # A failed acknowledgement becomes a retained failure.
my $pid = fork(); defined($pid) or prerequisite_failure();
if ($pid == 0) {
    $SIG{TERM} = $SIG{INT} = $SIG{HUP} = $SIG{PIPE} = 'DEFAULT';
    close($out_read); close($err_read); close($ready_read);
    close($ack_write);
    close($stdout_file); close($stderr_file);
    my $child_failure = sub { syswrite($ready_write, 'E'); _exit(125); };
    my $session = setsid();
    defined($session) && $session >= 0 or $child_failure->();
    # No exec or descendants before the parent accepts owned-session custody.
    syswrite($ready_write, 'R') == 1 or $child_failure->();
    my $ack = '';
    my $amount;
    do { $amount = sysread($ack_read, $ack, 1); } while (!defined($amount) && $! == EINTR);
    defined($amount) && $amount == 1 && $ack eq 'A' or $child_failure->();
    close($ack_read) or $child_failure->();
    open(STDIN, '<', '/dev/null') or $child_failure->();
    my $out_dup = dup2(fileno($out_write), 1);
    my $err_dup = dup2(fileno($err_write), 2);
    defined($out_dup) && $out_dup >= 0 && defined($err_dup) && $err_dup >= 0 or $child_failure->();
    close($out_write); close($err_write);
    exec { $command[0] } @command or $child_failure->();
}
close($out_write); close($err_write); close($ready_write);
close($ack_read);
my $select = IO::Select->new($ready_read, $out_read, $err_read);
my ($out_fd, $err_fd) = (fileno($out_read), fileno($err_read));
my %files = (fileno($out_read) => $stdout_file, fileno($err_read) => $stderr_file);
my %counts = (fileno($out_read) => 0, fileno($err_read) => 0);
my ($ready, $acknowledged, $ack_closed, $exec_ready, $reaped, $wait_attempted) = (0, 0, 0, 0, 0, 0);
my ($failure, $cleanup_error, $total, $drained, $ready_bytes, $status) = ('', '', 0, 0, '', undef);

sub read_stream {
    my ($stream, $capture) = @_;
    my $data = '';
    my $read_limit = !$capture && fileno($stream) != fileno($ready_read)
        ? (65536 - $drained < 8192 ? 65536 - $drained : 8192) : 8192;
    return if $read_limit <= 0;
    my $amount = sysread($stream, $data, $read_limit);
    if (!defined($amount)) {
        return if $! == EAGAIN || $! == EWOULDBLOCK || $! == EINTR;
        die "capture_read_failed\n";
    }
    if ($amount == 0) {
        $select->remove($stream);
        if (fileno($stream) == fileno($ready_read)) {
            $exec_ready = $ready_bytes eq 'R' && $acknowledged ? 1 : 0;
            $failure ||= 'startup_failed' unless $exec_ready;
        }
        return;
    }
    if (fileno($stream) == fileno($ready_read)) {
        $ready_bytes .= $data;
        $ready = 1 if substr($ready_bytes, 0, 1) eq 'R';
        $failure ||= 'startup_failed' if length($ready_bytes) > 1 || $ready_bytes ne 'R';
        # Custody is accepted before A; asynchronous failure cannot race exec.
        if ($capture && $ready && !$acknowledged && !$failure && !$interrupted) {
            my $written = syswrite($ack_write, 'A');
            if (defined($written) && $written == 1) { $acknowledged = 1; }
            else { $failure ||= 'startup_failed'; }
            $ack_closed = close($ack_write) ? 1 : 0;
            $failure ||= 'startup_failed' unless $ack_closed;
        }
        return;
    }
    if (!$capture) { $drained += $amount; return; }
    my $accepted = $amount < $limit - $total ? $amount : $limit - $total;
    if ($accepted > 0) {
        my $written = syswrite($files{fileno($stream)}, $data, $accepted);
        defined($written) && $written == $accepted or die "capture_write_failed\n";
        $counts{fileno($stream)} += $accepted; $total += $accepted;
    }
    $failure ||= 'output_limit' if $accepted != $amount;
}

sub signal_owned {
    my ($signal) = @_;
    die "signal_after_wait\n" if $wait_attempted;
    my $target = $ready ? -$pid : $pid;
    return if kill($signal, $target);
    my $error = 0 + $!;
    return if $error == ESRCH;
    # Darwin can deny a zombie-only group signal. The leader is still retained.
    if ($ready && $error == EPERM && getpgrp($pid) < 0 && kill(0, $pid)) { return; }
    $cleanup_error ||= 'owned_signal_failed';
}

eval {
    while ($select->count) {
        my $remaining = $deadline - clock_gettime(CLOCK_MONOTONIC);
        if ($remaining <= 0) { $failure ||= 'work_deadline'; last; }
        if ($interrupted) { $failure ||= 'interrupted'; last; }
        if (!$exec_ready && clock_gettime(CLOCK_MONOTONIC) - $started >= 5) {
            $failure ||= 'startup_deadline'; last;
        }
        my @readable = $select->can_read($remaining < .05 ? $remaining : .05);
        for my $stream (@readable) { read_stream($stream, 1); last if $failure; }
        last if $failure;
    }
    1;
} or $failure ||= 'capture_failed';

# All destructive signals precede the first waitpid call. EOF is not process exit.
if (!$ack_closed) {
    $ack_closed = close($ack_write) ? 1 : 0;
    $cleanup_error ||= 'capture_close_failed' unless $ack_closed;
}
eval { signal_owned('TERM'); 1 } or $cleanup_error ||= 'owned_signal_failed';
my $grace_deadline = clock_gettime(CLOCK_MONOTONIC) + 2;
eval {
    while (clock_gettime(CLOCK_MONOTONIC) < $grace_deadline) {
        if ($select->count && $drained < 65536) {
            for my $stream ($select->can_read(.05)) { read_stream($stream, 0); }
        } else { sleep(.05); }
    }
    1;
} or $cleanup_error ||= 'cleanup_drain_failed';
eval { signal_owned('KILL'); 1 } or $cleanup_error ||= 'owned_signal_failed';
$wait_attempted = 1;
my $reap_deadline = clock_gettime(CLOCK_MONOTONIC) + 3;
while (clock_gettime(CLOCK_MONOTONIC) < $reap_deadline) {
    my $result = waitpid($pid, WNOHANG);
    if ($result == $pid) { $status = $?; $reaped = 1; last; }
    if ($result < 0 && $! != EINTR) { $cleanup_error ||= 'owned_reap_failed'; last; }
    sleep(.05);
}
$cleanup_error ||= 'owned_reap_timeout' unless $reaped;
my $group_absent = 0;
if ($ready && $reaped) {
    # Only observation after reap; descendants may need a moment to be reaped.
    while (clock_gettime(CLOCK_MONOTONIC) < $reap_deadline) {
        if (!kill(0, -$pid) && $! == ESRCH) { $group_absent = 1; last; }
        sleep(.05);
    }
    $cleanup_error ||= 'owned_group_not_absent' unless $group_absent;
}
my $pipes_closed = $ack_closed;
for my $stream ($ready_read, $out_read, $err_read, $stdout_file, $stderr_file) {
    $pipes_closed = 0 unless close($stream);
}
$cleanup_error ||= 'capture_close_failed' unless $pipes_closed;
my $code = !defined($status) ? 126 : ($status & 127) ? 128 + ($status & 127) : $status >> 8;
$failure ||= 'interrupted' if $interrupted;
my $supervisor_passed = !$failure && !$cleanup_error && $exec_ready && $reaped && $group_absent;
my $receipt = {
    schema_id => 'hormuz.macos-pilot-bounded-process', schema_version => 1,
    capture_failure => $failure || undef, cleanup_error => $cleanup_error || undef,
    group_custody_observed => $ready ? JSON::PP::true : JSON::PP::false,
    parent_acknowledged => $acknowledged ? JSON::PP::true : JSON::PP::false,
    exec_readiness_observed => $exec_ready ? JSON::PP::true : JSON::PP::false,
    process_reaped => $reaped ? JSON::PP::true : JSON::PP::false,
    owned_group_absent => $group_absent ? JSON::PP::true : JSON::PP::false,
    captured_pipes_closed => $pipes_closed ? JSON::PP::true : JSON::PP::false,
    signal_after_reap => JSON::PP::false, retry_count => 0,
    child_exit_code => $code, stdout_bytes => $counts{$out_fd},
    stderr_bytes => $counts{$err_fd}, accepted_output_bytes => $total,
    work_deadline_seconds => 0 + $seconds, output_limit_bytes => 0 + $limit,
    cleanup_grace_seconds => 2, cleanup_reap_seconds => 3,
    cleanup_drained_bytes => $drained,
};
my $receipt_file;
if (!sysopen($receipt_file, "$stdout_path.process.json", O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600)) {
    print STDERR "macos_pilot_process_error=receipt_failed\n"; exit 126;
}
print {$receipt_file} JSON::PP->new->canonical->encode($receipt), "\n";
close($receipt_file) or $supervisor_passed = 0;
if (!$supervisor_passed) { print STDERR "macos_pilot_process_error=bounded_execution_failed\n"; exit 124; }
exit($code > 255 ? 125 : $code);
