@echo off
setlocal
set "AutoDyRoot=%~dp0"
start "" wscript.exe "%AutoDyRoot%scripts\start-dashboard.vbs"
exit /b 0
