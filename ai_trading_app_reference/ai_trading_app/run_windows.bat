@echo off
setlocal
if not exist .venv (
  py -m venv .venv
  if errorlevel 1 python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if not exist .env (
  copy .env.example .env
  echo Created .env. Edit it and add OPENAI_API_KEY, then run this file again.
  pause
  exit /b 1
)
python main.py %*
pause
