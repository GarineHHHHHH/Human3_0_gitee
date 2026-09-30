@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Human 3.0 Server
echo Starting Human 3.0 console...
echo.
python server.py %*
echo.
echo Server stopped.
pause
