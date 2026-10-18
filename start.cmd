@echo off
chcp 65001 >nul
title Now Playing - server
echo Starting Now Playing server (debug log below)...
echo.
python "%~dp0np-server.py" --debug
echo.
echo Server stopped.
pause
