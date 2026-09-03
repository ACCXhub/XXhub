@echo off
setlocal
set "AUTODY_HOME=%~dp0"
set "PLAYWRIGHT_BROWSERS_PATH=%~dp0data\ms-playwright"
set "PLAYWRIGHT_SKIP_BROWSER_GC=1"
set "AUTODY_INSTALL_PS1=%~dp0scripts\install.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "try { & $env:AUTODY_INSTALL_PS1; exit 0 } catch { [Console]::Error.WriteLine(($_ | Out-String)); exit 1 }"
set "ExitCode=%ERRORLEVEL%"
if not "%ExitCode%"=="0" (
  echo [ERROR] AutoDy installation failed. See the stage output above.
  pause
  exit /b %ExitCode%
)
echo [SUCCESS] AutoDy installation completed.
pause
exit /b 0
