@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  .venv\Scripts\python.exe server.py
) else (
  echo Virtual environment not found. Run setup commands in README.md first.
  pause
)
