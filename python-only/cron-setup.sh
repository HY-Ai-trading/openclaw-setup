#!/usr/bin/env bash
# Linux crontab 등록 스크립트
# 사용법: bash cron-setup.sh

DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$(which python3)"

# 기존 트레이딩 잡 제거 후 재등록
crontab -l 2>/dev/null | grep -v "openclaw-setup/python-only" | grep -v "트레이딩 자동매매" > /tmp/cron_tmp

cat >> /tmp/cron_tmp <<EOF
# 트레이딩 자동매매
50 8  * * 1-5 cd "$DIR" && $PYTHON -u scan.py >> "$DIR/logs/\$(date +\%Y-\%m-\%d).log" 2>&1
*/2 9-15 * * 1-5 cd "$DIR" && $PYTHON check_time.py | grep -q SKIP || $PYTHON -u scan.py >> "$DIR/logs/\$(date +\%Y-\%m-\%d).log" 2>&1
EOF

crontab /tmp/cron_tmp
rm /tmp/cron_tmp
mkdir -p "$DIR/logs"

echo "✅ cron 등록 완료"
crontab -l | grep trading -A1
