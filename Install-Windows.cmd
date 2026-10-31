@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
set install_status=%errorlevel%
if not "%WECHAT_READER_NONINTERACTIVE%"=="1" pause
exit /b %install_status%
