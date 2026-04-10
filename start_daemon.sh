#!/bin/bash
# 미래학원 문제제작기 — 백그라운드 자동 시작 스크립트
# launchd가 이 스크립트를 실행함 (터미널 불필요)

LOGFILE=/tmp/miraehakwon.log
TUNNEL_URL_FILE=/tmp/miraehakwon_tunnel_url.txt
PYTHON=/opt/homebrew/bin/python3.12
WORKDIR=/Users/miyoo1016/jason_lab

# 이전 URL 파일 삭제
rm -f "$TUNNEL_URL_FILE"

echo "=== 미래학원 서버 시작 $(date) ===" >> "$LOGFILE"

# 1) Flask 웹서버 백그라운드 실행
cd "$WORKDIR"
"$PYTHON" web_ui.py >> "$LOGFILE" 2>&1 &
WEB_PID=$!
echo "Flask PID: $WEB_PID" >> "$LOGFILE"
sleep 3

# 2) cloudflared 터널 실행 + URL 캡처
/opt/homebrew/bin/cloudflared tunnel --url http://localhost:7777 2>&1 | while IFS= read -r line; do
  echo "$line" >> "$LOGFILE"
  if echo "$line" | grep -q "trycloudflare.com"; then
    URL=$(echo "$line" | grep -oE "https://[a-zA-Z0-9-]+\.trycloudflare\.com")
    if [ -n "$URL" ]; then
      echo "$URL" > "$TUNNEL_URL_FILE"
      echo "터널 URL 저장됨: $URL" >> "$LOGFILE"
    fi
  fi
done

wait $WEB_PID
