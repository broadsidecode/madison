@echo off
cd /d "%~dp0"
python -m timeline_reviewer demo --open
if errorlevel 1 pause
