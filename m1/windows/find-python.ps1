# Read only: locate the interpreter belonging to an existing flower installation.
param([string]$SupportRoot)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
    if (-not $SupportRoot) {
        $flowerLocal = $env:LOCALAPPDATA
        if (-not $flowerLocal) {
            $flowerLocal = Join-Path ([Environment]::GetFolderPath('UserProfile')) 'AppData\Local'
        }
        $SupportRoot = Join-Path $flowerLocal 'RotatingFlower'
    }
    $flowerRoot = [IO.Path]::GetFullPath($SupportRoot)
    $flowerConfigPath = Join-Path $flowerRoot 'config.json'
    if (-not (Test-Path -LiteralPath $flowerConfigPath -PathType Leaf)) { exit 0 }
    $flowerConfig = Get-Content -LiteralPath $flowerConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($flowerConfig.schema -ne 1 -or $flowerConfig.runtime -isnot [string]) { exit 0 }
    if (-not [IO.Path]::IsPathRooted($flowerConfig.runtime)) { exit 0 }
    $flowerRuntime = [IO.Path]::GetFullPath($flowerConfig.runtime)
    $flowerExpectedParent = [IO.Path]::GetFullPath((Join-Path $flowerRoot 'runtimes'))
    if ([IO.Path]::GetDirectoryName($flowerRuntime) -ine $flowerExpectedParent) { exit 0 }
    $flowerPython = Join-Path $flowerRuntime 'venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $flowerPython -PathType Leaf) {
        [Console]::WriteLine($flowerPython)
    }
} catch {
    # Missing or invalid installations fall through to the normal Python launcher.
    exit 0
}
