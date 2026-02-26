[CmdletBinding()]
param(
    [ValidateSet("backend", "frontend", "e2e")]
    [string[]]$Only,

    [ValidateSet("backend", "frontend", "e2e")]
    [string[]]$Skip,

    [switch]$SkipFrontend,
    [switch]$RunE2E,
    [string]$BaseUrl = "http://127.0.0.1:8000"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true

try {
    [Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
}
catch {
    # no-op: fallback to host defaults
}

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$backendDir = Join-Path $repoRoot "backend"
$frontendDir = Join-Path $repoRoot "frontend"

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logsDir = Join-Path $repoRoot "scripts\logs\$timestamp"
New-Item -ItemType Directory -Path $logsDir -Force | Out-Null

function Get-SafeStepName {
    param([Parameter(Mandatory = $true)][string]$Name)
    $sanitized = $Name.ToLowerInvariant() -replace "[^a-z0-9]+", "-"
    return $sanitized.Trim("-")
}

function Get-DisplayCommand {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $all = @($FilePath) + $Arguments
    $quoted = $all | ForEach-Object {
        if (($_ -match '\s') -or $_.Contains('"')) {
            '"{0}"' -f ($_ -replace '"', '""')
        }
        else {
            $_
        }
    }
    return ($quoted -join " ")
}

function Invoke-Step {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $start = Get-Date
    $stepName = Get-SafeStepName -Name $Name
    $stdoutLog = Join-Path $logsDir ("{0}-{1}.stdout.log" -f $stepName, $timestamp)
    $stderrLog = Join-Path $logsDir ("{0}-{1}.stderr.log" -f $stepName, $timestamp)
    $combinedLog = Join-Path $logsDir ("{0}-{1}.log" -f $stepName, $timestamp)

    Write-Host ""
    Write-Host ("=== [{0}] {1} ===" -f (Get-Date -Format "HH:mm:ss"), $Name) -ForegroundColor Cyan
    Write-Host ("PWD: {0}" -f $WorkingDirectory)
    Write-Host ("CMD: {0}" -f (Get-DisplayCommand -FilePath $FilePath -Arguments $Arguments))

    $exitCode = 1
    $status = "FAIL"

    try {
        $proc = Start-Process `
            -FilePath $FilePath `
            -ArgumentList $Arguments `
            -WorkingDirectory $WorkingDirectory `
            -NoNewWindow `
            -Wait `
            -PassThru `
            -RedirectStandardOutput $stdoutLog `
            -RedirectStandardError $stderrLog

        $exitCode = [int]$proc.ExitCode
        $status = if ($exitCode -eq 0) { "PASS" } else { "FAIL" }
    }
    catch {
        $exitCode = 1
        $status = "FAIL"
        $_ | Out-String | Set-Content -Path $stderrLog -Encoding UTF8
    }

    if (Test-Path $stdoutLog) {
        Get-Content -Path $stdoutLog | Out-Host
    }
    if (Test-Path $stderrLog) {
        $stderrText = Get-Content -Path $stderrLog
        if ($stderrText) {
            $stderrText | Out-Host
        }
    }

    "# $Name" | Set-Content -Path $combinedLog -Encoding UTF8
    if (Test-Path $stdoutLog) {
        Add-Content -Path $combinedLog -Value "`n## STDOUT`n"
        Get-Content -Path $stdoutLog | Add-Content -Path $combinedLog
    }
    if (Test-Path $stderrLog) {
        Add-Content -Path $combinedLog -Value "`n## STDERR`n"
        Get-Content -Path $stderrLog | Add-Content -Path $combinedLog
    }

    $duration = [math]::Round(((Get-Date) - $start).TotalSeconds, 2)
    $color = if ($status -eq "PASS") { "Green" } else { "Red" }
    Write-Host ("--- {0}: {1} (exit={2}, {3}s) ---" -f $Name, $status, $exitCode, $duration) -ForegroundColor $color

    return [PSCustomObject]@{
        Step        = $Name
        Status      = $status
        ExitCode    = $exitCode
        DurationSec = $duration
        LogFile     = $combinedLog
    }
}

function Wait-BackendReady {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSec = 25
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri $Url -Method Get -TimeoutSec 3 -UseBasicParsing
            if ($resp.StatusCode -eq 200) {
                return $true
            }
        }
        catch {
            Start-Sleep -Milliseconds 400
        }
    }

    return $false
}

$run = [ordered]@{
    backend  = $true
    frontend = $true
    e2e      = $true
}

if ($Only -and $Only.Count -gt 0) {
    foreach ($key in @("backend", "frontend", "e2e")) {
        $run[$key] = $false
    }
    foreach ($item in $Only) {
        $run[$item] = $true
    }
}

if ($SkipFrontend) {
    $run["frontend"] = $false
}
if ($RunE2E) {
    $run["e2e"] = $true
}
if ($Skip -and $Skip.Count -gt 0) {
    foreach ($item in $Skip) {
        $run[$item] = $false
    }
}

$selected = @($run.GetEnumerator() | Where-Object { $_.Value } | ForEach-Object { $_.Key })
if ($selected.Count -eq 0) {
    Write-Host "No steps selected. Use -Only/-Skip/-RunE2E/-SkipFrontend to choose stages." -ForegroundColor Yellow
    exit 2
}

$backendPython = Join-Path $backendDir ".venv\Scripts\python.exe"
if (-not (Test-Path $backendPython)) {
    $backendPython = "python"
}

Write-Host ""
Write-Host "=== Gate Plan ===" -ForegroundColor Magenta
Write-Host ("Selected stages: {0}" -f ($selected -join ", "))
Write-Host ("Logs directory : {0}" -f $logsDir)
Write-Host ""

$results = @()

if ($run["backend"]) {
    $results += Invoke-Step `
        -Name "backend pytest" `
        -WorkingDirectory $backendDir `
        -FilePath $backendPython `
        -Arguments @("-m", "pytest", "-q")
}

$isWindowsHost = [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform([System.Runtime.InteropServices.OSPlatform]::Windows)
$npmCli = if ($isWindowsHost) { "npm.cmd" } else { "npm" }

if ($run["frontend"]) {
    $results += Invoke-Step `
        -Name "frontend build" `
        -WorkingDirectory $frontendDir `
        -FilePath $npmCli `
        -Arguments @("run", "build")
}

if ($run["e2e"]) {
    $healthUrl = "{0}/health" -f $BaseUrl.TrimEnd("/")
    $backendServerProc = $null
    $backendStartedHere = $false
    $backendReady = Wait-BackendReady -Url $healthUrl -TimeoutSec 5

    if (-not $backendReady) {
        Write-Host "Backend not detected for e2e. Starting temporary backend server..." -ForegroundColor Yellow

        $bindHost = "127.0.0.1"
        $bindPort = 8000
        try {
            $uri = [Uri]$BaseUrl
            if ($uri.Host) { $bindHost = $uri.Host }
            if ($uri.Port -gt 0) { $bindPort = $uri.Port }
        }
        catch {
            Write-Host "BaseUrl parse failed, fallback to 127.0.0.1:8000" -ForegroundColor Yellow
        }

        $serverStdout = Join-Path $logsDir ("backend-server-{0}.stdout.log" -f $timestamp)
        $serverStderr = Join-Path $logsDir ("backend-server-{0}.stderr.log" -f $timestamp)

        try {
            $backendServerProc = Start-Process `
                -FilePath $backendPython `
                -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", $bindHost, "--port", "$bindPort") `
                -WorkingDirectory $backendDir `
                -NoNewWindow `
                -PassThru `
                -RedirectStandardOutput $serverStdout `
                -RedirectStandardError $serverStderr

            $backendStartedHere = $true
            $backendReady = Wait-BackendReady -Url $healthUrl -TimeoutSec 25
        }
        catch {
            Write-Host ("Failed to start backend server: {0}" -f $_.Exception.Message) -ForegroundColor Yellow
            Write-Host "Retrying backend health probe in case another process already bound the port..." -ForegroundColor Yellow
            $backendReady = Wait-BackendReady -Url $healthUrl -TimeoutSec 8
            if (-not $backendReady) {
                Write-Host "Backend health probe still failed after startup retry." -ForegroundColor Red
            }
        }
    }

    if (-not $backendReady) {
        $results += [PSCustomObject]@{
            Step        = "e2e smoke"
            Status      = "FAIL"
            ExitCode    = 1
            DurationSec = 0
            LogFile     = "backend health check unavailable: $healthUrl"
        }
    }
    else {
        $results += Invoke-Step `
            -Name "e2e smoke" `
            -WorkingDirectory $repoRoot `
            -FilePath $backendPython `
            -Arguments @("scripts/e2e_smoke.py", "--base-url", $BaseUrl)
    }

    if ($backendStartedHere -and $backendServerProc) {
        try {
            if (-not $backendServerProc.HasExited) {
                Stop-Process -Id $backendServerProc.Id -Force
            }
        }
        catch {
            Write-Host ("Failed to stop temporary backend server (pid={0}): {1}" -f $backendServerProc.Id, $_.Exception.Message) -ForegroundColor Yellow
        }
    }
}

Write-Host ""
Write-Host "=== Gate Summary ===" -ForegroundColor Magenta
$results | Format-Table -AutoSize

$summaryFile = Join-Path $logsDir "summary.json"
$results | ConvertTo-Json -Depth 4 | Set-Content -Path $summaryFile -Encoding UTF8
Write-Host ("Logs   : {0}" -f $logsDir)
Write-Host ("Summary: {0}" -f $summaryFile)

$failed = @($results | Where-Object { $_.ExitCode -ne 0 })
if ($failed.Count -gt 0) {
    Write-Host ("Gate FAILED: {0}/{1} step(s) failed." -f $failed.Count, $results.Count) -ForegroundColor Red
    exit 1
}

Write-Host "Gate PASSED." -ForegroundColor Green
exit 0
