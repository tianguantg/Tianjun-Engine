$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$exampleDir = Join-Path $repoRoot "examples\cloudsimplus"

Push-Location $exampleDir
try {
    mvn -q -DskipTests compile
}
finally {
    Pop-Location
}
