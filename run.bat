@echo off
cd /d "%~dp0"
set "V=%LOCALAPPDATA%\Tingxie\venv"
if not exist "%V%\Scripts\pythonw.exe" (
  python -m venv "%V%"
  "%V%\Scripts\python" -m pip install -r requirements.txt
)
start "" "%V%\Scripts\pythonw.exe" app.py
