"""
query.py — 트레이딩 서버 GET/POST API 조회 (인증 자동 처리)
사용법:
  python3 query.py /kiwoom/account
  python3 query.py /kiwoom/quote/005930
  python3 query.py /kiwoom/orders/filled
  python3 query.py /kiwoom/orders/unfilled
  python3 query.py /kiwoom/sync-orders POST
  python3 query.py /dashboard/summary
  python3 query.py "/kiwoom/ranking?mrkt_tp=001&sort_tp=1"
"""

import sys, os, json, httpx
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

SERVER_URL = os.getenv("TRADING_SERVER_URL", "")
API_KEY    = os.getenv("SIGNAL_SECRET_KEY", "")

if not SERVER_URL:
    sys.exit("❌ .env에 TRADING_SERVER_URL이 없습니다.")
if not API_KEY:
    sys.exit("❌ .env에 SIGNAL_SECRET_KEY가 없습니다.")

if len(sys.argv) < 2:
    sys.exit("사용법: python3 query.py <엔드포인트> [POST]")

endpoint = sys.argv[1]
method   = sys.argv[2].upper() if len(sys.argv) > 2 else "GET"
headers  = {"X-Api-Key": API_KEY, "Content-Type": "application/json"}

try:
    if method == "POST":
        resp = httpx.post(f"{SERVER_URL}{endpoint}", headers=headers, timeout=10)
    else:
        resp = httpx.get(f"{SERVER_URL}{endpoint}", headers=headers, timeout=10)

    if resp.status_code == 401:
        sys.exit("❌ 인증 실패 (401)")

    data = resp.json()
    print(json.dumps(data, ensure_ascii=False, indent=2))

except httpx.ConnectError:
    sys.exit(f"❌ 서버 연결 실패 — {SERVER_URL} 응답 없음")
except Exception as e:
    sys.exit(f"❌ 오류: {e}")
