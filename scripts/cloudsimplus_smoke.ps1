param(
    [string]$MavenPath = "mvn",
    [switch]$RunExample,
    [string]$Server = "http://127.0.0.1:8024",
    [string]$Scenario = "normal"
)

$ErrorActionPreference = "Stop"

$script = Join-Path $PSScriptRoot "smoke_cloudsimplus.ps1"
& $script -MavenPath $MavenPath -RunExample:$RunExample -Server $Server -Scenario $Scenario
