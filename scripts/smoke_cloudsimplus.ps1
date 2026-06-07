param(
    [string]$MavenPath = "mvn",
    [switch]$RunExample,
    [string]$Server = "http://127.0.0.1:8024",
    [string]$Scenario = "normal"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$exampleDir = Join-Path $repoRoot "examples\cloudsimplus"

Write-Host "Checking Java runtime..."
& java -version

Write-Host "Checking Maven runtime: $MavenPath"
& $MavenPath -version

Push-Location $exampleDir
try {
    & $MavenPath clean compile

    if ($RunExample) {
        try {
            $health = Invoke-RestMethod "$Server/health" -TimeoutSec 5
            if ($health.status -ne "ok") {
                throw "Unexpected /health status: $($health.status)"
            }
        }
        catch {
            throw "Tianjun HTTP server is not reachable at $Server. Start it before using -RunExample. Original error: $($_.Exception.Message)"
        }

        & $MavenPath exec:java "-Dexec.args=$Server $Scenario"
    }
}
finally {
    Pop-Location
}
