@echo off
rem Windows Smart App Control blocks the unsigned launcher that uv puts in
rem .venv\Scripts\python.exe. This wrapper runs the signed CPython that uv
rem installed and points it at the venv's packages, which has the same effect.
rem
rem If the path below is wrong on your machine, find yours with:
rem   uv python find 3.12
setlocal
set "PYBASE=C:\Users\user\AppData\Roaming\uv\python\cpython-3.12.14-windows-x86_64-none\python.exe"
if not exist "%PYBASE%" (
  echo.
  echo   Python not found at:
  echo   %PYBASE%
  echo.
  echo   Edit py.cmd and set PYBASE to the path from:  uv python find 3.12
  echo.
  exit /b 1
)
set "PYTHONPATH=%~dp0.venv\Lib\site-packages"
set "PATH=%~dp0.venv\Scripts;%PATH%"
"%PYBASE%" %*
