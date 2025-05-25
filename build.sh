#!/usr/bin/env bash
set -o errexit

echo "🔧 Installing pipenv and dependencies..."
pip install pipenv
export PIPENV_VENV_IN_PROJECT=1
pipenv install --deploy --ignore-pipfile

echo "📦 Running collectstatic..."
pipenv run python manage.py collectstatic --no-input

echo "🛠 Running migrations..."
pipenv run python manage.py migrate
