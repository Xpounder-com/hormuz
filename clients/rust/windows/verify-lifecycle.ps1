param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$Output
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$previewExecutable = (Resolve-Path -LiteralPath $Executable).Path
$outputPath = [IO.Path]::GetFullPath($Output)
if (Test-Path -LiteralPath $outputPath) { throw "Refusing to overwrite lifecycle evidence." }
$sourceCommit = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $sourceCommit -notmatch '^[0-9a-f]{40}$') { throw "Source identity is unavailable." }
$proposedHead = $env:HORMUZ_PR_HEAD
if (-not $proposedHead) { $proposedHead = $sourceCommit }
if ($proposedHead -notmatch '^[0-9a-f]{40}$') { throw "Invalid proposed revision." }
$artifactHash = (Get-FileHash -LiteralPath $previewExecutable -Algorithm SHA256).Hash.ToLowerInvariant()
Add-Type -TypeDefinition @'
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
public static class LifecycleWindows {
    private delegate bool Enumerate(IntPtr window, IntPtr data);
    [DllImport("user32.dll")] private static extern bool EnumWindows(Enumerate callback, IntPtr data);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window, out uint pid);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetClassName(IntPtr window, StringBuilder text, int count);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr window);
    [DllImport("user32.dll")] private static extern IntPtr SendMessageTimeout(IntPtr window, uint message, UIntPtr wp, IntPtr lp, uint flags, uint timeout, out UIntPtr result);
    public static IntPtr Window(int pid) {
        IntPtr result = IntPtr.Zero;
        EnumWindows(delegate(IntPtr window, IntPtr unused) {
            uint owner; GetWindowThreadProcessId(window, out owner);
            if (owner != pid) return true;
            var name = new StringBuilder(256);
            GetClassName(window, name, name.Capacity);
            if (name.ToString() == "HormuzNativeCompanionPreview") { result = window; return false; }
            return true;
        }, IntPtr.Zero);
        return result;
    }
    public static IntPtr WaitForWindow(Process process) {
        var watch = Stopwatch.StartNew();
        while (watch.ElapsedMilliseconds < 10000) {
            if (process.HasExited) throw new Exception("Owned primary exited before window creation.");
            var window = Window(process.Id);
            if (window != IntPtr.Zero && IsWindowVisible(window)) return window;
            Thread.Sleep(20);
        }
        throw new Exception("Owned primary window did not become visible.");
    }
    public static void Send(IntPtr window, uint message, uint command) {
        UIntPtr result;
        if (SendMessageTimeout(window, message, new UIntPtr(command), IntPtr.Zero, 2, 2000, out result) == IntPtr.Zero)
            throw new Exception("Owned preview did not process a native command.");
    }
}
'@
$temporary = Join-Path ([IO.Path]::GetTempPath()) ("hormuz-instance-" + [guid]::NewGuid())
[IO.Directory]::CreateDirectory($temporary) | Out-Null
$owned = [Collections.Generic.List[Diagnostics.Process]]::new()
$passed = $false
function Start-OwnedPreview([string]$StateDirectory) {
    $process = Start-Process -FilePath $previewExecutable -PassThru -ArgumentList @(
        "--preview", "--state-directory", ('"' + $StateDirectory + '"'))
    $owned.Add($process)
    return $process
}
try {
    $stateDirectory = Join-Path $temporary "private"
    $primary = Start-OwnedPreview $stateDirectory
    $window = [LifecycleWindows]::WaitForWindow($primary)
    [LifecycleWindows]::Send($window, 0x0010, 0) # Close hides; it must not stop the owner.
    if ($primary.HasExited -or ([LifecycleWindows]::IsWindowVisible($window) -and -not [LifecycleWindows]::IsIconic($window))) {
        throw "Close did not retain a recoverable hidden/minimized primary."
    }
    $secondaries = @(1..8 | ForEach-Object { Start-OwnedPreview $stateDirectory })
    foreach ($secondary in $secondaries) {
        if (-not $secondary.WaitForExit(10000) -or $secondary.ExitCode -ne 0) {
            throw "A concurrent launch did not hand off to the owner."
        }
    }
    $reopened = [LifecycleWindows]::WaitForWindow($primary)
    if ($reopened -ne $window -or [LifecycleWindows]::IsIconic($window)) { throw "Reopen replaced the primary or left it minimized." }
    $primary.Kill()
    if (-not $primary.WaitForExit(5000)) { throw "Owned crash probe did not exit." }
    $replacement = Start-OwnedPreview $stateDirectory
    $replacementWindow = [LifecycleWindows]::WaitForWindow($replacement)
    [LifecycleWindows]::Send($replacementWindow, 0x0111, 104) # Explicit app Exit.
    if (-not $replacement.WaitForExit(5000) -or $replacement.ExitCode -ne 0) { throw "Replacement primary did not exit cleanly." }
    $unsafeDirectory = Join-Path $temporary "inherited-permissions"
    [IO.Directory]::CreateDirectory($unsafeDirectory) | Out-Null
    $rejected = Start-OwnedPreview $unsafeDirectory
    if (-not $rejected.WaitForExit(5000) -or $rejected.ExitCode -eq 0) { throw "Unsafe storage was not rejected." }
    if ((Get-FileHash -LiteralPath $previewExecutable -Algorithm SHA256).Hash.ToLowerInvariant() -ne $artifactHash) { throw "Artifact changed during lifecycle verification." }
    $passed = $true
} finally {
    $cleanupFailed = $false
    foreach ($process in $owned) {
        try {
            if (-not $process.HasExited) { $process.Kill(); if (-not $process.WaitForExit(5000)) { $cleanupFailed = $true } }
        } catch { $cleanupFailed = $true } finally { $process.Dispose() }
    }
    if ($cleanupFailed) { throw "An owned lifecycle test process could not be stopped." }
    Remove-Item -LiteralPath $temporary -Recurse -Force
}
if (-not $passed) { throw "Lifecycle verification did not complete." }
@{
    schema_version = 1
    source_commit = $sourceCommit
    proposed_head = $proposedHead
    artifact_sha256 = $artifactHash
    result = "passed"
    concurrent_secondary_launches = 8
    existing_window_reopened = $true
    abrupt_exit_recovery = $true
    explicit_exit_released_owner = $true
    unsafe_storage_rejected = $true
    owned_process_cleanup = "passed"
    input_scope = "native_process_launch_and_synthetic_window_commands"
    physical_keyboard_tray_sleep_acceptance = "pending"
} | ConvertTo-Json | Set-Content -LiteralPath $outputPath -Encoding UTF8
Write-Output "windows_instance_lifecycle=passed concurrent_launches=8 crash_recovery=true"
