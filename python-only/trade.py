"""
trade.py — 호가 확인 → 수량 계산 → 지정가 주문을 한 번에 처리
사용법:
  python3 trade.py --code 078930 --name GS --action BUY --confidence 0.85 --reason "분석 근거"
  python3 trade.py --code 078930 --name GS --action SELL --confidence 0.80 --reason "분석 근거"
"""

import argparse, hmac, hashlib, httpx, json, uuid, os, sys, time
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

SERVER_URL    = os.getenv("TRADING_SERVER_URL", "")
SIGNAL_SECRET = os.getenv("SIGNAL_SECRET_KEY", "")
API_KEY       = SIGNAL_SECRET

if not SERVER_URL:
    sys.exit("❌ .env에 TRADING_SERVER_URL이 없습니다.")
if not SIGNAL_SECRET:
    sys.exit("❌ .env에 SIGNAL_SECRET_KEY가 없습니다.")

HEADERS = {"X-Api-Key": API_KEY, "Content-Type": "application/json"}


def request_json(method, path, *, content=None, headers=None):
    url = f"{SERVER_URL}{path}"
    last_err = None
    for attempt in range(3):
        try:
            resp = httpx.request(
                method,
                url,
                content=content,
                headers=headers or HEADERS,
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
            last_err = e
            if attempt < 2:
                time.sleep(2 + attempt * 3)
                continue
            break
    sys.exit(f"❌ API 연결 실패({method} {path}): {last_err}")


def api_get(path):
    return request_json("GET", path)


def make_signature(body: str) -> str:
    return hmac.new(SIGNAL_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()


def send_signal(code, name, action, confidence, reason, quantity, price):
    payload = {
        "signal_id":  str(uuid.uuid4()),
        "stock_code": code,
        "stock_name": name,
        "action":     action.upper(),
        "confidence": float(confidence),
        "reason":     reason,
        "order_type": "LIMIT",
        "quantity":   int(quantity),
        "target_price": int(price),
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    sig  = make_signature(body)
    return request_json(
        "POST",
        "/signal/receive",
        content=body,
        headers={"Content-Type": "application/json", "x-signal-signature": sig},
    )


def quote_name(quote):
    return (quote.get("hts_kor_isnm") or quote.get("stk_nm") or quote.get("kor_isnm")
            or quote.get("name") or "").strip()


def main():
    parser = argparse.ArgumentParser(description="호가 확인 후 지정가 주문")
    parser.add_argument("--code",       required=True,             help="종목코드")
    parser.add_argument("--name",       required=True,             help="종목명")
    parser.add_argument("--action",     required=True,             help="BUY / SELL")
    parser.add_argument("--confidence", required=True, type=float, help="신뢰도 0.0~1.0")
    parser.add_argument("--reason",     required=True,             help="매매 근거")
    parser.add_argument("--ratio",      default=0.45,  type=float, help="예수금 중 투입 비율 (기본 0.45)")
    args = parser.parse_args()

    if args.confidence < 0.7:
        sys.exit(f"❌ 신뢰도 {args.confidence} < 0.7 → HOLD")

    # 1. 예수금 조회
    account = api_get("/kiwoom/account")
    cash    = int(account.get("cash", 0))
    print(f"💰 예수금: {cash:,}원")

    # 2. 호가 조회
    quote      = api_get(f"/kiwoom/quote/{args.code}")
    sell_price = abs(int(quote.get("sel_fpr_bid", 0)))  # 매도1호가 = BUY 체결가 (+/-부호 제거)
    buy_price  = abs(int(quote.get("buy_fpr_bid", 0)))  # 매수1호가 = SELL 체결가 (+/-부호 제거)
    stock_name = quote_name(quote) or args.name
    print(f"📊 {stock_name}({args.code}) 매도1호가: {sell_price:,}원 | 매수1호가: {buy_price:,}원")

    if args.action.upper() == "BUY":
        if buy_price <= 0:
            sys.exit("❌ 호가 0 → 장외시간, 주문 취소")
        if buy_price > cash:
            sys.exit(f"❌ 1주 가격({buy_price:,}원) > 예수금({cash:,}원) → 주문 취소")
        ratio = min(args.ratio, 0.90)
        qty   = max(1, int(cash * ratio / buy_price))
        price = buy_price  # 매수1호가(bid)로 지정가 → 매도1호가보다 1틱 저렴
        print(f"📌 BUY 지정가: {price:,}원 × {qty}주 = {price*qty:,}원")

    else:  # SELL
        if buy_price <= 0:
            sys.exit("❌ 호가 0 → 장외시간, 주문 취소")
        holding_rows = {h["stock_code"]: h for h in account.get("holdings", [])}
        holdings = {code: h["quantity"] for code, h in holding_rows.items()}
        stock_name = holding_rows.get(args.code, {}).get("stock_name") or stock_name
        qty = holdings.get(args.code, 0)
        if qty <= 0:
            sys.exit(f"❌ {stock_name} 보유수량 없음 → 주문 취소")
        price = buy_price
        print(f"📌 SELL 지정가: {price:,}원 × {qty}주")

    # 3. 주문 전송
    result = send_signal(args.code, stock_name, args.action, args.confidence, args.reason, qty, price)
    status = "✅ 접수" if result.get("accepted") else "⚠️  거절"
    print(f"{status} | {result.get('message', result)}")


if __name__ == "__main__":
    main()
