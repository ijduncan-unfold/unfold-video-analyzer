@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0portable\Start-VideoAnalyzer.ps1"
if errorlevel 1 pause
