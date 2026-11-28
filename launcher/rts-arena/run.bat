@echo off
rem Run RTS-Arena directly with Python (no exe build needed). Admin rights are requested automatically.
cd /d "%~dp0"
where pythonw >nul 2>&1 && (start "" pythonw arena.py) || (start "" python arena.py)
