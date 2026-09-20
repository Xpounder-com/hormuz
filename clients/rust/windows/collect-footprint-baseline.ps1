param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$Output,
    [string]$BuildManifest,
    [ValidateSet("visible", "folded", "hidden")][string]$Scenario = "visible",
    [ValidateRange(0, 120)][int]$WarmupSeconds = 60,
    [ValidateRange(5, 600)][int]$DurationSeconds = 300,
    [ValidateRange(1000, 30000)][int]$IntervalMilliseconds = 5000,
    [ValidateRange(1, 3)][int]$Repetitions = 3,
    [switch]$Worker
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($Worker) {
    try {
        # Windows PowerShell 5.1 supplies the UI Automation and WMI assemblies.
        $settings = $env:HORMUZ_FOOTPRINT_CONFIG | ConvertFrom-Json
        Add-Type -Path (Join-Path $PSScriptRoot "FootprintBaseline.cs") -ReferencedAssemblies `
            UIAutomationClient, UIAutomationTypes, WindowsBase, System.Management
        $result = [FootprintBaseline]::Run(
            $settings.process_id, $settings.start_ticks, $settings.launch_ticks,
            $settings.scenario, $settings.repetition, $settings.warmup,
            $settings.duration, $settings.interval)
        $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $settings.result_path -Encoding UTF8
        exit 0
    } catch {
        Write-Output ("windows_footprint_worker=failed " + $_.Exception.GetBaseException().Message)
        exit 1
    }
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw "Synthetic preview footprint collection requires Windows."
}
if (($DurationSeconds * 1000) % $IntervalMilliseconds -ne 0) {
    throw "Duration must be a whole number of sample intervals."
}
$previewExecutable = (Resolve-Path -LiteralPath $Executable).Path
$outputPath = [IO.Path]::GetFullPath($Output)
if (Test-Path -LiteralPath $outputPath) { throw "Refusing to replace existing footprint evidence." }
$outputDirectory = [IO.Path]::GetDirectoryName($outputPath)
if (-not (Test-Path -LiteralPath $outputDirectory -PathType Container)) {
    throw "The evidence output directory must already exist."
}
$sourceCommit = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $sourceCommit -notmatch '^[0-9a-f]{40}$') {
    throw "Source revision is unavailable."
}
$proposedHead = $env:HORMUZ_PR_HEAD
if (-not $proposedHead) { $proposedHead = $sourceCommit }
if ($proposedHead -notmatch '^[0-9a-f]{40}$') {
    throw "Proposed revision must be a full commit SHA."
}
$artifactHash = (Get-FileHash -LiteralPath $previewExecutable -Algorithm SHA256).Hash.ToLowerInvariant()
$artifactBytes = (Get-Item -LiteralPath $previewExecutable).Length
$buildManifestHash = $null
if ($BuildManifest) {
    $manifestPath = (Resolve-Path -LiteralPath $BuildManifest).Path
    $buildManifestHash = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $build = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($build.schema_id -ne "hormuz.windows.synthetic-footprint-build" -or
        $build.source_commit -ne $sourceCommit -or $build.proposed_head -ne $proposedHead -or
        $build.executable_sha256 -ne $artifactHash -or $build.executable_bytes -ne $artifactBytes -or
        $build.repeat_build -ne "passed_same_runner_same_checkout") {
        throw "The executable does not match its exact-source build manifest."
    }
    $compiler = $build.compiler
} else {
    $compiler = (rustc --version).Trim()
}
$runs = New-Object System.Collections.Generic.List[object]
$previousSettings = $env:HORMUZ_FOOTPRINT_CONFIG
try {
    for ($repetition = 1; $repetition -le $Repetitions; $repetition++) {
        $temporary = Join-Path ([IO.Path]::GetTempPath()) ("hormuz-windows-footprint-" + [guid]::NewGuid())
        [IO.Directory]::CreateDirectory($temporary) | Out-Null
        $preview = $null
        $workerProcess = $null
        try {
            $stateDirectory = Join-Path $temporary "private"
            $launchTicks = [DateTime]::UtcNow.Ticks
            $preview = Start-Process -FilePath $previewExecutable `
                -ArgumentList @("--preview", "--state-directory", ('"' + $stateDirectory + '"')) -PassThru
            $resultPath = Join-Path $temporary "result.json"
            $env:HORMUZ_FOOTPRINT_CONFIG = @{
                process_id = $preview.Id
                start_ticks = $preview.StartTime.ToUniversalTime().Ticks
                launch_ticks = $launchTicks
                result_path = $resultPath
                scenario = $Scenario
                repetition = $repetition
                warmup = $WarmupSeconds
                duration = $DurationSeconds
                interval = $IntervalMilliseconds
            } | ConvertTo-Json -Compress
            $stdout = Join-Path $temporary "worker-stdout.txt"
            $stderr = Join-Path $temporary "worker-stderr.txt"
            $powershell = Join-Path $env:SystemRoot "System32/WindowsPowerShell/v1.0/powershell.exe"
            $workerProcess = Start-Process -FilePath $powershell -PassThru `
                -ArgumentList @("-NoProfile", "-NonInteractive", "-MTA", "-File", ('"' + $PSCommandPath + '"'),
                    "-Worker", "-Executable", "unused", "-Output", "unused") `
                -RedirectStandardOutput $stdout -RedirectStandardError $stderr
            $timeoutSeconds = 90 + $WarmupSeconds + $DurationSeconds
            if (-not $workerProcess.WaitForExit([int]($timeoutSeconds * 1000))) {
                throw "Bounded footprint observer timed out."
            }
            if ($workerProcess.ExitCode -ne 0) {
                Write-Output ([IO.File]::ReadAllText($stdout).Trim())
                throw "Footprint observer rejected the owned preview."
            }
            if ([IO.File]::ReadAllText($stderr).Length -ne 0) {
                throw "Footprint observer emitted a diagnostic."
            }
            if (-not $preview.WaitForExit(5000) -or $preview.ExitCode -ne 0) {
                throw "Owned preview did not exit cleanly."
            }
            if ((Get-FileHash -LiteralPath $previewExecutable -Algorithm SHA256).Hash.ToLowerInvariant() -ne $artifactHash) {
                throw "Preview executable changed during collection."
            }
            $run = Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json
            $expectedSamples = [int]($DurationSeconds * 1000 / $IntervalMilliseconds) + 1
            if ($run.result -ne "passed" -or $run.scenario -ne $Scenario -or
                $run.repetition -ne $repetition -or $run.samples.Count -ne $expectedSamples -or
                $run.sample_duration_seconds -lt ($DurationSeconds - 1) -or
                @($run.samples | Where-Object { $_.process_count -ne 1 }).Count -ne 0) {
                throw "Incomplete or inconsistent footprint run."
            }
            $runs.Add($run)
            Write-Output "windows_footprint_run=passed scenario=$Scenario repetition=$repetition samples=$expectedSamples"
        } finally {
            $cleanupFailed = $false
            foreach ($ownedProcess in @($workerProcess, $preview)) {
                if ($null -ne $ownedProcess) {
                    try {
                        if (-not $ownedProcess.HasExited) {
                            $ownedProcess.Kill()
                            if (-not $ownedProcess.WaitForExit(5000)) { $cleanupFailed = $true }
                        }
                    } catch {
                        $cleanupFailed = $true
                    } finally {
                        $ownedProcess.Dispose()
                    }
                }
            }
            Remove-Item -LiteralPath $temporary -Recurse -Force
            if ($cleanupFailed) { throw "An owned measurement process could not be stopped." }
        }
    }
} finally {
    $env:HORMUZ_FOOTPRINT_CONFIG = $previousSettings
}

$completeProtocol = $Repetitions -eq 3 -and $WarmupSeconds -eq 60 -and
    $DurationSeconds -eq 300 -and $IntervalMilliseconds -eq 5000
$metadata = [ordered]@{
    schema_id = "hormuz.windows.synthetic-footprint-baseline"
    schema_version = 1
    result = "passed"
    complete_three_run_protocol = $completeProtocol
    artifact_kind = "unsigned_connected_development_candidate"
    execution_mode = "synthetic_preview_no_sign_in_or_relay"
    source_commit = $sourceCommit
    proposed_head = $proposedHead
    executable_sha256 = $artifactHash
    executable_bytes = $artifactBytes
    build_manifest_sha256 = $buildManifestHash
    target = "x86_64-pc-windows-msvc"
    compiler = $compiler
    host = [ordered]@{
        os = [Environment]::OSVersion.VersionString
        architecture = $env:PROCESSOR_ARCHITECTURE
        logical_processors = [Environment]::ProcessorCount
        runner_image_os = $env:ImageOS
        runner_image_version = $env:ImageVersion
        power_thermal_and_display = "uncontrolled"
    }
    scenario = $Scenario
    run_count = $runs.Count
    sample_count_per_run = [int]($DurationSeconds * 1000 / $IntervalMilliseconds) + 1
    measurement_tools = "UIAutomationClient, System.Diagnostics.Process, numeric Win32_Process PID/parent topology"
    runs = @($runs.ToArray())
    limitations = @(
        "Three independent synthetic preview launches on one hosted Windows runner for this scenario; other scenarios use separate runners.",
        "Sampling can miss a short-lived helper between observations; observed helpers fail the empty-preview run.",
        "Working set and private bytes are distinct process counters, not total physical footprint.",
        "Window-ready timing is an upper bound that includes PowerShell observer startup and polling; it is not cold start, rendered pixels or interaction readiness.",
        "No verified per-process wake-up counter was available; wake-ups are null, not zero.",
        "Observer CPU covers worker sampling only; its peak working set includes worker setup and sampling. Both exclude the parent watchdog, WMI service and artifact upload.",
        "Host power, thermal, display and background activity were not controlled or compared between scenario runners.",
        "No physical keyboard/tray, screen reader, GPU, real sign-in, active client, relay or numerical regression budget is qualified."
    )
    manual_platform_acceptance = "pending"
}
$staged = $outputPath + ".tmp-" + [guid]::NewGuid()
try {
    $metadata | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $staged -Encoding UTF8
    [IO.File]::Move($staged, $outputPath)
} finally {
    if (Test-Path -LiteralPath $staged) { Remove-Item -LiteralPath $staged -Force }
}
Write-Output "windows_footprint_baseline=passed scenario=$Scenario runs=$($runs.Count) complete_protocol=$completeProtocol manual_acceptance=pending"
