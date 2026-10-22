[CmdletBinding()]
param(
    [switch]$SandboxUKeyPresent,
    [switch]$Headless,
    [switch]$Once
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "lib-local.ps1")
Assert-Python312

$isWindowsHost = $env:OS -eq "Windows_NT"
if (-not $isWindowsHost) {
    throw "单一窗口 Worker 应在 Windows 交互式桌面运行。Mac/Linux 只能用于 Sandbox 单元测试。"
}

$arguments = @("-m", "app.rpa.browser_worker", "--domain", "SINGLE_WINDOW")
if (-not $Headless) { $arguments += "--headed" }
if ($SandboxUKeyPresent) { $arguments += "--ukey-present" }
if ($Once) { $arguments += "--once" }

Write-LocalInfo "当前只连接本地 Sandbox，不会读取真实 UKey 或访问真实单一窗口。"
if (-not $SandboxUKeyPresent) {
    Write-LocalInfo "未声明 Sandbox UKey 就绪；领取的任务会进入 WAITING_HUMAN。"
}

Push-Location (Join-Path $script:RepositoryRoot "services\load-planning-service")
try {
    & python @arguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
