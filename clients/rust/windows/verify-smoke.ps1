param([Parameter(Mandatory = $true)][string]$Executable)
$ErrorActionPreference = "Stop"
$previewExecutable = (Resolve-Path $Executable).Path
$smokeDirectory = Join-Path ([IO.Path]::GetTempPath()) ("hormuz-windows-smoke-" + [guid]::NewGuid())
[IO.Directory]::CreateDirectory($smokeDirectory) | Out-Null
$process = $null
try {
    $stdoutPath = Join-Path $smokeDirectory "stdout.txt"
    $stderrPath = Join-Path $smokeDirectory "stderr.txt"
    $process = Start-Process -FilePath $previewExecutable -ArgumentList "--smoke-test" -PassThru `
        -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
    if (-not $process.WaitForExit(15000)) {
        $process.Kill()
        $process.WaitForExit()
        throw "Native preview did not complete its bounded smoke check."
    }
    if ($process.ExitCode -ne 0) { throw "Native preview smoke check failed with exit code $($process.ExitCode)." }
    $output = [IO.File]::ReadAllText($stdoutPath).Trim()
    if ($output -notmatch '^windows_shell_smoke=passed tray_registered=(true|false)$') {
        throw "Native preview did not emit the expected completion marker."
    }
    if ([IO.File]::ReadAllText($stderrPath).Length -ne 0) { throw "Native preview emitted an unexpected diagnostic." }
    Write-Output $output
} finally {
    if ($null -ne $process) {
        if (-not $process.HasExited) { $process.Kill(); $process.WaitForExit() }
        $process.Dispose()
    }
    Remove-Item -LiteralPath $smokeDirectory -Recurse -Force
}
