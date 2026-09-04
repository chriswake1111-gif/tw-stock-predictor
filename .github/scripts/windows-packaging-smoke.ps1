param(
    [Parameter(Mandatory = $true)]
    [string]$PackageRoot,

    [Parameter(Mandatory = $true)]
    [string]$InstallRoot,

    [Parameter(Mandatory = $true)]
    [string]$UserRoot
)

$ErrorActionPreference = "Stop"

function Assert-True {
    param(
        [bool]$Condition,
        [string]$Message
    )

    if (-not $Condition) {
        throw "Smoke assertion failed: $Message"
    }
}

function Resolve-FullPath {
    param([string]$Path)

    return [System.IO.Path]::GetFullPath($Path)
}

function Write-StructuredDiagnostic {
    param(
        [string]$Label,
        [string]$Text
    )

    if (-not $Text) {
        return
    }
    try {
        $payload = $Text | ConvertFrom-Json
        $fields = @()
        foreach ($name in @("status", "code")) {
            $property = $payload.PSObject.Properties[$name]
            if ($null -ne $property -and $null -ne $property.Value) {
                $fields += "$name=$($property.Value)"
            }
        }
        if ($fields.Count -gt 0) {
            Write-Host "$Label diagnostic: $($fields -join ',')"
        } else {
            Write-Host "$Label diagnostic: structured output without status/code"
        }
    } catch {
        Write-Host "$Label diagnostic: non-JSON output (length=$($Text.Length))"
    }
}

function Write-StartupDiagnostics {
    param(
        [System.Diagnostics.Process]$Process,
        [string]$Root
    )

    if ($null -ne $Process) {
        try {
            if ($Process.HasExited) {
                Write-Host "Startup process exit code: $($Process.ExitCode)"
                Write-StructuredDiagnostic -Label "Startup stdout" -Text $Process.StandardOutput.ReadToEnd()
                Write-StructuredDiagnostic -Label "Startup stderr" -Text $Process.StandardError.ReadToEnd()
            } else {
                Write-Host "Startup process is still running: $($Process.Id)"
            }
        } catch {
            Write-Host "Unable to read startup process diagnostics"
        }
    }

    if (Test-Path -LiteralPath $Root) {
        $runtimePath = Join-Path $Root "runtime"
        $logsPath = Join-Path $Root "logs"
        $dataPath = Join-Path $Root "data"
        Write-Host "User-root state: runtime=$([bool](Test-Path -LiteralPath $runtimePath)),logs=$([bool](Test-Path -LiteralPath $logsPath)),data=$([bool](Test-Path -LiteralPath $dataPath))"
        $codes = @()
        $errorCodes = @()
        foreach ($log in (Get-ChildItem -LiteralPath $Root -Recurse -Filter "*.log*" -File -Force)) {
            foreach ($line in (Get-Content -LiteralPath $log.FullName)) {
                try {
                    $record = $line | ConvertFrom-Json
                    $property = $record.PSObject.Properties["code"]
                    if ($null -ne $property -and $null -ne $property.Value) {
                        $codes += [string]$property.Value
                    }
                    $context = $record.PSObject.Properties["context"]
                    if ($null -ne $context -and $null -ne $context.Value) {
                        $errorCode = $context.Value.PSObject.Properties["error_code"]
                        if ($null -ne $errorCode -and $null -ne $errorCode.Value) {
                            $errorCodes += [string]$errorCode.Value
                        }
                    }
                } catch {
                    continue
                }
            }
        }
        if ($codes.Count -gt 0) {
            Write-Host "Startup log event codes: $($codes -join ',')"
        }
        if ($errorCodes.Count -gt 0) {
            Write-Host "Startup error codes: $($errorCodes -join ',')"
        }
    }
}

function Assert-UnderRunnerTemp {
    param([string]$Path)

    $runnerTemp = Resolve-FullPath $env:RUNNER_TEMP
    $candidate = Resolve-FullPath $Path
    $prefix = $runnerTemp.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    Assert-True ($candidate.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) `
        "temporary test path must be below RUNNER_TEMP: $candidate"
}

function Wait-ForPath {
    param(
        [string]$Path,
        [int]$TimeoutSeconds = 45,
        [System.Diagnostics.Process]$Process,
        [string]$DiagnosticRoot
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while (-not (Test-Path -LiteralPath $Path) -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 500
    }
    if (-not (Test-Path -LiteralPath $Path)) {
        Write-StartupDiagnostics -Process $Process -Root $DiagnosticRoot
        Assert-True $false "expected path was not created: $Path"
    }
}

function Wait-ForDataOperation {
    param(
        [string]$Origin,
        [string]$OperationId,
        [int]$TimeoutSeconds = 180,
        [Microsoft.PowerShell.Commands.WebRequestSession]$WebSession = $null
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $restParams = @{
                Uri = "$Origin/api/v2/data-operations/operations/$OperationId"
                UseBasicParsing = $true
                TimeoutSec = 10
            }
            if ($null -ne $WebSession) {
                $restParams["WebSession"] = $WebSession
            }
            $op = Invoke-RestMethod @restParams
            if ($op.status -in @("succeeded", "failed", "partial", "cancelled", "interrupted")) {
                return $op
            }
        } catch {
            Write-Host "Transient poll attempt for $OperationId caught: $($_.Exception.Message)"
        }
        Start-Sleep -Milliseconds 1500
    }
    Assert-True $false "operation $OperationId did not reach terminal state within $TimeoutSeconds seconds"
}

function New-ProductProcess {
    param(
        [string]$FilePath,
        [string]$Arguments = "",
        [string]$ScenarioRoot = $UserRoot,
        [bool]$RedirectOutput = $true
    )

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $FilePath
    $rootArgument = "--user-root `"$ScenarioRoot`""
    $startInfo.Arguments = if ($Arguments) { "$rootArgument $Arguments" } else { $rootArgument }
    $startInfo.WorkingDirectory = $InstallRoot
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $RedirectOutput
    $startInfo.RedirectStandardError = $RedirectOutput
    $startInfo.EnvironmentVariables.Clear()
    foreach ($name in @("SystemRoot", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT")) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if ($value) {
            $startInfo.EnvironmentVariables[$name] = $value
        }
    }
    $startInfo.EnvironmentVariables["PATH"] = "$env:SystemRoot\System32;$env:SystemRoot"
    return [System.Diagnostics.Process]::Start($startInfo)
}

function Invoke-Fixture {
    param([string[]]$FixtureArguments)

    & python -B "$env:GITHUB_WORKSPACE\.github\scripts\phase18-smoke-fixture.py" @FixtureArguments | Out-Null
    Assert-True ($LASTEXITCODE -eq 0) "fixture command failed: $($FixtureArguments -join ' ')"
}

function Stop-Scenario {
    param(
        [System.Diagnostics.Process]$LauncherProcess,
        [string]$ScenarioRoot
    )

    $stopProcess = New-ProductProcess -FilePath $launcher -Arguments "--stop" -ScenarioRoot $ScenarioRoot
    $stopResult = Wait-ProductExit -Process $stopProcess -Scenario "scenario stop"
    $stopPayload = $stopResult.stdout | ConvertFrom-Json
    Assert-True ($stopResult.exit_code -eq 0) "scenario stop failed: status=$($stopPayload.status),reason=$($stopPayload.reason)"
    Assert-True ($stopPayload.status -eq "stopped") "scenario stop did not report stopped"

    $exited = $LauncherProcess.WaitForExit(30000)
    if (-not $exited -and $LauncherProcess.HasExited) {
        $exited = $true
    }
    Assert-True $exited "scenario launcher did not exit: $($LauncherProcess.Id)"
}

function Wait-ProductExit {
    param(
        [System.Diagnostics.Process]$Process,
        [string]$Scenario,
        [int]$TimeoutMilliseconds = 30000
    )

    Assert-True $Process.WaitForExit($TimeoutMilliseconds) `
        "$Scenario product process did not exit: $($Process.Id)"
    return [ordered]@{
        exit_code = $Process.ExitCode
        stdout = $Process.StandardOutput.ReadToEnd()
        stderr = $Process.StandardError.ReadToEnd()
    }
}

function Assert-ProcessGone {
    param(
        [int]$ProcessId,
        [int]$TimeoutSeconds = 30
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (-not (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
            return
        }
        Start-Sleep -Milliseconds 500
    }
    Assert-True (-not (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) `
        "process is still running: $ProcessId"
}

$package = (Resolve-Path -LiteralPath $PackageRoot).Path
$install = Resolve-FullPath $InstallRoot
$user = Resolve-FullPath $UserRoot
Assert-UnderRunnerTemp $install
Assert-UnderRunnerTemp $user

$installer = Join-Path $package "installer\tw-stock-predictor-setup.exe"
$launcher = Join-Path $install "tw-stock-predictor\tw-stock-predictor.exe"
$server = Join-Path $install "tw-stock-predictor-server\tw-stock-predictor-server.exe"
$runtimeDescriptor = Join-Path $user "runtime\instance.json"
$sentinel = Join-Path $user "data\user-sentinel.txt"
$smokeSummaryPath = Join-Path $package "smoke-summary.json"
$fixture = Join-Path $env:GITHUB_WORKSPACE ".github\scripts\phase18-smoke-fixture.py"

if (Test-Path -LiteralPath $install) {
    Remove-Item -LiteralPath $install -Recurse -Force
}
if (Test-Path -LiteralPath $user) {
    Remove-Item -LiteralPath $user -Recurse -Force
}
New-Item -ItemType Directory -Path $install -Force | Out-Null
New-Item -ItemType Directory -Path $user -Force | Out-Null

Assert-True (Test-Path -LiteralPath $installer) "installer is missing: $installer"
Assert-True (Test-Path -LiteralPath $fixture) "smoke fixture helper is missing: $fixture"

$installDirArgument = "/DIR=`"$install`""
$installerProcess = Start-Process -FilePath $installer -ArgumentList @(
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    $installDirArgument
) -Wait -PassThru
Assert-True ($installerProcess.ExitCode -eq 0) "installer failed with exit code $($installerProcess.ExitCode)"
Assert-True (Test-Path -LiteralPath $launcher) "installed launcher is missing: $launcher"
Assert-True (Test-Path -LiteralPath $server) "installed server is missing: $server"

$first = $null
$second = $null
$stop = $null
$orphan = $null
$serverPid = $null
$orphanServerPid = $null
$upgradeProcess = $null
$legacyProcess = $null
$corruptProcess = $null
$recoveryProcess = $null
$rejectedRecoveryProcess = $null
$writerRejectedProcess = $null
$logProcess = $null
try {
    Write-Host "Smoke scenario: fresh installed startup"
    $first = New-ProductProcess -FilePath $launcher -RedirectOutput $false
    Wait-ForPath -Path $runtimeDescriptor -Process $first -DiagnosticRoot $user
    $descriptor = Get-Content -LiteralPath $runtimeDescriptor -Raw | ConvertFrom-Json
    Assert-True ($descriptor.origin -match '^http://127\.0\.0\.1:\d+$') "origin is not loopback: $($descriptor.origin)"
    $serverPid = [int]$descriptor.server_pid
    $serverProcess = Get-Process -Id $serverPid -ErrorAction Stop
    Assert-True ($serverProcess.Path -eq $server) "server is not the installed packaged executable"

    $ready = Invoke-RestMethod -Uri "$($descriptor.origin)/api/ready" -UseBasicParsing -TimeoutSec 15
    Assert-True ($ready.contract_version -eq "tw_stock_ready_v1") "readiness contract mismatch"
    Assert-True ($ready.ready -eq $true) "packaged server did not become ready"
    $daily = Invoke-WebRequest -Uri "$($descriptor.origin)/research/daily" -UseBasicParsing -TimeoutSec 15
    Assert-True ($daily.StatusCode -eq 200) "research/daily did not return HTTP 200"

    # Phase 19 clean-machine data-operations verification
    $dataStatus = Invoke-RestMethod -Uri "$($descriptor.origin)/api/v2/data-operations/status" -UseBasicParsing -TimeoutSec 15
    Assert-True ($null -ne $dataStatus.readiness) "data-operations status readiness missing"

    $csrfRes = Invoke-RestMethod -Uri "$($descriptor.origin)/api/v2/data-operations/csrf-token" -UseBasicParsing -TimeoutSec 15 -SessionVariable "smokeSession"
    Assert-True ($null -ne $csrfRes.csrf_token) "csrf token missing"

    $syncHeaders = @{
        "Origin" = "$($descriptor.origin)"
        "X-CSRF-Token" = $csrfRes.csrf_token
        "Content-Type" = "application/json"
    }
    # 1. Trigger sync and poll to terminal completed state
    $syncRes = Invoke-RestMethod -Uri "$($descriptor.origin)/api/v2/data-operations/sync" -Method POST -Headers $syncHeaders -Body "{}" -WebSession $smokeSession -TimeoutSec 15
    Assert-True ($syncRes.status -in @("running", "succeeded")) "sync did not start"
    $syncOp = Wait-ForDataOperation -Origin $descriptor.origin -OperationId $syncRes.operation_id -TimeoutSeconds 180 -WebSession $smokeSession
    $syncItemsJson = if ($syncOp.items) { ($syncOp.items | ConvertTo-Json -Compress) } else { "none" }
    Assert-True ($syncOp.status -in @("succeeded", "partial")) "sync operation failed: $($syncOp.status), error: $($syncOp.error_detail), items: $syncItemsJson"

    # 2. Trigger on-demand symbol enablement and poll to terminal completed state
    $enableRes = Invoke-RestMethod -Uri "$($descriptor.origin)/api/v2/data-operations/symbols/2330.TW/enable" -Method POST -Headers $syncHeaders -Body "{}" -WebSession $smokeSession -TimeoutSec 15
    Assert-True ($enableRes.status -in @("running", "succeeded")) "enable symbol did not start"
    $enableOp = Wait-ForDataOperation -Origin $descriptor.origin -OperationId $enableRes.operation_id -TimeoutSeconds 120 -WebSession $smokeSession
    $enableItemsJson = if ($enableOp.items) { ($enableOp.items | ConvertTo-Json -Compress) } else { "none" }
    Assert-True ($enableOp.status -in @("succeeded", "partial")) "enable symbol operation failed: $($enableOp.status), error: $($enableOp.error_detail), items: $enableItemsJson"

    # 3. Assert BC-2: Authoritative Phase 14 EOD context proof
    $cutoff = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
    $eodRes = Invoke-RestMethod -Uri "$($descriptor.origin)/api/v2/market-context/eod-close/as-of/2330.TW?knowledge_cutoff_at=$cutoff" -UseBasicParsing -TimeoutSec 15
    Assert-True ($null -ne $eodRes) "BC-2: EOD market context not returned"

    # 4. Assert BC-3: General V2 analysis regression
    $analysisRes = Invoke-WebRequest -Uri "$($descriptor.origin)/api/v2/analysis/2330.TW?knowledge_cutoff_at=$cutoff" -UseBasicParsing -TimeoutSec 15
    Assert-True ($analysisRes.StatusCode -eq 200) "BC-3: GET /api/v2/analysis/2330.TW did not return HTTP 200"

    $second = New-ProductProcess -FilePath $launcher
    Write-Host "Smoke scenario: single-instance rejection"
    $secondResult = Wait-ProductExit -Process $second -Scenario "single-instance rejection"
    $secondPayload = $secondResult.stdout | ConvertFrom-Json
    Assert-True ($secondResult.exit_code -eq 0) "second launch failed: $($secondResult.stderr)"
    Assert-True ($secondPayload.status -eq "existing_instance") "single-instance guard did not reject second launch"

    $stop = New-ProductProcess -FilePath $launcher -Arguments "--stop"
    Write-Host "Smoke scenario: graceful stop"
    $stopResult = Wait-ProductExit -Process $stop -Scenario "graceful stop"
    $stopPayload = $stopResult.stdout | ConvertFrom-Json
    Assert-True ($stopResult.exit_code -eq 0) `
        "stop command failed: status=$($stopPayload.status),reason=$($stopPayload.reason)"
    Assert-True ($stopPayload.status -eq "stopped") "stop command did not report stopped"
    Assert-True $first.WaitForExit(15000) "graceful stop did not release the launcher"
    Assert-ProcessGone -ProcessId $serverPid
    Assert-True (-not (Test-Path -LiteralPath $runtimeDescriptor)) "runtime descriptor was not cleared"

    $orphan = New-ProductProcess -FilePath $launcher -RedirectOutput $false
    Wait-ForPath -Path $runtimeDescriptor -Process $orphan -DiagnosticRoot $user
    $orphanDescriptor = Get-Content -LiteralPath $runtimeDescriptor -Raw | ConvertFrom-Json
    $orphanServerPid = [int]$orphanDescriptor.server_pid
    $orphanServerProcess = Get-Process -Id $orphanServerPid -ErrorAction Stop
    Assert-True ($orphanServerProcess.Path -eq $server) "orphan test did not start the installed server"
    if (-not $orphan.HasExited) {
        Stop-Process -Id $orphan.Id -Force
    }
    Assert-True $orphan.WaitForExit(15000) "orphan launcher did not exit after forced termination"
    Assert-ProcessGone -ProcessId $orphanServerPid

    # Installed known-v2-upgradeable startup must create pre-upgrade evidence and execute Scenario B regression.
    $upgradeRoot = Join-Path $env:RUNNER_TEMP "tw-stock-predictor-upgradeable"
    $upgradeDb = Join-Path $upgradeRoot "data\cache.db"
    New-Item -ItemType Directory -Path (Split-Path -Parent $upgradeDb) -Force | Out-Null
    Invoke-Fixture -FixtureArguments @("upgradeable", $upgradeDb, "--symbol", "2330.TW")
    $upgradeDescriptor = Join-Path $upgradeRoot "runtime\instance.json"
    $upgradeProcess = New-ProductProcess -FilePath $launcher -ScenarioRoot $upgradeRoot -RedirectOutput $false
    Wait-ForPath -Path $upgradeDescriptor -Process $upgradeProcess -DiagnosticRoot $upgradeRoot
    $upgradeDesc = Get-Content -LiteralPath $upgradeDescriptor -Raw | ConvertFrom-Json

    # Scenario B: Run sync & symbol enablement in upgraded instance
    $csrfResB = Invoke-RestMethod -Uri "$($upgradeDesc.origin)/api/v2/data-operations/csrf-token" -UseBasicParsing -TimeoutSec 15 -SessionVariable "smokeSessionB"
    Assert-True ($null -ne $csrfResB.csrf_token) "Scenario B: csrf token missing"

    $syncHeadersB = @{
        "Origin" = "$($upgradeDesc.origin)"
        "X-CSRF-Token" = $csrfResB.csrf_token
        "Content-Type" = "application/json"
    }
    $syncResB = Invoke-RestMethod -Uri "$($upgradeDesc.origin)/api/v2/data-operations/sync" -Method POST -Headers $syncHeadersB -Body "{}" -WebSession $smokeSessionB -TimeoutSec 15
    Assert-True ($syncResB.status -in @("running", "succeeded")) "Scenario B: sync did not start"
    $syncOpB = Wait-ForDataOperation -Origin $upgradeDesc.origin -OperationId $syncResB.operation_id -TimeoutSeconds 180 -WebSession $smokeSessionB
    Assert-True ($syncOpB.status -in @("succeeded", "partial")) "Scenario B: sync operation failed: $($syncOpB.status)"

    $enableResB = Invoke-RestMethod -Uri "$($upgradeDesc.origin)/api/v2/data-operations/symbols/2330.TW/enable" -Method POST -Headers $syncHeadersB -Body "{}" -WebSession $smokeSessionB -TimeoutSec 15
    Assert-True ($enableResB.status -in @("running", "succeeded")) "Scenario B: enable symbol did not start"
    $enableOpB = Wait-ForDataOperation -Origin $upgradeDesc.origin -OperationId $enableResB.operation_id -TimeoutSeconds 120 -WebSession $smokeSessionB
    Assert-True ($enableOpB.status -in @("succeeded", "partial")) "Scenario B: enable symbol operation failed: $($enableOpB.status)"

    # Verify Phase 17 Queue consumption via GET /api/v2/research/daily-context?market_date=<D>&knowledge_cutoff_at=<K>
    $statusB = Invoke-RestMethod -Uri "$($upgradeDesc.origin)/api/v2/data-operations/status" -UseBasicParsing -TimeoutSec 15
    $marketDateB = $statusB.market_context_summary.latest_eod_date
    if (-not $marketDateB) {
        $marketDateB = [DateTime]::UtcNow.ToString("yyyy-MM-dd")
    }
    $cutoffB = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
    $dailyContextRes = Invoke-RestMethod -Uri "$($upgradeDesc.origin)/api/v2/research/daily-context?market_date=$marketDateB&knowledge_cutoff_at=$cutoffB" -UseBasicParsing -TimeoutSec 15
    Assert-True ($null -ne $dailyContextRes) "Scenario B: GET /api/v2/research/daily-context did not return data"
    Assert-True ($dailyContextRes.items.Count -ge 1) "Scenario B: daily-context items empty"
    $item2330 = $dailyContextRes.items | Where-Object { $_.canonical_symbol -eq "2330.TW" }
    Assert-True ($null -ne $item2330) "Scenario B: 2330.TW not found in daily-context items"
    Assert-True ($item2330.watchlist_reference.symbol -eq "2330.TW") "Scenario B: 2330.TW queue item identity mismatch"
    if ($enableOpB.status -eq "succeeded") {
        Assert-True ($item2330.quality.phase14_status -eq "available") "Scenario B: 2330.TW did not consume materialized Phase14 EOD evidence"
    } else {
        Assert-True ($item2330.quality.phase14_status -in @("available", "partial", "needs_human_input", "insufficient_data", "unknown")) "Scenario B: unexpected phase14_status for partial enablement: $($item2330.quality.phase14_status)"
    }

    # Verify /research/daily UI bookmarkable route
    $dailyUiRes = Invoke-WebRequest -Uri "$($upgradeDesc.origin)/research/daily" -UseBasicParsing -TimeoutSec 15
    Assert-True ($dailyUiRes.StatusCode -eq 200) "Scenario B: GET /research/daily UI did not return HTTP 200"

    Stop-Scenario -LauncherProcess $upgradeProcess -ScenarioRoot $upgradeRoot
    $preUpgradeMetadata = Get-ChildItem -LiteralPath (Join-Path $upgradeRoot "backup") -Recurse -Filter "*.meta.json" -File |
        Where-Object { (Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json).reason -eq "pre_upgrade" }
    Assert-True ($preUpgradeMetadata.Count -ge 1) "installed upgrade did not preserve pre-upgrade metadata"

    # Installed legacy startup must preserve the source and activate a new current V2 DB.
    $legacyRoot = Join-Path $env:RUNNER_TEMP "tw-stock-predictor-legacy"
    $legacyDb = Join-Path $legacyRoot "data\cache.db"
    New-Item -ItemType Directory -Path (Split-Path -Parent $legacyDb) -Force | Out-Null
    Invoke-Fixture -FixtureArguments @("legacy", $legacyDb)
    $legacyHash = (Get-FileHash -LiteralPath $legacyDb -Algorithm SHA256).Hash
    $legacyDescriptor = Join-Path $legacyRoot "runtime\instance.json"
    $legacyProcess = New-ProductProcess -FilePath $launcher -ScenarioRoot $legacyRoot -RedirectOutput $false
    Wait-ForPath -Path $legacyDescriptor -Process $legacyProcess -DiagnosticRoot $legacyRoot
    Stop-Scenario -LauncherProcess $legacyProcess -ScenarioRoot $legacyRoot
    $legacyArchives = Get-ChildItem -LiteralPath (Join-Path $legacyRoot "backup\legacy") -Filter "legacy-source-*.db" -File
    Assert-True ($legacyArchives.Count -ge 1) "installed legacy source archive is missing"
    Assert-True ((Get-FileHash -LiteralPath $legacyArchives[0].FullName -Algorithm SHA256).Hash -eq $legacyHash) `
        "installed legacy source archive hash changed"

    # Corrupt/unknown startup must fail closed without replacing the canonical bytes.
    $corruptRoot = Join-Path $env:RUNNER_TEMP "tw-stock-predictor-corrupt"
    $corruptDb = Join-Path $corruptRoot "data\cache.db"
    New-Item -ItemType Directory -Path (Split-Path -Parent $corruptDb) -Force | Out-Null
    [IO.File]::WriteAllBytes($corruptDb, [Text.Encoding]::UTF8.GetBytes("not a sqlite database"))
    $corruptHash = (Get-FileHash -LiteralPath $corruptDb -Algorithm SHA256).Hash
    $corruptProcess = New-ProductProcess -FilePath $launcher -ScenarioRoot $corruptRoot
    Write-Host "Smoke scenario: corrupt database fail-closed"
    $corruptResult = Wait-ProductExit -Process $corruptProcess -Scenario "corrupt database"
    $corruptPayload = $corruptResult.stdout | ConvertFrom-Json
    Assert-True ($corruptResult.exit_code -eq 2) "corrupt installed startup did not fail"
    Assert-True ($corruptPayload.status -eq "failed" -and $corruptPayload.reason -eq "database_corrupt_unknown") `
        "corrupt installed startup returned the wrong failure"
    Assert-True ((Get-FileHash -LiteralPath $corruptDb -Algorithm SHA256).Hash -eq $corruptHash) `
        "corrupt canonical changed during failed startup"

    # Installed recovery CLI: success, fail-closed preservation, and active-writer rejection.
    $recoveryRoot = Join-Path $env:RUNNER_TEMP "tw-stock-predictor-recovery"
    $recoveryDb = Join-Path $recoveryRoot "data\cache.db"
    $recoverySource = Join-Path $recoveryRoot "fixture\source.db"
    $recoveryBackup = Join-Path $recoveryRoot "backup\source-backup.db"
    New-Item -ItemType Directory -Path (Split-Path -Parent $recoveryDb) -Force | Out-Null
    New-Item -ItemType Directory -Path (Split-Path -Parent $recoverySource) -Force | Out-Null
    New-Item -ItemType Directory -Path (Split-Path -Parent $recoveryBackup) -Force | Out-Null
    Invoke-Fixture -FixtureArguments @("current", $recoveryDb, "--symbol", "2317.TW")
    Invoke-Fixture -FixtureArguments @("current", $recoverySource, "--symbol", "2330.TW")
    Invoke-Fixture -FixtureArguments @("backup", $recoverySource, $recoveryBackup)
    $recovery = New-ProductProcess -FilePath $launcher -Arguments "recovery activate `"$recoveryBackup`"" -ScenarioRoot $recoveryRoot
    Write-Host "Smoke scenario: installed recovery activation"
    $recoveryResult = Wait-ProductExit -Process $recovery -Scenario "recovery activation"
    $recoveryPayload = $recoveryResult.stdout | ConvertFrom-Json
    Assert-True ($recoveryResult.exit_code -eq 0 -and $recoveryPayload.status -eq "activated") `
        "installed recovery activation failed"
    $symbolPayload = & python -B $fixture symbols $recoveryDb | ConvertFrom-Json
    Assert-True (($symbolPayload.symbols -join ",") -eq "2330.TW") "installed recovery activated the wrong database"

    $invalidSource = Join-Path $recoveryRoot "fixture\legacy.db"
    $invalidBackup = Join-Path $recoveryRoot "backup\legacy-backup.db"
    Invoke-Fixture -FixtureArguments @("legacy", $invalidSource)
    Invoke-Fixture -FixtureArguments @("backup", $invalidSource, $invalidBackup)
    $beforeRejectedRecovery = (Get-FileHash -LiteralPath $recoveryDb -Algorithm SHA256).Hash
    Write-Host "Smoke scenario: invalid recovery rejection"
    $rejectedRecoveryProcess = New-ProductProcess -FilePath $launcher -Arguments "recovery activate `"$invalidBackup`"" -ScenarioRoot $recoveryRoot
    $rejectedResult = Wait-ProductExit -Process $rejectedRecoveryProcess -Scenario "invalid recovery rejection"
    $rejectedPayload = $rejectedResult.stderr | ConvertFrom-Json
    Assert-True ($rejectedResult.exit_code -eq 2 -and $rejectedPayload.code -eq "restore_candidate_not_current") `
        "installed recovery did not reject a legacy candidate deterministically"
    Assert-True ((Get-FileHash -LiteralPath $recoveryDb -Algorithm SHA256).Hash -eq $beforeRejectedRecovery) `
        "canonical changed after rejected recovery"

    $recoveryDescriptor = Join-Path $recoveryRoot "runtime\instance.json"
    $recoveryProcess = New-ProductProcess -FilePath $launcher -ScenarioRoot $recoveryRoot -RedirectOutput $false
    Wait-ForPath -Path $recoveryDescriptor -Process $recoveryProcess -DiagnosticRoot $recoveryRoot
    Write-Host "Smoke scenario: active-writer recovery rejection"
    $writerRejectedProcess = New-ProductProcess -FilePath $launcher -Arguments "recovery activate `"$recoveryBackup`"" -ScenarioRoot $recoveryRoot
    $writerResult = Wait-ProductExit -Process $writerRejectedProcess -Scenario "active-writer recovery rejection"
    $writerPayload = $writerResult.stderr | ConvertFrom-Json
    Assert-True ($writerResult.exit_code -eq 2 -and $writerPayload.code -eq "restore_writer_active") `
        "installed recovery did not reject an active writer"
    Stop-Scenario -LauncherProcess $recoveryProcess -ScenarioRoot $recoveryRoot

    # Installed logging must retain only its own bounded segments and preserve non-log files.
    $logRoot = Join-Path $env:RUNNER_TEMP "tw-stock-predictor-logs"
    $logDir = Join-Path $logRoot "logs"
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $largeLog = Join-Path $logDir "launcher.log"
    [IO.File]::WriteAllBytes($largeLog, (New-Object byte[] (11 * 1024 * 1024)))
    $nonLog = Join-Path $logDir "preserve.bin"
    [IO.File]::WriteAllBytes($nonLog, [byte[]](1, 2, 3, 4))
    $logDescriptor = Join-Path $logRoot "runtime\instance.json"
    $logProcess = New-ProductProcess -FilePath $launcher -ScenarioRoot $logRoot -RedirectOutput $false
    Wait-ForPath -Path $logDescriptor -Process $logProcess -DiagnosticRoot $logRoot
    Stop-Scenario -LauncherProcess $logProcess -ScenarioRoot $logRoot
    $logicalLogs = Get-ChildItem -LiteralPath $logDir -Filter "launcher.log*" -File
    Assert-True ($logicalLogs.Count -le 6) "installed logging retained too many segments"
    Assert-True ((($logicalLogs | Measure-Object -Property Length -Sum).Sum) -le (60 * 1024 * 1024)) `
        "installed logical log exceeded 60 MiB"
    Assert-True (([IO.File]::ReadAllBytes($nonLog) -join ",") -eq "1,2,3,4") `
        "installed logging changed a non-log file"

    New-Item -ItemType Directory -Path (Split-Path -Parent $sentinel) -Force | Out-Null
    Set-Content -LiteralPath $sentinel -Value "preserve-me" -NoNewline
    $uninstaller = Join-Path $install "unins000.exe"
    Assert-True (Test-Path -LiteralPath $uninstaller) "uninstaller is missing: $uninstaller"
    $uninstallerProcess = Start-Process -FilePath $uninstaller -ArgumentList @(
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART"
    ) -PassThru
    Assert-True $uninstallerProcess.WaitForExit(60000) "uninstaller did not finish within 60s"
    Assert-True ($uninstallerProcess.ExitCode -eq 0) "uninstaller failed with exit code $($uninstallerProcess.ExitCode)"
    Assert-True (Test-Path -LiteralPath $sentinel) "user data was removed during uninstall"
    Assert-True ((Get-Content -LiteralPath $sentinel -Raw) -eq "preserve-me") "user sentinel changed during uninstall"

    $summary = [ordered]@{
        status = "passed"
        installer = $installer
        installed_launcher = $launcher
        installed_server = $server
        loopback_ready = $true
        research_daily_http_status = $daily.StatusCode
        single_instance_guard = $true
        graceful_stop = $true
        process_tree_cleanup = $true
        known_v2_current = $true
        known_v2_upgradeable = $true
        legacy_preservation = $true
        corrupt_unknown_fail_closed = $true
        installed_recovery = $true
        recovery_failure_preservation = $true
        active_writer_rejection = $true
        bounded_log_retention = $true
        uninstall_preserved_user_data = $true
        runtime_dependencies = "installed_onedir_executables_with_minimal_system_path_only"
    }
    $summary | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $smokeSummaryPath -Encoding UTF8
    $summary | ConvertTo-Json -Depth 4
}
catch {
    Write-Host "Smoke test failed: $_"
    Write-Host "Diagnostic: Dumping logs on failure from UserRoot ($UserRoot)..."
    Get-ChildItem -Path $UserRoot -Recurse -Filter "*.log" -File -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.Length -lt 1048576) {
            Write-Host "=== LOG: $($_.FullName) ==="
            Get-Content -LiteralPath $_.FullName -Tail 50 -ErrorAction SilentlyContinue
        }
    }
    throw
}
finally {
    foreach ($process in @($first, $second, $stop, $orphan, $upgradeProcess, $legacyProcess, $corruptProcess, $recoveryProcess, $rejectedRecoveryProcess, $writerRejectedProcess, $logProcess)) {
        if ($null -ne $process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
}
