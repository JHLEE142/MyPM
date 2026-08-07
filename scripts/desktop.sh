#!/bin/zsh
# PacePM을 데스크톱 앱 창(주소창 없는 단독 창)으로 연다.
# 서버가 꺼져 있으면 백엔드(8000)·프론트(3000)를 먼저 띄운 뒤 창을 연다.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if ! curl -s -o /dev/null --max-time 2 http://localhost:8000/health; then
  echo "백엔드 시작 중..."
  (cd "$ROOT/apps/api" && nohup .venv/bin/python -m uvicorn app.main:app --port 8000 >/tmp/pacepm-api.log 2>&1 &)
fi

if ! curl -s -o /dev/null --max-time 2 http://localhost:3000; then
  echo "프론트 시작 중..."
  (cd "$ROOT/apps/web" && nohup npm run dev >/tmp/pacepm-web.log 2>&1 &)
fi

for i in {1..60}; do
  curl -s -o /dev/null --max-time 2 http://localhost:3000 && break
  sleep 1
done

open -na "Google Chrome" --args --app=http://localhost:3000
