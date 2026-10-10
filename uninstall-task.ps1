# coding: utf-8 powershell
# 移除由 install-task.ps1 配置的 rxlib 登录自启项。
# 会删除启动文件夹里的静默启动器，并结束正在运行的平台进程。
param(
    [string]$ShortcutName = 'rxlib_wyckoff-platform'
)

$ErrorActionPreference = 'Stop'

$StartupDir = [Environment]::GetFolderPath('Startup')
$VbsPath = Join-Path $StartupDir ("{0}.vbs" -f $ShortcutName)

if (Test-Path $VbsPath) {
    Remove-Item -Path $VbsPath -Force
    Write-Host "已删除启动项：$VbsPath" -ForegroundColor Green
} else {
    Write-Host "未找到启动项（可能已经卸载）：$VbsPath" -ForegroundColor Yellow
}

# 结束由启动器运行的 pythonw 进程（按命令行含 launcher.py 匹配）
$targets = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like '*launcher.py*' }

foreach ($t in $targets) {
    Write-Host ("  结束进程 PID {0}" -f $t.ProcessId)
    Stop-Process -Id $t.ProcessId -Force -ErrorAction SilentlyContinue
}

if (-not $targets) { Write-Host '没有正在运行的平台后台进程。' -ForegroundColor DarkGray }
Write-Host '完成。' -ForegroundColor Green
