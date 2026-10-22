Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "lib-local.ps1")
Initialize-LocalDirectories

$services = @(
    @{ Name = "link-booking-service"; Port = 8000; Url = "http://127.0.0.1:8000/health/live" },
    @{ Name = "load-planning-service"; Port = 8200; Url = "http://127.0.0.1:8200/health/live" },
    @{ Name = "platform-bff"; Port = 8080; Url = "http://127.0.0.1:8080/actuator/health" },
    @{ Name = "platform-web"; Port = 5173; Url = "http://127.0.0.1:5173/" },
    @{ Name = "link-carrier-simulator"; Port = 8100; Url = "http://127.0.0.1:8100/health/live" },
    @{ Name = "carrier-simulator-web"; Port = 5174; Url = "http://127.0.0.1:5174/" }
)

foreach ($service in $services) {
    $ready = Test-HttpReady -Url $service.Url
    $state = if ($ready) { "READY" } elseif (Test-LocalPort -Port $service.Port) { "PORT_ONLY" } else { "STOPPED" }
    "{0,-26} {1,-10} port {2}" -f $service.Name, $state, $service.Port
}

$workerPidPath = Get-PidPath -Name "link-rpa-worker"
if (Test-Path -LiteralPath $workerPidPath -PathType Leaf) {
    $workerPid = (Get-Content -LiteralPath $workerPidPath -Raw).Trim()
    if ($workerPid -match '^[0-9]+$') {
        $workerState = if (Get-Process -Id ([int]$workerPid) -ErrorAction SilentlyContinue) { "RUNNING" } else { "STALE_PID" }
        "{0,-26} {1,-10} PID {2}" -f "link-rpa-worker", $workerState, $workerPid
    }
    else {
        "{0,-26} {1,-10} PID 文件无效" -f "link-rpa-worker", "STALE_PID"
    }
}
else {
    "{0,-26} {1,-10}" -f "link-rpa-worker", "STOPPED"
}

Write-Host ""
Push-Location $script:RepositoryRoot
try {
    & docker compose --env-file $script:EnvironmentFile ps mysql nacos
}
finally {
    Pop-Location
}
