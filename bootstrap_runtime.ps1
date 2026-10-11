﻿# bootstrap_runtime.ps1 — 首次运行自动下载便携 Python 运行时。
#
# 场景：用户从仓库/发行版拉到只有代码的项目，本地没有 runtime\python312。
# 本脚本检测缺失后，从 manifest.json 的 runtime_download_url 下载并解压，
# 校验 SHA-256，完成后本地复用，后续启动无需再下载。
#
# 参数： -Root 项目根目录（默认取脚本自身所在目录）
#
# 退出码： 0 = 就绪；1 = 失败（含未配置下载地址）

param(
    [string]$Root = (Split-Path -Parent $MyInvocation.MyCommand.Path)
)

$ErrorActionPreference = 'Stop'

# 联网下载所需（Windows PowerShell 5 默认不加载）
Add-Type -AssemblyName System.Net.Http -ErrorAction SilentlyContinue

$AppDir = Join-Path $Root 'app\current'
$ManifestPath = Join-Path $AppDir 'manifest.json'

if (-not (Test-Path $ManifestPath)) {
    Write-Host "未找到版本清单：$ManifestPath" -ForegroundColor Red
    exit 1
}

$manifest = Get-Content $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$RuntimeName = if ($manifest.runtime) { $manifest.runtime } else { 'python310' }

$PythonExe = Join-Path $Root (Join-Path "runtime\$RuntimeName" 'python.exe')
$PylibsDir = Join-Path $Root "runtime\$RuntimeName\pylibs"
$RuntimeParent = Join-Path $Root 'runtime'

# 已就绪，无需处理
if ((Test-Path $PythonExe) -and (Test-Path $PylibsDir)) {
    exit 0
}

# 读取下载配置
$DownloadUrl = $null
try { $DownloadUrl = $manifest.runtime_download_url } catch { }
$Sha256 = $null
try { $Sha256 = $manifest.runtime_sha256 } catch { }

if (-not $DownloadUrl) {
    Write-Host "未找到与本版本匹配的便携 Python：$PythonExe" -ForegroundColor Red
    Write-Host '且 manifest.json 未配置 runtime_download_url，无法自动下载。' -ForegroundColor Yellow
    Write-Host '请参照 build_runtime.ps1 打包运行时并上传（建议 gitee 发行版资产），' -ForegroundColor Yellow
    Write-Host '再在 app\current\manifest.json 填入 runtime_download_url 与 runtime_sha256。' -ForegroundColor Yellow
    exit 1
}

# 压缩包本地缓存目录（已存在 .part 时可断点续传）
$dlDir = Join-Path $Root 'runtime'
if (-not (Test-Path $dlDir)) { New-Item -ItemType Directory -Path $dlDir | Out-Null }
$Archive = Join-Path $dlDir ("{0}-win.zip" -f $RuntimeName)

Write-Host ('未检测到运行环境 {0}，需自动下载便携 Python（仅首次，约数百 MB）。' -f $RuntimeName) -ForegroundColor Cyan
Write-Host "  下载地址：$DownloadUrl" -ForegroundColor DarkGray

function Stop-DownloadFail {
    param([string]$Msg)
    Write-Host $Msg -ForegroundColor Red
    exit 1
}

# 流式下载（支持断点续传）：已存在的 .part 续传，下载完成后统一校验哈希
function Invoke-DownloadRuntime {
    param(
        [string]$Url,
        [string]$Dest,
        [int]$MaxRetries = 3
    )

    $handler = New-Object System.Net.Http.HttpClientHandler
    $client = New-Object System.Net.Http.HttpClient $handler
    $client.Timeout = [TimeSpan]::FromHours(4)

    for ($attempt = 1; $attempt -le $MaxRetries; $attempt++) {
        $partial = "$Dest.part"
        $existing = if (Test-Path $partial) { (Get-Item $partial).Length } else { 0L }

        try {
            $req = New-Object System.Net.Http.HttpRequestMessage('Get', $Url)
            if ($existing -gt 0) {
                $req.Headers.Range = New-Object System.Net.Http.Headers.RangeHeaderValue($existing, $null)
            }
            $resp = $client.SendAsync($req, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
        } catch {
            if ($attempt -lt $MaxRetries) {
                Write-Host ("  连接失败（$($_.Exception.Message)），重试 {0}/{1}..." -f $attempt, $MaxRetries) -ForegroundColor Yellow
                Start-Sleep -Seconds 3
                continue
            }
            Stop-DownloadFail ("下载连接失败：$($_.Exception.Message)")
        }

        if (-not $resp.IsSuccessStatusCode) {
            $resp.Dispose()
            if ($attempt -lt $MaxRetries) {
                Write-Host ("  HTTP $([int]$resp.StatusCode)，重试 {0}/{1}..." -f $attempt, $MaxRetries) -ForegroundColor Yellow
                Start-Sleep -Seconds 3
                continue
            }
            Stop-DownloadFail ("下载失败：HTTP $([int]$resp.StatusCode)  $Url")
        }

        # 计算总大小（Range 续传时从 Content-Range 取 total）
        $total = $null
        if ($resp.Content.Headers.ContentRange -and $resp.Content.Headers.ContentRange.Total) {
            $total = [long]$resp.Content.Headers.ContentRange.Total
        } elseif ($resp.Content.Headers.ContentLength) {
            $total = [long]$resp.Content.Headers.ContentLength
        }

        $stream = $resp.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        $fs = [System.IO.File]::Open($partial, [System.IO.FileMode]::Append, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        $stopwatch = New-Object System.Diagnostics.Stopwatch
        $stopwatch.Start()
        $reportInterval = 5000   # 毫秒
        $lastReport = 0
        try {
            $buffer = New-Object byte[] (1MB)
            while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                $fs.Write($buffer, 0, $read)
                if ($total -and $stopwatch.ElapsedMilliseconds - $lastReport -ge $reportInterval) {
                    $lastReport = $stopwatch.ElapsedMilliseconds
                    $pct = [math]::Round(($fs.Length / $total) * 100, 0)
                    Write-Host ("  已下载 {0:N0} / {1:N0} MB（{2}%）" -f ($fs.Length/1MB), ($total/1MB), $pct)
                }
            }
        } finally {
            $fs.Close(); $stream.Close(); $resp.Dispose()
        }
        $stopwatch.Stop()

        # 校验总大小（可选）
        if ($total) {
            $real = (Get-Item $partial).Length
            if ($real -ne $total) {
                Write-Host ("  下载不完整：{0:N0} / {1:N0} 字节，将重试..." -f $real, $total) -ForegroundColor Yellow
                continue
            }
        }

        # 校验 SHA-256
        if ($Sha256) {
            Write-Host '  正在校验文件完整性（SHA-256）...' -ForegroundColor DarkGray
            $actual = (Get-FileHash -Path $partial -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($actual -ne $Sha256) {
                Remove-Item $partial -Force -ErrorAction SilentlyContinue
                if ($attempt -lt $MaxRetries) {
                    Write-Host '  校验和不匹配，重新下载...' -ForegroundColor Yellow
                    continue
                }
                Stop-DownloadFail '校验和不匹配：下载可能被篡改或损坏，已删除缓存文件。'
            }
        } else {
            Write-Host '  警告：未配置 runtime_sha256，跳过完整性校验。请在正式分发前补上。' -ForegroundColor Yellow
        }

        Move-Item -Path $partial -Destination $Dest -Force
        return
    }
    Stop-DownloadFail '多次尝试后仍下载失败。'
}

Invoke-DownloadRuntime -Url $DownloadUrl -Dest $Archive

# 解压到 runtime\ 下（zip 顶层为 python312，解压后得到 runtime\python312\...）
Write-Host '  正在解压运行环境...' -ForegroundColor Cyan
try {
    Expand-Archive -Path $Archive -DestinationPath $RuntimeParent -Force
} catch {
    Write-Host ("解压失败：$($_.Exception.Message)") -ForegroundColor Red
    Write-Host ("可手动将 $Archive 解压到 $RuntimeParent 后重试。") -ForegroundColor Yellow
    exit 1
}

# 清理压缩包，释放空间
Remove-Item $Archive -Force -ErrorAction SilentlyContinue

if (-not (Test-Path $PythonExe)) {
    Write-Host "解压后仍未找到 $PythonExe" -ForegroundColor Red
    exit 1
}

Write-Host ("运行环境就绪：{0}" -f $PythonExe) -ForegroundColor Green
exit 0