Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:ScriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:RepositoryRoot = (Resolve-Path (Join-Path $script:ScriptDirectory "..\..")).Path
$script:EnvironmentFile = Join-Path $script:RepositoryRoot ".env.local"
$script:RuntimeDirectory = Join-Path $script:RepositoryRoot "runtime"
$script:LogDirectory = Join-Path $script:RuntimeDirectory "logs"
$script:LogArchiveDirectory = Join-Path $script:LogDirectory "archive"
$script:PidDirectory = Join-Path $script:RuntimeDirectory "pids"

function Write-LocalInfo {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "[local] $Message"
}

function Write-LocalWarning {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Warning "[local] $Message"
}

function Initialize-LocalDirectories {
    foreach ($directory in @(
        $script:RuntimeDirectory,
        $script:LogDirectory,
        $script:LogArchiveDirectory,
        $script:PidDirectory
    )) {
        New-Item -ItemType Directory -Force -Path $directory | Out-Null
    }
}

function Assert-Command {
    param([Parameter(Mandatory = $true)][string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "缺少命令：$Name。请先安装并重新打开 PowerShell。"
    }
}

function Assert-Python312 {
    Assert-Command "python"
    $version = (& python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
    if ($version -ne "3.12") {
        throw "当前 python 是 $version；本项目需要 Python 3.12。请先执行 conda activate customs-affairs-os-lab。"
    }
}

function Assert-DockerReady {
    Assert-Command "docker"
    & docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Desktop 尚未启动或当前用户无法连接 Docker Engine。请先启动 Docker Desktop。"
    }
    & docker compose version *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "当前 Docker 未提供 Compose V2。请更新 Docker Desktop。"
    }
}

function Import-DotEnv {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "找不到 $Path。请先复制 .env.example 为 .env.local。"
    }

    foreach ($rawLine in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith("#")) {
            continue
        }
        $separator = $line.IndexOf("=")
        if ($separator -lt 1) {
            throw ".env.local 包含无法解析的行；应使用 KEY=VALUE 格式。"
        }
        $name = $line.Substring(0, $separator).Trim()
        $value = $line.Substring($separator + 1).Trim()
        if (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if ($name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
            throw ".env.local 包含无效变量名：$name"
        }
        Set-Item -Path "Env:$name" -Value $value
    }
}

function Assert-RequiredEnvironment {
    foreach ($name in @(
        "MYSQL_ROOT_PASSWORD",
        "MYSQL_DATABASE",
        "MYSQL_USER",
        "MYSQL_PASSWORD",
        "NACOS_AUTH_TOKEN"
    )) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if ([string]::IsNullOrWhiteSpace($value)) {
            throw ".env.local 缺少 $name。请参考 .env.example 补充。"
        }
    }
}

function Test-LocalPort {
    param([Parameter(Mandatory = $true)][int]$Port)
    $listeners = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
    return $null -ne ($listeners | Where-Object { $_.Port -eq $Port } | Select-Object -First 1)
}

function Test-HttpReady {
    param([Parameter(Mandatory = $true)][string]$Url)
    try {
        Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

function Wait-HttpReady {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$Label,
        [int]$TimeoutSeconds = 60
    )
    for ($attempt = 1; $attempt -le $TimeoutSeconds; $attempt++) {
        if (Test-HttpReady -Url $Url) {
            Write-LocalInfo "$Label 已就绪。"
            return
        }
        Start-Sleep -Seconds 1
    }
    throw "$Label 在 $TimeoutSeconds 秒内没有就绪。请查看 runtime\logs。"
}

function Rotate-LocalLog {
    param(
        [Parameter(Mandatory = $true)][string]$LogPath,
        [Parameter(Mandatory = $true)][string]$Scope,
        [int]$Keep = 10
    )
    if (-not (Test-Path -LiteralPath $LogPath -PathType Leaf)) {
        return
    }
    if ((Get-Item -LiteralPath $LogPath).Length -eq 0) {
        Remove-Item -LiteralPath $LogPath -Force
        return
    }

    $archiveDirectory = Join-Path $script:LogArchiveDirectory $Scope
    New-Item -ItemType Directory -Force -Path $archiveDirectory | Out-Null
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
    $archiveName = "{0}.{1}.log" -f ([IO.Path]::GetFileNameWithoutExtension($LogPath)), $timestamp
    Move-Item -LiteralPath $LogPath -Destination (Join-Path $archiveDirectory $archiveName)

    $archives = @(
        Get-ChildItem -LiteralPath $archiveDirectory -File |
            Sort-Object LastWriteTimeUtc -Descending
    )
    if ($archives.Count -gt $Keep) {
        $archives | Select-Object -Skip $Keep | Remove-Item -Force
    }
}

function Get-PidPath {
    param([Parameter(Mandatory = $true)][string]$Name)
    return Join-Path $script:PidDirectory "$Name.pid"
}

function Start-ManagedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [int]$Port = 0,
        [string]$HealthUrl = ""
    )

    if ($Port -gt 0 -and (Test-LocalPort -Port $Port)) {
        if (Test-HttpReady -Url $HealthUrl) {
            Write-LocalInfo "$Name 已在端口 $Port 运行，复用现有进程。"
            return
        }
        throw "端口 $Port 已被占用，但不是可用的 $Name。请先处理端口冲突。"
    }

    $stdoutPath = Join-Path $script:LogDirectory "$Name.log"
    $stderrPath = Join-Path $script:LogDirectory "$Name.error.log"
    Rotate-LocalLog -LogPath $stdoutPath -Scope $Name
    Rotate-LocalLog -LogPath $stderrPath -Scope "$Name-error"

    $process = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $ArgumentList `
        -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -PassThru `
        -WindowStyle Hidden

    Set-Content -LiteralPath (Get-PidPath -Name $Name) -Value $process.Id -Encoding ASCII
    Write-LocalInfo "已启动 $Name（PID $($process.Id)）。"
    if ($Port -gt 0) {
        Wait-HttpReady -Url $HealthUrl -Label $Name -TimeoutSeconds 120
    }
    else {
        Start-Sleep -Seconds 1
        $process.Refresh()
        if ($process.HasExited) {
            throw "$Name 启动后退出。请查看 runtime\logs。"
        }
    }
}

function Start-LocalInfrastructure {
    Assert-DockerReady
    $composeLog = Join-Path $script:LogDirectory "compose.log"
    Rotate-LocalLog -LogPath $composeLog -Scope "compose"
    Write-LocalInfo "启动或复用 Docker MySQL 与 Nacos。"
    Push-Location $script:RepositoryRoot
    try {
        & docker compose --env-file $script:EnvironmentFile up -d mysql nacos *>> $composeLog
        if ($LASTEXITCODE -ne 0) {
            throw "Docker Compose 启动失败。"
        }
    }
    finally {
        Pop-Location
    }
    Wait-HttpReady -Url "http://127.0.0.1:8848/nacos/actuator/health" -Label "Nacos" -TimeoutSeconds 120
}

function Initialize-LearningSchemas {
    Write-LocalInfo "确认四个本地学习 schema 已创建。"
    foreach ($schema in @(
        "platform_bff_lab",
        "link_booking_service",
        "load_planning_service",
        "link_carrier_simulator"
    )) {
        $sql = @"
CREATE DATABASE IF NOT EXISTS ``$schema`` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
GRANT ALL PRIVILEGES ON ``$schema``.* TO '$env:MYSQL_USER'@'%';
FLUSH PRIVILEGES;
"@
        Push-Location $script:RepositoryRoot
        try {
            & docker compose --env-file $script:EnvironmentFile exec -T `
                -e "MYSQL_PWD=$env:MYSQL_ROOT_PASSWORD" `
                mysql mysql -uroot -e $sql | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "创建 schema $schema 失败。"
            }
        }
        finally {
            Pop-Location
        }
    }
}

function Invoke-AlembicUpgrade {
    param(
        [Parameter(Mandatory = $true)][string]$ServiceDirectory,
        [Parameter(Mandatory = $true)][string]$Label
    )
    Write-LocalInfo "执行 $Label Alembic 迁移。"
    Push-Location $ServiceDirectory
    try {
        & python -m alembic upgrade head
        if ($LASTEXITCODE -ne 0) {
            throw "$Label Alembic 迁移失败。"
        }
    }
    finally {
        Pop-Location
    }
}

function Stop-ManagedLocalProcess {
    param([Parameter(Mandatory = $true)][string]$Name)
    $pidPath = Get-PidPath -Name $Name
    if (-not (Test-Path -LiteralPath $pidPath -PathType Leaf)) {
        return
    }
    $recordedPid = (Get-Content -LiteralPath $pidPath -Raw).Trim()
    if ($recordedPid -notmatch '^[0-9]+$') {
        Remove-Item -LiteralPath $pidPath -Force
        return
    }
    $process = Get-Process -Id ([int]$recordedPid) -ErrorAction SilentlyContinue
    if ($null -ne $process) {
        # Maven 和 npm 会产生子进程；/T 只终止这棵已记录 PID 的进程树。
        & taskkill.exe /PID $recordedPid /T /F | Out-Null
        Write-LocalInfo "已停止 $Name（PID $recordedPid）。"
    }
    Remove-Item -LiteralPath $pidPath -Force
}
