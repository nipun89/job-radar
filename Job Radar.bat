@echo off
REM Double-click to start Job Radar. Your browser opens at http://localhost:5050
REM Close this window to stop it.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m radar serve
