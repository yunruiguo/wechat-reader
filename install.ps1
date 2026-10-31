$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONIOENCODING = 'utf-8'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$root = if ($env:WECHAT_READER_HOME) { $env:WECHAT_READER_HOME } else { Join-Path $HOME '.wechat-reader' }
New-Item -ItemType Directory -Force -Path $root | Out-Null
$log = Join-Path $root 'install.log'
$env:UV_UNMANAGED_INSTALL = Join-Path $root 'tools'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $root 'python'
if (-not $env:UV_CACHE_DIR) { $env:UV_CACHE_DIR = Join-Path $root 'cache' }
$uv = Join-Path $root 'tools\uv.exe'
$python = Join-Path $root 'venv\Scripts\python.exe'

function Invoke-Checked {
    param([string]$File, [string[]]$Arguments, [switch]$Visible)
    # Windows PowerShell 5.1 treats native stderr as an error record. Check the
    # process exit code instead, so normal uv progress output is not a failure.
    $ErrorActionPreference = 'Continue'
    if ($Visible) { & $File @Arguments } else { & $File @Arguments *>> $log }
    if ($LASTEXITCODE -ne 0) { throw "安装步骤失败（退出码 $LASTEXITCODE）。" }
}

try {
    Write-Host "`nwechat-reader 一键安装`n首次安装需要联网下载运行环境，请稍候。`n"
    Write-Host '准备安装工具…'
    if (-not (Test-Path $uv)) {
        $installer = Join-Path $root 'uv-installer.ps1'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://astral.sh/uv/install.ps1' -OutFile $installer -TimeoutSec 180
        $ErrorActionPreference = 'Continue'
        & $installer *>> $log
        $ErrorActionPreference = 'Stop'
        if (-not (Test-Path $uv)) { throw '安装工具下载或初始化失败。' }
    }
    Write-Host '准备独立 Python 环境…'
    if (-not (Test-Path $python)) {
        Invoke-Checked -File $uv -Arguments @('venv', '--python', '3.11', (Join-Path $root 'venv'))
    }
    Write-Host '安装 wechat-reader…'
    Invoke-Checked -File $uv -Arguments @('pip', 'install', '--python', $python, '--upgrade', $PSScriptRoot)
    Invoke-Checked -File $python -Arguments @('-m', 'wechat_reader_setup') -Visible
    exit 0
} catch {
    Write-Host "`n[安装未完成] $($_.Exception.Message)`n请检查网络后重新运行。详细记录：$log"
    if (Test-Path $log) { Get-Content $log -Tail 12 }
    exit 1
}
