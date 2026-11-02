#!/usr/bin/env bash
# exit on error
set -o errexit

echo "==> Upgrading pip and installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt
pip install --no-deps "https://github.com/Kotak-Neo/Kotak-neo-api-v2/archive/refs/heads/main.zip"

echo "==> Building React frontend bundle..."
cd frontend
npm ci || npm install
npm run build
cd ..

echo "==> Ensuring runtime data and log directories exist..."
mkdir -p data logs

echo "==> Terminal ready for production!"
