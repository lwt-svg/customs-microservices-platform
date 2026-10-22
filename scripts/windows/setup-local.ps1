[CmdletBinding()]
param(
    [switch]$SkipDockerPull,
    [switch]$SkipBrowserInstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "lib-local.ps1")

Initialize-LocalDirectories
Assert-Python312
foreach ($command in @("docker", "java", "mvn", "node", "npm")) {
    Assert-Command $command
}
Assert-DockerReady

if (-not (Test-Path -LiteralPath $script:EnvironmentFile -PathType Leaf)) {
    Copy-Item `
        -LiteralPath (Join-Path $script:RepositoryRoot ".env.example") `
        -Destination $script:EnvironmentFile
    Write-LocalInfo "已创建 .env.local；请按本机情况检查其中的学习环境密码。"
}
else {
    Write-LocalInfo ".env.local 已存在，保持原文件不变。"
}

Write-LocalInfo "安装三个 Python 服务、测试工具和 RPA 依赖。"
Push-Location $script:RepositoryRoot
try {
    & python -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        throw "升级 pip 失败。"
    }
    & python -m pip install -r requirements-local.txt
    if ($LASTEXITCODE -ne 0) {
        throw "安装 Python 依赖失败。"
    }
    if (-not $SkipBrowserInstall) {
        & python -m playwright install chromium
        if ($LASTEXITCODE -ne 0) {
            throw "安装 Playwright Chromium 失败。"
        }
    }
}
finally {
    Pop-Location
}

foreach ($frontend in @("platform-web", "carrier-simulator-web")) {
    Write-LocalInfo "安装 $frontend 前端依赖。"
    Push-Location (Join-Path $script:RepositoryRoot $frontend)
    try {
        & npm ci
        if ($LASTEXITCODE -ne 0) {
            throw "$frontend npm ci 失败。"
        }
    }
    finally {
        Pop-Location
    }
}

Write-LocalInfo "预下载 Java Maven 依赖。"
Push-Location (Join-Path $script:RepositoryRoot "platform-bff")
try {
    & mvn "-q" "-DskipTests" "dependency:go-offline"
    if ($LASTEXITCODE -ne 0) {
        throw "Maven 依赖下载失败。"
    }
}
finally {
    Pop-Location
}

if (-not $SkipDockerPull) {
    Write-LocalInfo "拉取 MySQL 与 Nacos 镜像。"
    Push-Location $script:RepositoryRoot
    try {
        & docker compose --env-file $script:EnvironmentFile pull mysql nacos
        if ($LASTEXITCODE -ne 0) {
            throw "Docker 镜像拉取失败。请检查 Docker Desktop 与网络。"
        }
    }
    finally {
        Pop-Location
    }
}

Write-Host ""
Write-LocalInfo "Windows 首次安装完成。"
Write-LocalInfo "日常启动：.\scripts\windows\run-local.ps1 core"
