Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "lib-local.ps1")
Initialize-LocalDirectories

foreach ($name in @(
    "carrier-simulator-web",
    "link-carrier-simulator",
    "platform-web",
    "platform-bff",
    "link-rpa-worker",
    "load-planning-service",
    "link-booking-service"
)) {
    Stop-ManagedLocalProcess -Name $name
}

Write-LocalInfo "应用进程已停止。MySQL 与 Nacos 保留运行，便于下次快速启动。"
Write-LocalInfo "如需同时停止基础设施：docker compose --env-file .env.local stop mysql nacos"
