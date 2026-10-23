#!/usr/bin/env bash
set -e
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "Created .env. Edit it and add OPENAI_API_KEY, then run this script again."
  exit 1
fi
python main.py "$@"
