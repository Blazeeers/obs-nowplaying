@echo off
chcp 65001 >nul
title Now Playing for OBS - install
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
