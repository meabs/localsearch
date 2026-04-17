@echo off
cd /d "C:\Users\meaburn\code\datagraph"
set PYTHONPATH=src
.venv\Scripts\python.exe -m uvicorn operation_lens_v2.api.main:app --host 0.0.0.0 --port 8000
