[CmdletBinding()]
param(
    [switch]$Headed,
    [switch]$Once
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "lib-local.ps1")
Assert-Python312

$arguments = @("-m", "app.rpa.browser_worker", "--domain", "LINK")
if ($Headed) { $arguments += "--headed" }
if ($Once) { $arguments += "--once" }

Push-Location (Join-Path $script:RepositoryRoot "services\load-planning-service")
try {
    & python @arguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
