﻿param([int]$Port = 8504)

function Test-LocalPort([int]$TargetPort) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect('127.0.0.1', $TargetPort, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(1000)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppDir = Join-Path $Root 'app\current'
$ManifestPath = Join-Path $AppDir 'manifest.json'
$Entry = Join-Path $AppDir 'factor_lab_ui.py'
$HomeUrl = "http://localhost:$Port"

if (Test-LocalPort $Port) {
    Start-Process $HomeUrl
    exit 0
}

# app/current 是 NTFS Junction（git 不跟踪），克隆后该目录缺失。
# 缺失时自动定位 app/releases/ 下最新版本，复制为真实目录，做到"克隆即用"。
if (-not (Test-Path $ManifestPath)) {
    $ReleasesDir = Join-Path $Root 'app\releases'
    Write-Host "未找到 app/current（Junction 不随仓库克隆）。正在从 app/releases 自动补齐..." -ForegroundColor Yellow

    if (-not (Test-Path $ReleasesDir)) {
        Write-Host "未找到版本目录：$ReleasesDir" -ForegroundColor Red
        Read-Host '按回车退出'
        exit 1
    }

    $latest = Get-ChildItem -Path $ReleasesDir -Directory | Sort-Object { try { [version]$_.Name } catch { [version]'0.0' } } -Descending | Select-Object -First 1
    if (-not $latest) {
        Write-Host "app/releases 下没有可用版本目录。" -ForegroundColor Red
        Read-Host '按回车退出'
        exit 1
    }
    if (-not (Test-Path (Join-Path $latest.FullName 'manifest.json'))) {
        Write-Host "最新版本目录缺少 manifest.json：$($latest.Name)" -ForegroundColor Red
        Read-Host '按回车退出'
        exit 1
    }

    Copy-Item -Path $latest.FullName -Destination $AppDir -Recurse -Force
    Write-Host "已从 $($latest.Name) 补齐 app/current。" -ForegroundColor Green
}

if (-not (Test-Path $ManifestPath)) {
    Write-Host "未找到版本清单：$ManifestPath" -ForegroundColor Red
    Read-Host '按回车退出'
    exit 1
}

$manifest = Get-Content $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$runtimeName = $manifest.runtime
if (-not $runtimeName) { $runtimeName = 'python310' }

$Python = Join-Path $Root (Join-Path "runtime\$runtimeName" 'python.exe')
$Pylibs = Join-Path $Root "runtime\$runtimeName\pylibs"

# 首次运行若缺少便携运行时，自动下载（需 manifest.json 已配置下载地址）
if (-not ((Test-Path $Python) -and (Test-Path $Pylibs))) {
    & (Join-Path $Root 'bootstrap_runtime.ps1') -Root $Root
    if ($LASTEXITCODE -ne 0) {
        Read-Host '按回车退出'
        exit 1
    }
}

# 下载后仍缺失（理论上不会到达，兜底提示）
if (-not (Test-Path $Python)) {
    Write-Host "未找到与本版本匹配的便携 Python：$Python" -ForegroundColor Red
    Read-Host '按回车退出'
    exit 1
}
if (-not (Test-Path $Pylibs)) {
    Write-Host "未找到隔离依赖目录：$Pylibs" -ForegroundColor Red
    Read-Host '按回车退出'
    exit 1
}
if (-not (Test-Path $Entry)) {
    Write-Host "未找到平台入口：$Entry" -ForegroundColor Red
    Read-Host '按回车退出'
    exit 1
}

$env:RXLIB_HOME = $Root
$env:PYTHONPATH = "$Pylibs;$AppDir"
$env:PYTHONNOUSERSITE = '1'
$env:PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION = 'python'
$env:NO_PROXY = 'localhost,127.0.0.1'
$env:no_proxy = 'localhost,127.0.0.1'

Write-Host '============================================'
Write-Host ' rxlib 量化交易研究平台'
Write-Host ' 正在启动服务，请稍候...'
Write-Host '============================================'

# 手动临时档：服务在独立窗口运行，窗口里直接显示 Streamlit 启动信息与
# 访问地址（证明正在运行，不会像死机）；关闭该窗口即停止服务。
# 仅错误输出落盘，便于崩溃排查。需要无窗口/开机自启请用 install-task.ps1。
$LogsDir = Join-Path $Root 'logs'
if (-not (Test-Path $LogsDir)) { New-Item -ItemType Directory -Path $LogsDir | Out-Null }
$Stamp = Get-Date -Format 'yyyyMMdd'
$ErrLog = Join-Path $LogsDir ("run-{0}.err.log" -f $Stamp)

$argList = @(
    '-m', 'streamlit', 'run', $Entry,
    "--server.port=$Port",
    '--server.address=0.0.0.0',
    '--server.headless=true',
    '--global.developmentMode=false',
    '--server.fileWatcherType=none'
)
$proc = Start-Process -FilePath $Python -ArgumentList $argList `
    -WorkingDirectory $AppDir -PassThru -RedirectStandardError $ErrLog

$ready = $false
for ($i = 1; $i -le 60; $i++) {
    Start-Sleep -Seconds 1
    if (Test-LocalPort $Port) {
        $ready = $true
        break
    }
    # 进程已提前退出：多半是启动即崩溃，直接给出日志位置
    if ($proc.HasExited) {
        Write-Host "服务进程异常退出（退出码 $($proc.ExitCode)）。" -ForegroundColor Red
        Write-Host "请查看错误日志：$ErrLog" -ForegroundColor Yellow
        Read-Host '按回车退出'
        exit 1
    }
    if (($i % 5) -eq 0) { Write-Host "正在等待服务就绪... ($i 秒)" }
}

if (-not $ready) {
    Write-Host "服务在限定时间内未就绪，可稍后手动访问：$HomeUrl" -ForegroundColor Yellow
    Write-Host "错误日志：$ErrLog" -ForegroundColor Yellow
    Read-Host '按回车退出'
    exit 1
}

Write-Host '平台已经启动，正在打开浏览器...' -ForegroundColor Green
Write-Host "如运行异常，错误日志：$ErrLog" -ForegroundColor DarkGray
Start-Process $HomeUrl
Start-Sleep -Seconds 1
exit 0