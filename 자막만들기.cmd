@echo off
chcp 65001 > nul
cd /d "%~dp0"
if "%~1"=="" (
  echo.
  echo   사용법: 자막만들기.cmd "유튜브주소"
  echo   검수 후: 자막만들기.cmd "유튜브주소" --burn
  echo.
  pause
  exit /b 1
)
call "%~dp0py.cmd" run.py %*
echo.
pause