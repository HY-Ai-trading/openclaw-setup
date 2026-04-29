#!/usr/bin/env bash
# 트레이딩 서버 API 래퍼 — X-Api-Key 인증 자동 처리
# 사용법: bash api.sh <endpoint> [curl 추가 옵션...]
# 예시:
#   bash api.sh /kiwoom/account
#   bash api.sh /kiwoom/quote/005930
#   bash api.sh /kiwoom/orders/filled
#   bash api.sh /kiwoom/sync-orders -X POST
#   bash api.sh /dashboard/summary

ENV_FILE="$(dirname "$0")/.env"
source "$ENV_FILE"

BASE="${TRADING_SERVER_URL}"
if [ -z "$BASE" ]; then
  echo "❌ .env에 TRADING_SERVER_URL이 없습니다." >&2
  exit 1
fi
ENDPOINT="$1"
shift

curl -s \
  -H "X-Api-Key: $SIGNAL_SECRET_KEY" \
  -H "Content-Type: application/json" \
  "${@}" \
  "${BASE}${ENDPOINT}"
