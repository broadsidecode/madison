@echo off
cd /d "%~dp0"
python -m timeline_reviewer launch
if errorlevel 1 pause
