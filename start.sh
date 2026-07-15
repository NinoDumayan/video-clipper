#!/usr/bin/env bash
set -e

/app/venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000 &
UVICORN_PID=$!

./node_modules/.bin/next start --port "$PORT" &
NEXT_PID=$!

trap "kill $UVICORN_PID $NEXT_PID 2>/dev/null" EXIT SIGINT SIGTERM

wait -n
