"""
batch_analyze.py — 여러 종목 한 번에 quote+indicators 조회, 컴팩트 테이블 출력
사용법:
  python3 batch_analyze.py 005930 000660 078930 061250 ...
출력: 종목별 핵심 지표 한 줄 요약 (SKIP 포함)
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

HEADERS = {"X-Api-Key": API_KEY, "Content-Type": "application/json"}


def get(path):
    try:
        r = httpx.get(f"{SERVER_URL}{path}", headers=HEADERS, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"_error": str(e)}


def main():
    codes = sys.argv[1:]
    if not codes:
        sys.exit("사용법: python3 batch_analyze.py 코드1 코드2 ...")

    # 계좌 조회
    acct = get("/kiwoom/account")
    if "_error" in acct:
        print(f"⚠️  계좌 조회 실패: {acct['_error']}")
        cash = 0
        holdings = {}
    else:
        cash = int(acct.get("cash", 0))
        holdings = {h["stock_code"]: h for h in acct.get("holdings", [])}

    print(f"💰 예수금: {cash:,}원  보유종목: {list(holdings.keys()) or '없음'}")
    if holdings:
        for code, h in holdings.items():
            print(f"   보유 {h.get('stock_name','?')}({code}) {h.get('quantity',0)}주 | 수익률 {h.get('profit_rate',0):.1f}%")
    print()
    print(f"{'코드':>6}  {'종목명':<10}  {'가격':>8}  {'상태':>4}  {'RSI':>5}  {'변화율':>6}  {'거래량배':>5}  {'BB위치':>6}  {'호가비':>5}  {'MA':>3}")
    print("-" * 80)

    results = []
    for code in codes:
        quote = get(f"/kiwoom/quote/{code}")
        if "_error" in quote:
            print(f"{code:>6}  {'?':<10}  {'?':>8}  FAIL")
            continue

        name = quote.get("hts_kor_isnm", code)[:8]
        price = abs(int(quote.get("sel_fpr_bid", 0) or 0))
        tot_buy = int(quote.get("tot_buy_req", 0) or 0)
        tot_sel = int(quote.get("tot_sel_req", 1) or 1)
        bid_ratio = round(tot_buy / tot_sel, 2) if tot_sel > 0 else 0

        if price == 0:
            print(f"{code:>6}  {name:<10}  {'장외':>8}  SKIP")
            continue
        if price > cash:
            print(f"{code:>6}  {name:<10}  {price:>8,}  SKIP  (가격>{cash:,})")
            continue

        ind = get(f"/kiwoom/indicators/{code}")
        if "_error" in ind:
            print(f"{code:>6}  {name:<10}  {price:>8,}   ERR  지표조회실패")
            continue

        rsi    = ind.get("rsi_14", 0)
        chg    = ind.get("change_rate", 0)
        vol    = ind.get("volume_ratio", 0)
        close  = ind.get("close", price)
        bb_u   = ind.get("bb_upper", 0)
        bb_l   = ind.get("bb_lower", 0)
        ma5    = ind.get("ma_5", 0)
        ma20   = ind.get("ma_20", 0)
        buy_rt = ind.get("buy_rt", 0)

        if bb_l and bb_u:
            bb_pos = "하단↓" if close <= bb_l else ("상단↑" if close >= bb_u else "중간")
        else:
            bb_pos = "-"

        ma_sig = "골" if (ma5 and ma20 and ma5 < ma20 and close > ma5) else "-"

        print(f"{code:>6}  {name:<10}  {price:>8,}    OK  {rsi:>5.1f}  {chg:>+6.1f}%  {vol:>5.1f}x  {bb_pos:>6}  {bid_ratio:>5.2f}  {ma_sig:>3}")
        results.append({
            "code": code, "name": name, "price": price,
            "rsi": rsi, "change_rate": chg, "volume_ratio": vol,
            "close": close, "bb_upper": bb_u, "bb_lower": bb_l,
            "ma_5": ma5, "ma_20": ma20, "bid_ratio": bid_ratio,
            "buy_rt": buy_rt,
            "profit_rate": holdings.get(code, {}).get("profit_rate"),
            "holding": code in holdings,
        })

    print()
    print(f"✅ 분석완료: {len(results)}개 종목 (SKIP 제외)")


if __name__ == "__main__":
    main()
