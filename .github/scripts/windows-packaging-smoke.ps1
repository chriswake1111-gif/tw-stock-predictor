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

function New-ProductProcess {
    param(
        [string]$FilePath,
        [string]$Arguments = ""
    )

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $FilePath
    $startInfo.Arguments = $Arguments
    $startInfo.WorkingDirectory = Split-Path -Parent $FilePath
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.EnvironmentVariables["TW_STOCK_PACKAGED"] = "true"
    $startInfo.EnvironmentVariables["TW_STOCK_USER_ROOT"] = $UserRoot
    return [System.Diagnostics.Process]::Start($startInfo)
}

function Wait-ProductExit {
    param(
        [System.Diagnostics.Process]$Process,
        [int]$TimeoutMilliseconds = 30000
    )

    Assert-True $Process.WaitForExit($TimeoutMilliseconds) "product process did not exit: $($Process.Id)"
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

if (Test-Path -LiteralPath $install) {
    Remove-Item -LiteralPath $install -Recurse -Force
}
if (Test-Path -LiteralPath $user) {
    Remove-Item -LiteralPath $user -Recurse -Force
}
New-Item -ItemType Directory -Path $install -Force | Out-Null
New-Item -ItemType Directory -Path $user -Force | Out-Null

Assert-True (Test-Path -LiteralPath $installer) "installer is missing: $installer"

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
try {
    $first = New-ProductProcess -FilePath $launcher
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

    $second = New-ProductProcess -FilePath $launcher
    $secondResult = Wait-ProductExit -Process $second
    $secondPayload = $secondResult.stdout | ConvertFrom-Json
    Assert-True ($secondResult.exit_code -eq 0) "second launch failed: $($secondResult.stderr)"
    Assert-True ($secondPayload.status -eq "existing_instance") "single-instance guard did not reject second launch"

    $stop = New-ProductProcess -FilePath $launcher -Arguments "--stop"
    $stopResult = Wait-ProductExit -Process $stop
    $stopPayload = $stopResult.stdout | ConvertFrom-Json
    Assert-True ($stopResult.exit_code -eq 0) "stop command failed: $($stopResult.stderr)"
    Assert-True ($stopPayload.status -eq "stopped") "stop command did not report stopped"
    Assert-True $first.WaitForExit(15000) "graceful stop did not release the launcher"
    Assert-ProcessGone -ProcessId $serverPid
    Assert-True (-not (Test-Path -LiteralPath $runtimeDescriptor)) "runtime descriptor was not cleared"

    $orphan = New-ProductProcess -FilePath $launcher
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

    New-Item -ItemType Directory -Path (Split-Path -Parent $sentinel) -Force | Out-Null
    Set-Content -LiteralPath $sentinel -Value "preserve-me" -NoNewline
    $uninstaller = Join-Path $install "unins000.exe"
    Assert-True (Test-Path -LiteralPath $uninstaller) "uninstaller is missing: $uninstaller"
    $uninstallerProcess = Start-Process -FilePath $uninstaller -ArgumentList @(
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART"
    ) -Wait -PassThru
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
        uninstall_preserved_user_data = $true
        runtime_dependencies = "installed_onedir_executables_only"
    }
    $summary | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $smokeSummaryPath -Encoding UTF8
    $summary | ConvertTo-Json -Depth 4
}
finally {
    foreach ($process in @($first, $second, $stop, $orphan)) {
        if ($null -ne $process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
}
