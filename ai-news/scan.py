"""
scan.py (ai-news) — 시장 데이터 수집 + 지표 계산만 출력
판단은 AI가 뉴스 검색 후 결정
"""
import sys, os, httpx
sys.stdout.reconfigure(line_buffering=True)
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

SERVER_URL = os.getenv("TRADING_SERVER_URL", "")
API_KEY    = os.getenv("SIGNAL_SECRET_KEY", "")
DART_KEY   = os.getenv("DART_API_KEY", "")

if not SERVER_URL: sys.exit("❌ TRADING_SERVER_URL 없음")
if not API_KEY:    sys.exit("❌ SIGNAL_SECRET_KEY 없음")

HEADERS = {"X-Api-Key": API_KEY}
_client = httpx.Client(headers=HEADERS, timeout=10)

WATCHLIST = ["005930","000660","035420","051910","006400",
             "078930","061250","217820","001510","056080","084850"]

EXCLUDE = ["KODEX","TIGER","KINDEX","RISE","ACE","PLUS","KoAct","HANARO",
           "레버리지","인버스","ETN","ETF","리츠","채권","부동산","인프라",
           "선물","액티브","나스닥","S&P","다우","미국","중국","일본","글로벌"]

CORP_MAP = {
    "005930":"00126380","000660":"00164779","035420":"00266961",
    "051910":"00401731","006400":"00164488","078930":"00108670",
    "061250":"00648826","217820":"00877422","001510":"00112774",
    "056080":"00631518","084850":"00741612",
}
BAD_KW  = ["불성실공시","영업정지","과징금","횡령","손실","부도","감사의견","검찰","수사","파산","회생"]
GOOD_KW = ["수주","계약체결","실적개선","자사주취득","영업이익","매출증가","흑자"]


def get(path):
    try:
        r = _client.get(f"{SERVER_URL}{path}")
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"_err": str(e)}

def fetch_stock(code):
    return code, get(f"/kiwoom/quote/{code}"), get(f"/kiwoom/indicators/{code}")

def get_dart(code):
    if not DART_KEY:
        return "없음", ""
    try:
        bgn = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        params = {"crtfc_key": DART_KEY, "bgn_de": bgn, "sort": "date", "page_count": 20}
        corp = CORP_MAP.get(code)
        if corp:
            params["corp_code"] = corp
        r = httpx.get("https://opendart.fss.or.kr/api/list.json", params=params, timeout=8)
        items = r.json().get("list", [])
        if not corp:
            items = [i for i in items if code in i.get("stock_code", "")]
        if not items:
            return "없음", ""
        titles = [i.get("report_nm", "") for i in items[:5]]
        summary = " / ".join(titles[:3])
        if any(kw in t for t in titles for kw in BAD_KW):
            return "악재", summary
        if any(kw in t for t in titles for kw in GOOD_KW):
            return "호재", summary
        return "없음", summary
    except:
        return "없음", ""


def main():
    with ThreadPoolExecutor(max_workers=30) as ex:
        f_acct  = ex.submit(get, "/kiwoom/account")
        f_k200  = ex.submit(get, "/kiwoom/indicators/069500")
        f_rank1 = ex.submit(get, "/kiwoom/ranking?mrkt_tp=001&sort_tp=1")
        f_rank2 = ex.submit(get, "/kiwoom/ranking?mrkt_tp=001&sort_tp=2")
        wl_fut  = {ex.submit(fetch_stock, c): c for c in WATCHLIST}
        acct = f_acct.result(); k200 = f_k200.result()
        rank1 = f_rank1.result(); rank2 = f_rank2.result()
        wl_res = {}
        for fut in as_completed(wl_fut):
            code, q, i = fut.result(); wl_res[code] = (q, i)

    cash     = int(acct.get("cash", 0)) if "_err" not in acct else 0
    holdings = {}
    if "_err" not in acct:
        for h in acct.get("holdings", []):
            holdings[h["stock_code"]] = h

    k_rsi      = float(k200.get("rsi_14") or 50)    if "_err" not in k200 else 50
    k_chg      = float(k200.get("change_rate") or 0) if "_err" not in k200 else 0
    overheated = k_chg <= -2.0 or k_rsi >= 80
    buy_min    = 3 if overheated else 2

    now_str = datetime.now().strftime("%H:%M")
    print(f"[{now_str}] 예수금 {cash:,}원 | KODEX200 RSI{k_rsi:.0f}/{k_chg:+.1f}% {'과열' if overheated else '정상'} | 매수기준 {buy_min}개↑")
    for code, h in holdings.items():
        print(f"보유: {h.get('stock_name','?')}({code}) {h.get('quantity',0)}주 수익률{h.get('profit_rate',0):+.1f}%")

    # 랭킹 후보
    rank_map = {}
    for data in [rank1, rank2]:
        items = data if isinstance(data, list) else data.get("output", data.get("items", []))
        if not isinstance(items, list): continue
        for item in items[:20]:
            name  = item.get("hts_kor_isnm", "")
            code  = item.get("mksc_shrn_iscd", "").strip()
            price = abs(int(item.get("cur_prc", 0) or 0))
            if not code or not name or any(kw in name for kw in EXCLUDE): continue
            if price == 0 or price > cash: continue
            if code in rank_map: rank_map[code]["both"] = True
            else: rank_map[code] = {"name": name, "price": price,
                                    "sig": str(item.get("pred_pre_sig","")), "both": False}

    top_codes = [c for c, _ in sorted(rank_map.items(),
                 key=lambda x: -(2*x[1]["both"]+(x[1]["sig"]=="5")))[:10]]
    all_codes = list(dict.fromkeys(WATCHLIST + top_codes))

    extra = [c for c in all_codes if c not in wl_res]
    if extra:
        with ThreadPoolExecutor(max_workers=len(extra)) as ex:
            futs = {ex.submit(fetch_stock, c): c for c in extra}
            for fut in as_completed(futs):
                code, q, i = fut.result(); wl_res[code] = (q, i)

    print(f"\n{'코드':<8}{'종목명':<8}{'가격':>8}  {'RSI':>5} {'변화율':>7} {'거래량':>6} {'BB':>6} {'호가비':>5} {'수익률':>6}  B/S")
    print("─" * 76)

    analysis = {}
    for code in all_codes:
        q, ind = wl_res.get(code, ({}, {}))
        if "_err" in q: continue
        name  = (q.get("hts_kor_isnm") or q.get("stk_nm")
                 or holdings.get(code, {}).get("stock_name") or code)[:7]
        price = abs(int(q.get("sel_fpr_bid") or 0))
        t_buy = int(q.get("tot_buy_req") or 0)
        t_sel = int(q.get("tot_sel_req") or 1)
        bid_r = round(t_buy / max(t_sel, 1), 2)
        if price == 0: continue
        if price > cash and code not in holdings: continue
        if "_err" in ind: continue

        rsi   = float(ind.get("rsi_14")       or 0)
        chg   = float(ind.get("change_rate")  or 0)
        vol   = float(ind.get("volume_ratio") or 0)
        close = float(ind.get("close")        or price)
        bb_u  = float(ind.get("bb_upper")     or 0)
        bb_l  = float(ind.get("bb_lower")     or 0)
        ma5   = float(ind.get("ma_5")         or 0)
        ma20  = float(ind.get("ma_20")        or 0)
        brt   = float(ind.get("buy_rt")       or 0)
        pr    = holdings.get(code, {}).get("profit_rate")

        bb_pos = "하단" if bb_l and close <= bb_l else ("상단" if bb_u and close >= bb_u else "중간")
        ma_sig = "골" if (ma5 and ma20 and ma5 < ma20 and close > ma5) else "-"
        pr_str = f"{pr:+.1f}%" if pr is not None else "-"

        buy_conds = []
        if rsi  and rsi  <= 50:  buy_conds.append(f"RSI{rsi:.0f}")
        if vol  and vol  >= 1.3: buy_conds.append(f"거래량{vol:.1f}x")
        if chg  and chg  <= -1.0:buy_conds.append(f"하락{chg:.1f}%")
        if bb_l and close <= bb_l: buy_conds.append("BB하단")
        if ma5 and ma20 and ma5 < ma20 and close > ma5: buy_conds.append("MA골")
        if bid_r and bid_r >= 1.3: buy_conds.append(f"호가{bid_r:.1f}")
        if brt  and brt  >= 150: buy_conds.append(f"buy_rt{brt:.0f}")

        sell_conds = []
        if pr is not None:
            if pr >= 5:  sell_conds.append(f"수익+{pr:.1f}%")
            if pr <= -7: sell_conds.append(f"손절{pr:.1f}%")
        if rsi  and rsi  >= 78:  sell_conds.append(f"RSI{rsi:.0f}")
        if chg  and chg  >= 4.0: sell_conds.append(f"급등+{chg:.1f}%")
        if bb_u and close >= bb_u: sell_conds.append("BB상단")
        if bid_r and bid_r < 0.5: sell_conds.append(f"호가{bid_r:.2f}")

        b = len(buy_conds)
        s = len(sell_conds) if code in holdings else 0
        print(f"{code:<8}{name:<8}{price:>8,}  {rsi:>5.1f} {chg:>+7.1f}% {vol:>6.1f}x {bb_pos:>6} {bid_r:>5.2f} {pr_str:>6}  {b}/{s}")
        analysis[code] = dict(name=name, price=price, pr=pr, rsi=rsi, chg=chg,
                               vol=vol, bid_r=bid_r, bb_pos=bb_pos, ma_sig=ma_sig,
                               brt=brt, buy_conds=buy_conds, sell_conds=sell_conds)

    # 신호 상세
    print("\n[매도신호] (보유종목, 기준 3개↑)")
    for code, d in analysis.items():
        if code not in holdings or not d["sell_conds"]: continue
        pr_str = f"{d['pr']:+.1f}%" if d["pr"] is not None else ""
        print(f"  {d['name']}({code}) {pr_str} → {', '.join(d['sell_conds'])} ({len(d['sell_conds'])}개)")

    print(f"\n[매수신호] (기준 {buy_min}개↑)")
    news_targets = []
    for code, d in analysis.items():
        if not d["buy_conds"]: continue
        mark = "★" if len(d["buy_conds"]) >= buy_min else " "
        print(f" {mark} {d['name']}({code}) → {', '.join(d['buy_conds'])} ({len(d['buy_conds'])}개)")
        if len(d["buy_conds"]) >= max(1, buy_min - 1):
            news_targets.append((code, d["name"]))

    # DART
    dart_codes = list({c for c, d in analysis.items()
                       if len(d["buy_conds"]) >= 1 or c in holdings})
    if dart_codes:
        print("\n[DART 공시]")
        dart_results = {}
        with ThreadPoolExecutor(max_workers=max(1, len(dart_codes))) as ex:
            futs = {ex.submit(get_dart, c): c for c in dart_codes}
            for fut in as_completed(futs):
                c = futs[fut]; dart_results[c] = fut.result()
        for c in dart_codes:
            sig, summary = dart_results.get(c, ("없음", ""))
            label = {"악재": "⚠️악재", "호재": "✅호재", "없음": "없음"}[sig]
            print(f"  {analysis[c]['name']}({c}): {label}"
                  + (f" — {summary[:40]}" if summary else ""))

    # AI 뉴스 검색 대상 명시
    if news_targets:
        print("\n[AI 뉴스검색 대상] ← 아래 종목 최신 뉴스 확인 후 매매 판단")
        for code, name in news_targets:
            print(f"  {name}({code}): https://search.naver.com/search.naver?query={name}+주식+뉴스")

    print("\n=== SCAN_COMPLETE ===")


if __name__ == "__main__":
    main()
