#!/bin/bash
# WSL 장중 생존 유지 스크립트
# setup_wakeup.ps1에서 호출됨 — 장 마감(15:35)까지 루프 돌며 cron 감시

LOG="/home/hyunho/openclaw-setup/python-only/logs/keepalive_$(date +%Y-%m-%d).log"
echo "[$(date '+%H:%M:%S')] keepalive 시작" >> "$LOG"

# cron 시작
if ! service cron status > /dev/null 2>&1; then
    service cron start
    echo "[$(date '+%H:%M:%S')] cron 재시작" >> "$LOG"
fi

# 15:35까지 1분마다 cron 생존 확인
while true; do
    H=$(date +%H); M=$(date +%M)
    # 15:35 이후 종료
    if [ "$H" -gt 15 ] || { [ "$H" -eq 15 ] && [ "$M" -ge 35 ]; }; then
        echo "[$(date '+%H:%M:%S')] 장 마감 — keepalive 종료" >> "$LOG"
        break
    fi
    # cron 죽으면 재시작
    if ! service cron status > /dev/null 2>&1; then
        service cron start
        echo "[$(date '+%H:%M:%S')] ⚠️ cron 재시작 (죽어있었음)" >> "$LOG"
    fi
    sleep 60
done
