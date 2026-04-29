"""
send_signal.py
오픈클로가 분석 후 직접 실행하는 신호 전송 스크립트

사용 예시:
  python send_signal.py --code 005930 --name 삼성전자 --action BUY --confidence 0.85 --reason "RSI 과매도 + 거래량 급증"
  python send_signal.py --code 005930 --name 삼성전자 --action BUY --confidence 0.85 --reason "급등" --order_type MARKET
"""

import argparse, hmac, hashlib, httpx, json, uuid, os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

SERVER_URL    = os.getenv("TRADING_SERVER_URL", "")
SIGNAL_SECRET = os.getenv("SIGNAL_SECRET_KEY", "")

if not SERVER_URL:
    raise SystemExit("❌ .env에 TRADING_SERVER_URL이 없습니다.")
if not SIGNAL_SECRET:
    raise SystemExit("❌ .env에 SIGNAL_SECRET_KEY가 없습니다.")

def make_signature(body: str) -> str:
    return hmac.new(SIGNAL_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()

def send(code, name, action, confidence, reason, quantity=None, price=None, order_type="LIMIT"):
    payload = {
        "signal_id":    str(uuid.uuid4()),
        "stock_code":   code,
        "stock_name":   name,
        "action":       action.upper(),
        "confidence":   float(confidence),
        "reason":       reason,
        "order_type":   order_type.upper(),   # LIMIT 또는 MARKET
    }
    if quantity:
        payload["quantity"] = int(quantity)
    if price and order_type.upper() == "LIMIT":
        payload["target_price"] = int(price)

    body = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    sig  = make_signature(body)

    try:
        resp = httpx.post(
            f"{SERVER_URL}/signal/receive",
            content=body,
            headers={
                "Content-Type":       "application/json",
                "x-signal-signature": sig,
            },
            timeout=10,
        )
        result = resp.json()
        status = "✅ 접수" if result.get("accepted") else "⚠️  거절"
        print(f"{status} [{code}] {name} {action} | {result.get('message', '')}")
        return result
    except Exception as e:
        print(f"❌ 전송 실패: {e}")
        return {"accepted": False, "error": str(e)}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="오픈클로 → 트레이딩 서버 신호 전송")
    parser.add_argument("--code",       required=True,              help="종목코드 (6자리)")
    parser.add_argument("--name",       required=True,              help="종목명")
    parser.add_argument("--action",     required=True,              help="BUY / SELL / HOLD")
    parser.add_argument("--confidence", required=True,  type=float, help="신뢰도 (0.0~1.0)")
    parser.add_argument("--reason",     required=True,              help="분석 이유")
    parser.add_argument("--quantity",   default=None,   type=int,   help="수량 (선택, 기본: 1)")
    parser.add_argument("--price",      default=None,   type=int,   help="지정가 (선택, 없으면 현재 호가)")
    parser.add_argument("--order_type", default="LIMIT",            help="LIMIT(기본) 또는 MARKET(시장가 즉시체결)")
    args = parser.parse_args()

    send(
        code       = args.code,
        name       = args.name,
        action     = args.action,
        confidence = args.confidence,
        reason     = args.reason,
        quantity   = args.quantity,
        price      = args.price,
        order_type = args.order_type,
    )
