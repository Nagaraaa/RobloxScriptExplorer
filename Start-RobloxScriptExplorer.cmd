@echo off
cd /d "%~dp0"
start "" pythonw "%~dp0explorer_gui.py" %*
