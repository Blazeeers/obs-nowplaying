@echo off
chcp 65001 >nul
title Now Playing for OBS - uninstall
echo Removing autostart and stopping the server...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$p = Join-Path ([Environment]::GetFolderPath('Startup')) 'Now Playing (OBS).vbs'; if (Test-Path $p) { Remove-Item $p -Force; Write-Host 'autostart removed' } else { Write-Host 'autostart not found' }; Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' OR Name='python.exe'\" | Where-Object { $_.CommandLine -like '*np-server.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host ('stopped ' + $_.ProcessId) }"
echo.
echo Done. You can delete this folder.
pause
