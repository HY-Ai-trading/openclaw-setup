#!/usr/bin/env bash
set -e

# .env 로드
ENV_FILE="$(dirname "$0")/.env"
if [ -f "$ENV_FILE" ]; then
  export $(grep -v '^#' "$ENV_FILE" | xargs)
fi

SERVER_URL="${TRADING_SERVER_URL}"
if [ -z "$SERVER_URL" ]; then
  echo "❌ .env에 TRADING_SERVER_URL이 없습니다."
  exit 1
fi
SECRET="${SIGNAL_SECRET_KEY}"

if [ -z "$SECRET" ]; then
  echo "❌ .env에 SIGNAL_SECRET_KEY가 없습니다."
  exit 1
fi

# 주문 페이로드 (삼성전자 BUY 1주, 시장가)
SIGNAL_ID=$(python3 -c "import uuid; print(uuid.uuid4())")
BODY=$(printf '{"signal_id":"%s","stock_code":"005930","stock_name":"삼성전자","action":"BUY","confidence":0.75,"reason":"오픈클로 테스트 주문","order_type":"MARKET"}' "$SIGNAL_ID")

# HMAC-SHA256 서명
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | awk '{print $2}')

echo "→ 서버: $SERVER_URL"
echo "→ 페이로드: $BODY"
echo "→ 서명: $SIG"
echo ""

# 요청 (signal/receive는 HMAC 서명 방식 — X-Api-Key 불필요)
RESPONSE=$(curl -s -w "\n%{http_code}" \
  -X POST "$SERVER_URL/signal/receive" \
  -H "Content-Type: application/json" \
  -H "x-signal-signature: $SIG" \
  --data-raw "$BODY")

HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
BODY_RESP=$(echo "$RESPONSE" | head -n-1)

echo "← HTTP $HTTP_CODE"
echo "← 응답: $BODY_RESP"

if echo "$BODY_RESP" | grep -q '"accepted":true'; then
  echo ""
  echo "✅ 주문 접수됨"
else
  echo ""
  echo "⚠️  주문 거절 또는 오류"
fi
