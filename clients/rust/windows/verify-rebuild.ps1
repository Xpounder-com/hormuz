$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    $executable = "target/release/hormuz-windows.exe"
    $first = (Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant()
    # Remove this package's release outputs, retaining the exact dependency objects.
    # The rebuilt binary must be byte-identical to the one already exercised by UIA.
    cargo clean -p hormuz-windows --release
    if ($LASTEXITCODE -ne 0) { throw "Preview clean failed." }
    if (Test-Path -LiteralPath $executable) { throw "Preview executable survived the clean." }
    Start-Sleep -Seconds 2
    cargo build -p hormuz-windows --release --locked --offline
    if ($LASTEXITCODE -ne 0) { throw "Preview rebuild failed." }
    $second = (Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($first -ne $second) { throw "Same-input preview rebuild changed the executable bytes." }
    @{
        schema_version = 1
        source_commit = (git rev-parse HEAD).Trim()
        compiler = (rustc --version).Trim()
        first_sha256 = $first
        second_sha256 = $second
        result = "passed"
        scope = "same_runner_same_checkout_cached_dependencies_package_clean_rebuild"
        cross_machine_reproducibility = "unqualified"
    } | ConvertTo-Json | Set-Content target/release/windows-rebuild.json -Encoding UTF8
    Write-Output "windows_preview_repeat_build=passed sha256=$second"
} finally {
    Pop-Location
}
