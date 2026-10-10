# build_runtime.ps1 — 打包便携 Python 运行时为可分发的 zip，并输出校验和。
#
# 用途：
#   1. 把 runtime\python312 打成一个自包含 zip（内部顶层目录为 python312）；
#   2. 计算该 zip 的 SHA-256 校验和；
#   3. 打印需手工填充进 app\current\manifest.json 的字段 JSON 片段。
#
# 之后需要做的（面向 gitee Releases 分发）：
#   1. 把生成的 dist\<RuntimeName>-win.zip 上传为 gitee「发行版」的资产；
#   2. 获取该资产的直接下载链接，填入 manifest.json 的 runtime_download_url；
#   3. 把脚本打印的 sha256 填入 manifest.json 的 runtime_sha256。
#
# 用法： powershell -NoProfile -ExecutionPolicy Bypass -File build_runtime.ps1
param(
    [string]$Root = (Split-Path -Parent $MyInvocation.MyCommand.Path),
    [string]$RuntimeName = 'python312'
)

$ErrorActionPreference = 'Stop'

$SourceDir = Join-Path $Root "runtime\$RuntimeName"
if (-not (Test-Path $SourceDir)) {
    Write-Host "未找到运行环境目录：$SourceDir" -ForegroundColor Red
    exit 1
}

$DistDir = Join-Path $Root 'dist'
if (-not (Test-Path $DistDir)) { New-Item -ItemType Directory -Path $DistDir | Out-Null }

$Archive = Join-Path $DistDir ("{0}-win.zip" -f $RuntimeName)

Write-Host ("打包中：{0} → {1}" -f $SourceDir, $Archive) -ForegroundColor Cyan
if (Test-Path $Archive) { Remove-Item $Archive -Force }

# 以文件夹方式打包，使 zip 顶层目录为 python312，
# 用户端解压到 runtime\ 下即可得到 runtime\python312\python.exe。
Compress-Archive -Path $SourceDir -DestinationPath $Archive -CompressionLevel Optimal

$Hash = (Get-FileHash -Path $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
$SizeMB = [math]::Round((Get-Item $Archive).Length / 1MB, 1)

Write-Host ''
Write-Host "打包完成：$Archive  ($SizeMB MB)" -ForegroundColor Green
Write-Host ("SHA-256：{0}" -f $Hash) -ForegroundColor Green
Write-Host ''

Write-Host '请把下面这段 JSON 填进 app\current\manifest.json（替换同名字段）：' -ForegroundColor Yellow
Write-Host '------------------------------------------------------------'
Write-Host ('"runtime_download_url": "<把 ' + $RuntimeName + '-win.zip 上传到 gitee 发行版后，填它的下载直链>",')
Write-Host ('"runtime_sha256": "' + $Hash + '",')
Write-Host '------------------------------------------------------------'
Write-Host ''
Write-Host '提示：gitee 发行版资产直链形如' -ForegroundColor DarkGray
Write-Host ('  https://gitee.com/<owner>/<repo>/releases/download/<tag>/' + $RuntimeName + '-win.zip')