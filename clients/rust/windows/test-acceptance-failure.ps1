# Run with Windows PowerShell: verify a rejected target cannot leave a process or success evidence.
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$temporary = Join-Path ([IO.Path]::GetTempPath()) ("hormuz-acceptance-failure-" + [guid]::NewGuid())
[IO.Directory]::CreateDirectory($temporary) | Out-Null
$probe = Join-Path $temporary "acceptance-failure-probe.exe"
$pidPath = Join-Path $temporary "probe.pid"
$outputPath = Join-Path $temporary "must-not-exist.json"
$previousPidPath = $env:HORMUZ_ACCEPTANCE_FAILURE_PID
try {
    Add-Type -TypeDefinition @'
using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
public static class AcceptanceFailureProbe {
    public static void Main() {
        File.WriteAllText(Environment.GetEnvironmentVariable("HORMUZ_ACCEPTANCE_FAILURE_PID"),
            Process.GetCurrentProcess().Id.ToString());
        Thread.Sleep(60000);
    }
}
'@ -OutputAssembly $probe -OutputType WindowsApplication
    $env:HORMUZ_ACCEPTANCE_FAILURE_PID = $pidPath
    $rejected = $false
    try {
        & (Join-Path $PSScriptRoot "verify-acceptance.ps1") -Executable $probe -Output $outputPath `
            -WarmupSeconds 1 -SampleCount 2 -Repetitions 1 -Cycles 1
    } catch {
        if ($_.Exception.Message -ne "External UI Automation acceptance failed.") { throw }
        $rejected = $true
    }
    if (-not $rejected) { throw "A target without the preview UI was accepted." }
    if (Test-Path -LiteralPath $outputPath) { throw "Rejected target produced success evidence." }
    if (-not (Test-Path -LiteralPath $pidPath)) { throw "The failure probe never started." }
    $probeId = [int]([IO.File]::ReadAllText($pidPath))
    $remaining = Get-Process -Id $probeId -ErrorAction SilentlyContinue
    if ($null -ne $remaining) {
        try {
            if ($remaining.Path -eq $probe) { throw "Rejected target was left running." }
        } finally { $remaining.Dispose() }
    }
    # Refusal to overwrite evidence happens before process creation.
    [IO.File]::WriteAllText($outputPath, "preserve-existing-evidence")
    [IO.File]::Delete($pidPath)
    $refusedOverwrite = $false
    try {
        & (Join-Path $PSScriptRoot "verify-acceptance.ps1") -Executable $probe -Output $outputPath
    } catch {
        if ($_.Exception.Message -ne "Refusing to replace existing acceptance evidence.") { throw }
        $refusedOverwrite = $true
    }
    if (-not $refusedOverwrite -or (Test-Path -LiteralPath $pidPath) -or
        [IO.File]::ReadAllText($outputPath) -ne "preserve-existing-evidence") {
        throw "Existing evidence was not preserved before launch."
    }
    Write-Output "windows_acceptance_failure_cleanup=passed evidence_overwrite_refused=true"
} finally {
    if (Test-Path -LiteralPath $pidPath) {
        $remaining = Get-Process -Id ([int]([IO.File]::ReadAllText($pidPath))) -ErrorAction SilentlyContinue
        if ($null -ne $remaining) {
            try {
                if ($remaining.Path -eq $probe) {
                    $remaining.Kill()
                    if (-not $remaining.WaitForExit(5000)) { throw "Failure probe cleanup timed out." }
                }
            } finally { $remaining.Dispose() }
        }
    }
    $env:HORMUZ_ACCEPTANCE_FAILURE_PID = $previousPidPath
    Remove-Item -LiteralPath $temporary -Recurse -Force
}
