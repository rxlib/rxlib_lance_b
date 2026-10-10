param(
    [ValidateSet(3, 6)]
    [int]$KeepMonths = 6,
    [switch]$Apply
)

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppDir = Join-Path $Root 'app\current'
$ManifestPath = Join-Path $AppDir 'manifest.json'

if (-not (Test-Path $ManifestPath)) {
    Write-Host "未找到版本清单：$ManifestPath" -ForegroundColor Red
    exit 1
}
$manifest = Get-Content $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$runtimeName = $manifest.runtime
if (-not $runtimeName) { $runtimeName = 'python310' }
$Python = Join-Path $Root (Join-Path "runtime\$runtimeName" 'python.exe')
$Pylibs = Join-Path $Root "runtime\$runtimeName\pylibs"

if (-not (Test-Path $Python)) {
    Write-Host "未找到便携 Python：$Python" -ForegroundColor Red
    exit 1
}

$env:RXLIB_HOME = $Root
$env:PYTHONPATH = "$Pylibs;$AppDir"
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONIOENCODING = 'utf-8'

$argList = @((Join-Path $AppDir 'cache_main.py'), '--keep', $KeepMonths)
if ($Apply) { $argList += '--apply' }

& $Python @argList
