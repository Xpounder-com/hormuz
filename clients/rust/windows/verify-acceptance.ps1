param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$Output,
    [ValidateRange(1, 30)][int]$WarmupSeconds = 2,
    [ValidateRange(2, 61)][int]$SampleCount = 6,
    [ValidateRange(250, 5000)][int]$IntervalMilliseconds = 1000,
    [ValidateRange(1, 3)][int]$Repetitions = 3,
    [ValidateRange(1, 100)][int]$Cycles = 100,
    [string]$Budget,
    [switch]$Worker
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($Worker) {
    # Windows PowerShell's .NET Framework supplies the OS UI Automation assemblies.
    try {
        $settings = $env:HORMUZ_PREVIEW_ACCEPTANCE_CONFIG | ConvertFrom-Json
        Add-Type -Path (Join-Path $PSScriptRoot "PreviewAcceptance.cs") -ReferencedAssemblies `
            UIAutomationClient, UIAutomationTypes, WindowsBase, System.Management
        $result = [PreviewAcceptance]::Run(
            $settings.process_id, $settings.start_ticks, $settings.warmup,
            $settings.samples, $settings.interval, $settings.repetitions, $settings.cycles)
        $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $settings.result_path -Encoding UTF8
        exit 0
    } catch {
        Write-Output ("windows_ui_acceptance=failed " + $_.Exception.GetBaseException().Message)
        exit 1
    }
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw "Native preview acceptance requires Windows."
}
$previewExecutable = (Resolve-Path -LiteralPath $Executable).Path
$outputPath = [IO.Path]::GetFullPath($Output)
if (Test-Path -LiteralPath $outputPath) { throw "Refusing to replace existing acceptance evidence." }
if (-not (Test-Path -LiteralPath ([IO.Path]::GetDirectoryName($outputPath)) -PathType Container)) {
    throw "The evidence output directory must already exist."
}
$budgetConfig = $null
if ($Budget) {
    if ($Cycles -ne 100) { throw "Interaction budgets require exactly 100 cycles." }
    $budgetPath = (Resolve-Path -LiteralPath $Budget).Path
    $budgetDocument = Get-Content -LiteralPath $budgetPath -Raw | ConvertFrom-Json
    if ($budgetDocument.schema_id -ne "hormuz.native-client-footprint-budgets" -or
        $budgetDocument.schema_version -ne 1 -or $budgetDocument.status -ne "proposed" -or
        $null -eq $budgetDocument.windows) {
        throw "Unexpected footprint budget schema."
    }
    $budgetConfig = $budgetDocument.windows
    $requiredBudgetFields = @(
        "post_100_cycle_working_set_growth_bytes_max",
        "post_100_cycle_private_bytes_growth_max",
        "post_100_cycle_cpu_seconds_max",
        "post_100_cycle_handle_growth_max",
        "post_100_cycle_gdi_object_growth_max",
        "post_100_cycle_user_object_growth_max"
    )
    foreach ($field in $requiredBudgetFields) {
        if ($budgetConfig.PSObject.Properties.Name -notcontains $field -or
            [double]$budgetConfig.$field -lt 0) {
            throw "Windows interaction budget '$field' is missing or invalid."
        }
    }
}
$sourceCommit = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $sourceCommit -notmatch '^[0-9a-f]{40}$') {
    throw "Source revision is unavailable."
}
$proposedHead = $env:HORMUZ_PR_HEAD
if (-not $proposedHead) { $proposedHead = $sourceCommit }
if ($proposedHead -notmatch '^[0-9a-f]{40}$') { throw "Proposed revision must be a full commit SHA." }
$artifactHash = (Get-FileHash -LiteralPath $previewExecutable -Algorithm SHA256).Hash.ToLowerInvariant()
$temporary = Join-Path ([IO.Path]::GetTempPath()) ("hormuz-windows-acceptance-" + [guid]::NewGuid())
[IO.Directory]::CreateDirectory($temporary) | Out-Null
$preview = $null
$workerProcess = $null
$previousSettings = $env:HORMUZ_PREVIEW_ACCEPTANCE_CONFIG
try {
    # The outer watchdog owns the preview even if a UI Automation provider hangs in the worker.
    $stateDirectory = Join-Path $temporary "private"
    $preview = Start-Process -FilePath $previewExecutable -ArgumentList @("--preview", "--state-directory", ('"' + $stateDirectory + '"')) -PassThru
    $resultPath = Join-Path $temporary "result.json"
    $env:HORMUZ_PREVIEW_ACCEPTANCE_CONFIG = @{
        process_id = $preview.Id
        start_ticks = $preview.StartTime.ToUniversalTime().Ticks
        result_path = $resultPath
        warmup = $WarmupSeconds
        samples = $SampleCount
        interval = $IntervalMilliseconds
        repetitions = $Repetitions
        cycles = $Cycles
    } | ConvertTo-Json -Compress
    $stdout = Join-Path $temporary "worker-stdout.txt"
    $stderr = Join-Path $temporary "worker-stderr.txt"
    $powershell = Join-Path $env:SystemRoot "System32/WindowsPowerShell/v1.0/powershell.exe"
    $workerProcess = Start-Process -FilePath $powershell -PassThru `
        -ArgumentList @("-NoProfile", "-NonInteractive", "-MTA", "-File", ('"' + $PSCommandPath + '"'),
            "-Worker", "-Executable", "unused", "-Output", "unused") `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $timeoutSeconds = 90 + 3 * $Repetitions * ($WarmupSeconds + $SampleCount * $IntervalMilliseconds / 1000) + 2 * $WarmupSeconds
    if (-not $workerProcess.WaitForExit([int]($timeoutSeconds * 1000))) {
        throw "Bounded external UI Automation acceptance timed out."
    }
    if ($workerProcess.ExitCode -ne 0) {
        Write-Output ([IO.File]::ReadAllText($stdout).Trim())
        throw "External UI Automation acceptance failed."
    }
    if ([IO.File]::ReadAllText($stderr).Length -ne 0) { throw "Acceptance worker emitted a diagnostic." }
    if (-not $preview.WaitForExit(5000) -or $preview.ExitCode -ne 0) {
        throw "Owned preview did not exit cleanly."
    }
    if ((Get-FileHash -LiteralPath $previewExecutable -Algorithm SHA256).Hash.ToLowerInvariant() -ne $artifactHash) {
        throw "Preview artifact changed during acceptance."
    }
    $result = Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json
    $keyboardChecks = @(
        "sendinput_tab_shift_tab_focus_navigation",
        "sendinput_space_enter_fold_expand",
        "sendinput_escape_enter_hide_and_reopen_focus"
    )
    if ($result.result -ne "passed" -or $result.completed_cycles -ne $Cycles -or
        $result.samples.Count -ne (3 * $Repetitions * $SampleCount + 2) -or
        $result.keyboard_driver -ne "Win32 SendInput synthetic virtual-key events" -or
        @($keyboardChecks | Where-Object { $result.checks -notcontains $_ }).Count -ne 0) {
        throw "Acceptance evidence is incomplete."
    }
    $beforeCycle = @($result.samples | Where-Object { $_.scenario -eq "before_cycles" })
    $afterCycle = @($result.samples | Where-Object { $_.scenario -eq "after_cycles" })
    if ($beforeCycle.Count -ne 1 -or $afterCycle.Count -ne 1) {
        throw "Interaction-growth boundary samples are incomplete."
    }
    $interactionGrowth = [ordered]@{
        cycles = $Cycles
        working_set_bytes = [long]$afterCycle[0].working_set_bytes - [long]$beforeCycle[0].working_set_bytes
        private_bytes = [long]$afterCycle[0].private_bytes - [long]$beforeCycle[0].private_bytes
        process_cpu_seconds = [double]$afterCycle[0].process_cpu_seconds - [double]$beforeCycle[0].process_cpu_seconds
        handle_count = [int]$afterCycle[0].handle_count - [int]$beforeCycle[0].handle_count
        gdi_objects = [long]$afterCycle[0].gdi_objects - [long]$beforeCycle[0].gdi_objects
        user_objects = [long]$afterCycle[0].user_objects - [long]$beforeCycle[0].user_objects
    }
    $budgetStatus = "not_evaluated"
    if ($null -ne $budgetConfig) {
        if ($interactionGrowth.process_cpu_seconds -lt 0 -or
            $interactionGrowth.working_set_bytes -gt [long]$budgetConfig.post_100_cycle_working_set_growth_bytes_max -or
            $interactionGrowth.private_bytes -gt [long]$budgetConfig.post_100_cycle_private_bytes_growth_max -or
            $interactionGrowth.process_cpu_seconds -gt [double]$budgetConfig.post_100_cycle_cpu_seconds_max -or
            $interactionGrowth.handle_count -gt [int]$budgetConfig.post_100_cycle_handle_growth_max -or
            $interactionGrowth.gdi_objects -gt [long]$budgetConfig.post_100_cycle_gdi_object_growth_max -or
            $interactionGrowth.user_objects -gt [long]$budgetConfig.post_100_cycle_user_object_growth_max) {
            throw "The 100-cycle interaction observation exceeds a proposed footprint budget."
        }
        $budgetStatus = "passed"
    }
    $metadata = [ordered]@{
        schema_version = 2
        artifact_kind = "unsigned_connected_development_candidate"
        execution_mode = "synthetic_preview"
        source_commit = $sourceCommit
        proposed_head = $proposedHead
        artifact_sha256 = $artifactHash
        artifact_bytes = (Get-Item -LiteralPath $previewExecutable).Length
        os = [Environment]::OSVersion.VersionString
        architecture = $env:PROCESSOR_ARCHITECTURE
        logical_processors = [Environment]::ProcessorCount
        measurement_tools = "UIAutomationClient, Win32 SendInput, System.Diagnostics.Process, Win32 GetGuiResources, numeric Win32_Process PID/parent/creation-time topology"
        budget_status = $budgetStatus
        interaction_growth = $interactionGrowth
        evidence = $result
        limitations = @(
            "Short CI observations; proposed budgets are regression alarms, not a physical-footprint claim.",
            "Working set and private bytes are distinct counters; neither is total physical memory.",
            "No helpers were observed at sample times; short-lived processes between samples may be missed.",
            "Win32 SendInput exercises synthetic keyboard events only on this CI desktop; physical keyboard and screen-reader usability remain unverified.",
            "Foreground and focus were checked around keyboard input, but other desktop configurations are unqualified.",
            "Reopen uses a synthetic notification; actual tray activation remains pending.",
            "CPU counter precision and observer work can hide or perturb small changes.",
            "Observer CPU covers the worker measurement interval, excluding compilation, the watchdog and WMI service work.",
            "Startup, wake-ups, GPU, display migration, sign-in and active-client scenarios remain unmeasured.",
            "Host power, thermal state and display configuration are uncontrolled."
        )
        manual_platform_acceptance = "pending"
    }
    $staged = Join-Path $temporary "evidence.json"
    $metadata | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $staged -Encoding UTF8
    [IO.File]::Move($staged, $outputPath)
    Write-Output "windows_ui_acceptance=passed keyboard=sendinput_synthetic cycles=$Cycles samples=$($result.samples.Count) manual_acceptance=pending"
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
    $env:HORMUZ_PREVIEW_ACCEPTANCE_CONFIG = $previousSettings
    Remove-Item -LiteralPath $temporary -Recurse -Force
    if ($cleanupFailed) { throw "An owned acceptance process could not be stopped within the cleanup deadline." }
}
