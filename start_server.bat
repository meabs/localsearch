@echo off
setlocal

set "REPO_DIR=%~dp0"
if "%REPO_DIR:~-1%"=="\" set "REPO_DIR=%REPO_DIR:~0,-1%"
cd /d "%REPO_DIR%"

set "PYTHONPATH=%REPO_DIR%\src"
set "PYTHON_EXE="

if exist "%REPO_DIR%\.venv\Scripts\python.exe" set "PYTHON_EXE=%REPO_DIR%\.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "C:\Users\meaburn\code\datagraph\.venv\Scripts\python.exe" set "PYTHON_EXE=C:\Users\meaburn\code\datagraph\.venv\Scripts\python.exe"
if not defined PYTHON_EXE (
  where /q py
  if not errorlevel 1 (
    set "PYTHON_EXE=py"
  ) else (
    set "PYTHON_EXE=python"
  )
)

"%PYTHON_EXE%" -m uvicorn operation_lens_v2.api.main:app --host 0.0.0.0 --port 8000
