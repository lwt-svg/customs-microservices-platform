[CmdletBinding()]
param(
    [ValidateSet("core", "full")]
    [string]$Mode = "core",

    [ValidateSet("configured", "rules", "codex", "openai")]
    [string]$InsightMode = "configured"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "lib-local.ps1")

Initialize-LocalDirectories
Assert-Python312
foreach ($command in @("docker", "java", "mvn", "node", "npm")) {
    Assert-Command $command
}
Import-DotEnv -Path $script:EnvironmentFile
Assert-RequiredEnvironment
if ([string]::IsNullOrWhiteSpace($env:RPA_SANDBOX_AUTO_LINK_WORKER_ENABLED)) {
    $env:RPA_SANDBOX_AUTO_LINK_WORKER_ENABLED = "true"
}
if ([string]::IsNullOrWhiteSpace($env:RPA_WORKER_POLL_SECONDS)) {
    $env:RPA_WORKER_POLL_SECONDS = "2"
}

switch ($InsightMode) {
    "rules" {
        $env:LOAD_PLANNING_PLAN_INSIGHTS_ENABLED = "true"
        $env:LOAD_PLANNING_PLAN_INSIGHTS_PROVIDER = "rule_based"
    }
    "codex" {
        $env:LOAD_PLANNING_PLAN_INSIGHTS_ENABLED = "true"
        $env:LOAD_PLANNING_PLAN_INSIGHTS_PROVIDER = "codex_cli"
    }
    "openai" {
        $env:LOAD_PLANNING_PLAN_INSIGHTS_ENABLED = "true"
        $env:LOAD_PLANNING_PLAN_INSIGHTS_PROVIDER = "openai_responses"
    }
}

if ($Mode -eq "full") {
    $env:CARRIER_DISPATCH_ENABLED = "true"
    $env:CARRIER_DISPATCH_RETRY_ENABLED = "true"
}
else {
    $env:CARRIER_DISPATCH_ENABLED = "false"
    $env:CARRIER_DISPATCH_RETRY_ENABLED = "false"
}

Start-LocalInfrastructure
Initialize-LearningSchemas

$linkDirectory = Join-Path $script:RepositoryRoot "services\link-booking-service"
$loadDirectory = Join-Path $script:RepositoryRoot "services\load-planning-service"
$bffDirectory = Join-Path $script:RepositoryRoot "platform-bff"
$webDirectory = Join-Path $script:RepositoryRoot "platform-web"

Invoke-AlembicUpgrade -ServiceDirectory $linkDirectory -Label "LINK Python"
Invoke-AlembicUpgrade -ServiceDirectory $loadDirectory -Label "智能装载 Python"

Start-ManagedProcess `
    -Name "link-booking-service" `
    -FilePath "python" `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") `
    -WorkingDirectory $linkDirectory `
    -Port 8000 `
    -HealthUrl "http://127.0.0.1:8000/health/live"

Start-ManagedProcess `
    -Name "load-planning-service" `
    -FilePath "python" `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8200") `
    -WorkingDirectory $loadDirectory `
    -Port 8200 `
    -HealthUrl "http://127.0.0.1:8200/health/live"

if ($env:RPA_SANDBOX_AUTO_LINK_WORKER_ENABLED -match '^(?i:true|1|yes|on)$') {
    Start-ManagedProcess `
        -Name "link-rpa-worker" `
        -FilePath "python" `
        -ArgumentList @(
            "-m", "app.rpa.browser_worker",
            "--domain", "LINK",
            "--poll-seconds", $env:RPA_WORKER_POLL_SECONDS
        ) `
        -WorkingDirectory $loadDirectory
}

$mavenCommand = (Get-Command "mvn.cmd" -ErrorAction SilentlyContinue)
if ($null -eq $mavenCommand) {
    $mavenCommand = Get-Command "mvn"
}
Start-ManagedProcess `
    -Name "platform-bff" `
    -FilePath $mavenCommand.Source `
    -ArgumentList @("spring-boot:run") `
    -WorkingDirectory $bffDirectory `
    -Port 8080 `
    -HealthUrl "http://127.0.0.1:8080/actuator/health"

$npmCommand = (Get-Command "npm.cmd" -ErrorAction SilentlyContinue)
if ($null -eq $npmCommand) {
    $npmCommand = Get-Command "npm"
}
Start-ManagedProcess `
    -Name "platform-web" `
    -FilePath $npmCommand.Source `
    -ArgumentList @("run", "dev", "--", "--host", "127.0.0.1", "--port", "5173", "--strictPort") `
    -WorkingDirectory $webDirectory `
    -Port 5173 `
    -HealthUrl "http://127.0.0.1:5173/"

if ($Mode -eq "full") {
    $carrierDirectory = Join-Path $script:RepositoryRoot "services\link-carrier-simulator"
    $carrierWebDirectory = Join-Path $script:RepositoryRoot "carrier-simulator-web"
    Invoke-AlembicUpgrade -ServiceDirectory $carrierDirectory -Label "模拟承运人"
    Start-ManagedProcess `
        -Name "link-carrier-simulator" `
        -FilePath "python" `
        -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8100") `
        -WorkingDirectory $carrierDirectory `
        -Port 8100 `
        -HealthUrl "http://127.0.0.1:8100/health/live"
    Start-ManagedProcess `
        -Name "carrier-simulator-web" `
        -FilePath $npmCommand.Source `
        -ArgumentList @("run", "dev", "--", "--host", "127.0.0.1", "--port", "5174", "--strictPort") `
        -WorkingDirectory $carrierWebDirectory `
        -Port 5174 `
        -HealthUrl "http://127.0.0.1:5174/"
}

Write-Host ""
Write-LocalInfo "Windows 本地环境已启动。"
Write-LocalInfo "平台：http://127.0.0.1:5173"
Write-LocalInfo "Nacos：http://127.0.0.1:8848/nacos"
Write-LocalInfo "状态：.\scripts\windows\status-local.ps1"
Write-LocalInfo "停止：.\scripts\windows\stop-local.ps1"
