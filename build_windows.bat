@echo off
REM Build "Test Tracker.exe" into dist\Test Tracker\ (needs Python 3 on this PC)
cd /d "%~dp0"
if not exist .venv python -m venv .venv
.venv\Scripts\pip install -q -r requirements.txt pyinstaller
.venv\Scripts\pyinstaller --noconfirm --clean --windowed --name "Test Tracker" test_tracker.py
echo Built dist\Test Tracker\Test Tracker.exe
