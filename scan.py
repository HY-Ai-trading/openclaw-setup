"""
scan.py — 전체 분석 + 권고 exec 명령 자동 생성
에이전트는 출력된 exec 명령만 그대로 실행하면 됨
"""
import sys, os, httpx, subprocess
sys.stdout.reconfigure(line_buffering=True)
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))


def _send_discord(msg):
    token   = os.getenv("DISCORD_BOT_TOKEN", "")
    user_id = os.getenv("DISCORD_USER_ID", "")
    if not token:
        return
    try:
        h = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
        dm = httpx.post("https://discord.com/api/v10/users/@me/channels",
                        headers=h, json={"recipient_id": user_id}, timeout=8)
        ch = dm.json()["id"]
        httpx.post(f"https://discord.com/api/v10/channels/{ch}/messages",
                   headers=h, json={"content": msg}, timeout=8)
        print("✅ Discord 전송 완료")
    except Exception as e:
        print(f"⚠️ Discord 전송 실패: {e}")

SERVER_URL = os.getenv("TRADING_SERVER_URL", "")
API_KEY    = os.getenv("SIGNAL_SECRET_KEY", "")
DART_KEY   = os.getenv("DART_API_KEY", "")
TRADE_PY   = "/home/hyunho/openclaw-setup/trade.py"

if not SERVER_URL:
    sys.exit("❌ TRADING_SERVER_URL 없음")
if not API_KEY:
    sys.exit("❌ SIGNAL_SECRET_KEY 없음")

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
    q = get(f"/kiwoom/quote/{code}")
    i = get(f"/kiwoom/indicators/{code}")
    return code, q, i


def get_dart(code):
    """returns ('호재'|'악재'|'없음', summary_str)"""
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
    # ── 기초 + 워치리스트 전부 동시 조회 ──────────────────────
    with ThreadPoolExecutor(max_workers=30) as ex:
        f_acct  = ex.submit(get, "/kiwoom/account")
        f_k200  = ex.submit(get, "/kiwoom/indicators/069500")
        f_rank1 = ex.submit(get, "/kiwoom/ranking?mrkt_tp=001&sort_tp=1")
        f_rank2 = ex.submit(get, "/kiwoom/ranking?mrkt_tp=001&sort_tp=2")
        wl_fut  = {ex.submit(fetch_stock, c): c for c in WATCHLIST}

        acct  = f_acct.result()
        k200  = f_k200.result()
        rank1 = f_rank1.result()
        rank2 = f_rank2.result()
        wl_res = {}
        for fut in as_completed(wl_fut):
            code, q, i = fut.result()
            wl_res[code] = (q, i)

    # ── 계좌 ──────────────────────────────────────────────────
    cash     = int(acct.get("cash", 0)) if "_err" not in acct else 0
    holdings = {}
    if "_err" not in acct:
        for h in acct.get("holdings", []):
            holdings[h["stock_code"]] = h

    # ── KODEX200 ──────────────────────────────────────────────
    k_rsi  = float(k200.get("rsi_14") or 50)    if "_err" not in k200 else 50
    k_chg  = float(k200.get("change_rate") or 0) if "_err" not in k200 else 0
    overheated = k_chg <= -2.0 or k_rsi >= 80
    buy_min    = 3 if overheated else 2

    print(f"💰 예수금: {cash:,}원   KODEX200 RSI {k_rsi:.0f}/{k_chg:+.1f}% → 매수기준 {buy_min}개↑{'  ⚠️과열' if overheated else ''}")
    for code, h in holdings.items():
        pr = h.get("profit_rate", 0)
        print(f"📦 보유 {h.get('stock_name','?')}({code}) {h.get('quantity',0)}주 | {pr:+.1f}%")

    # ── 랭킹 후보 ─────────────────────────────────────────────
    rank_map = {}
    for data in [rank1, rank2]:
        items = data if isinstance(data, list) else data.get("output", data.get("items", []))
        if not isinstance(items, list):
            continue
        for item in items[:20]:
            name  = item.get("hts_kor_isnm", "")
            code  = item.get("mksc_shrn_iscd", "").strip()
            price = abs(int(item.get("cur_prc", 0) or 0))
            if not code or not name or any(kw in name for kw in EXCLUDE):
                continue
            if price == 0 or price > cash:
                continue
            if code in rank_map:
                rank_map[code]["both"] = True
            else:
                rank_map[code] = {"name": name, "price": price,
                                  "sig": str(item.get("pred_pre_sig", "")),
                                  "both": False}

    top_codes = [c for c, _ in
                 sorted(rank_map.items(),
                        key=lambda x: -(2*x[1]["both"] + (x[1]["sig"]=="5")))[:10]]
    all_codes = list(dict.fromkeys(WATCHLIST + top_codes))

    # ── 랭킹 후보 추가 조회 ───────────────────────────────────
    extra = [c for c in all_codes if c not in wl_res]
    if extra:
        with ThreadPoolExecutor(max_workers=len(extra)) as ex:
            futs = {ex.submit(fetch_stock, c): c for c in extra}
            for fut in as_completed(futs):
                code, q, i = fut.result()
                wl_res[code] = (q, i)

    # ── 종목별 분석 ───────────────────────────────────────────
    print(f"\n{'코드':>6}  {'종목명':<10} {'가격':>8}  {'RSI':>5} {'변화율':>7} {'거래량':>6} {'BB':>6} {'호가비':>5} {'MA':>3}  BUY/SELL")
    print("─" * 92)

    analysis = {}  # code -> dict

    for code in all_codes:
        q, ind = wl_res.get(code, ({}, {}))

        if "_err" in q:
            print(f"{code:>6}  {'?':<10} 조회실패")
            continue

        name  = (q.get("hts_kor_isnm") or q.get("stk_nm")
                 or holdings.get(code, {}).get("stock_name") or code)[:8]
        price = abs(int(q.get("sel_fpr_bid") or 0))
        t_buy = int(q.get("tot_buy_req") or 0)
        t_sel = int(q.get("tot_sel_req") or 1)
        bid_r = round(t_buy / max(t_sel, 1), 2)

        if price == 0:
            print(f"{code:>6}  {name:<10} {'장외':>8}  SKIP")
            continue
        if price > cash and code not in holdings:
            print(f"{code:>6}  {name:<10} {price:>8,}  SKIP  예수금초과")
            continue

        if "_err" in ind:
            print(f"{code:>6}  {name:<10} {price:>8,}  지표실패")
            continue

        rsi   = float(ind.get("rsi_14")      or 0)
        chg   = float(ind.get("change_rate") or 0)
        vol   = float(ind.get("volume_ratio")or 0)
        close = float(ind.get("close")       or price)
        bb_u  = float(ind.get("bb_upper")    or 0)
        bb_l  = float(ind.get("bb_lower")    or 0)
        ma5   = float(ind.get("ma_5")        or 0)
        ma20  = float(ind.get("ma_20")       or 0)
        brt   = float(ind.get("buy_rt")      or 0)
        pr    = holdings.get(code, {}).get("profit_rate")

        bb_pos = ("하단↓" if bb_l and close <= bb_l
                  else "상단↑" if bb_u and close >= bb_u else "중간")
        ma_sig = "골" if (ma5 and ma20 and ma5 < ma20 and close > ma5) else "-"

        # BUY 조건 카운트
        buy_conds = []
        if rsi   and rsi   <= 50:  buy_conds.append(f"RSI{rsi:.0f}")
        if vol   and vol   >= 1.3: buy_conds.append(f"거래량{vol:.1f}x")
        if chg   and chg   <= -1.0:buy_conds.append(f"하락{chg:.1f}%")
        if bb_l  and close <= bb_l:buy_conds.append("BB하단")
        if ma5 and ma20 and ma5 < ma20 and close > ma5: buy_conds.append("MA골")
        if bid_r and bid_r >= 1.3: buy_conds.append(f"호가{bid_r:.1f}")
        if brt   and brt   >= 150: buy_conds.append(f"buy_rt{brt:.0f}")

        # SELL 조건 카운트 (보유 종목만)
        sell_conds = []
        if pr is not None:
            if pr  >= 5:   sell_conds.append(f"수익+{pr:.1f}%")
            if pr  <= -7:  sell_conds.append(f"손절{pr:.1f}%")
        if rsi  and rsi  >= 78:  sell_conds.append(f"RSI{rsi:.0f}")
        if chg  and chg  >= 4.0: sell_conds.append(f"급등+{chg:.1f}%")
        if bb_u and close >= bb_u:sell_conds.append("BB상단")
        if bid_r and bid_r < 0.5: sell_conds.append(f"호가{bid_r:.1f}")

        buy_str  = f"BUY{len(buy_conds)}" if buy_conds  else "-"
        sell_str = f"SELL{len(sell_conds)}" if sell_conds else "-"

        print(f"{code:>6}  {name:<10} {price:>8,}  {rsi:>5.1f} {chg:>+7.1f}% {vol:>6.1f}x {bb_pos:>6} {bid_r:>5.2f} {ma_sig:>3}  {buy_str}/{sell_str}")

        analysis[code] = {
            "name": name, "price": price,
            "buy_conds": buy_conds, "sell_conds": sell_conds,
            "pr": pr, "rsi": rsi, "chg": chg, "vol": vol,
        }

    # ── 권고사항 생성 ─────────────────────────────────────────
    print("\n" + "═"*50)
    print("권고사항")
    print("═"*50)

    actions = []

    # SELL — 수익/손실 무관, 시장 신호로만 판단
    for code, d in analysis.items():
        if code not in holdings:
            continue
        pr = d.get("pr") or 0
        sell_conds = d["sell_conds"]
        name = d["name"]

        if len(sell_conds) >= 3:
            conf = 0.85 if len(sell_conds) >= 4 else 0.70
            reason = ", ".join(sell_conds)
            actions.append(("SELL", code, name, pr, d))
            label = "수익실현" if pr >= 0 else "손실축소"
            print(f"🟡 SELL({label}) {name}({code}) {pr:+.1f}% 신호[{reason}] 신뢰도{conf}")
            cmd = [sys.executable, TRADE_PY, "--code", code, "--name", name,
                   "--action", "SELL", "--confidence", str(conf), "--ratio", "1.0", "--reason", reason]
            print(f"   exec: {' '.join(cmd)}")
            r = subprocess.run(cmd, capture_output=True, text=True)
            print(r.stdout.strip())
            if r.returncode != 0:
                print(f"   ⚠️ SELL 오류: {r.stderr.strip()}")
        elif pr < -7:
            print(f"⚠️  손실보유 {name}({code}) {pr:+.1f}% 신호없음 → 반등 대기")

    # BUY 후보 — DART 포함 (이미 매도 결정된 종목 제외)
    sold_codes = {code for act, code, _, _, _ in actions if act == "SELL"}
    buy_candidates = []
    for code, d in analysis.items():
        buy_conds = d["buy_conds"]
        if len(buy_conds) >= buy_min and code not in sold_codes:
            buy_candidates.append((code, d))

    if buy_candidates:
        # DART 병렬 조회
        dart_results = {}
        with ThreadPoolExecutor(max_workers=len(buy_candidates)) as ex:
            futs = {ex.submit(get_dart, code): code for code, _ in buy_candidates}
            for fut in as_completed(futs):
                code = futs[fut]
                dart_results[code] = fut.result()

        # BUY ratio 분배
        valid_buys = []
        for code, d in buy_candidates:
            signal, dart_summary = dart_results.get(code, ("없음", ""))
            buy_conds = d["buy_conds"]
            n = len(buy_conds)
            if signal == "악재":
                print(f"⚫ BUY 제외 {d['name']}({code}) 조건{n}개이나 악재공시: {dart_summary[:40]}")
                continue
            if signal == "호재":
                n += 1
            valid_buys.append((code, d, n, dart_summary))

        if valid_buys:
            # 신뢰도 매핑
            conf_map = {2: 0.70, 3: 0.75, 4: 0.85}
            # ratio 분배
            ratio = 0.5 if len(valid_buys) == 1 else (0.35 if len(valid_buys) == 2 else 0.25)

            for code, d, n, dart_summary in valid_buys:
                name = d["name"]
                conf = conf_map.get(n, 0.95)
                conds_str = "/".join(d["buy_conds"])
                dart_note = f" [공시:{dart_summary[:20]}]" if dart_summary else ""
                reason = f"{conds_str}. 조건{n}개 충족{dart_note}"
                actions.append(("BUY", code, name, None, d))
                print(f"🟢 BUY {name}({code}) 조건{n}개 [{conds_str}]{dart_note} 신뢰도{conf} ratio{ratio}")
                cmd = [sys.executable, TRADE_PY, "--code", code, "--name", name,
                       "--action", "BUY", "--confidence", str(conf), "--ratio", str(ratio), "--reason", reason]
                print(f"   exec: {' '.join(cmd)}")
                r = subprocess.run(cmd, capture_output=True, text=True)
                print(r.stdout.strip())
                if r.returncode != 0:
                    print(f"   ⚠️ BUY 오류: {r.stderr.strip()}")

    if not actions:
        print("→ 전종목 HOLD (조건 미충족)")

    # ── 알림 메시지 ───────────────────────────────────────────
    now_str = datetime.now().strftime("%H:%M")
    if not actions:
        hold_parts = [
            f"{h.get('stock_name','?')[:4]}({code}) {h.get('profit_rate',0):+.1f}%"
            for code, h in holdings.items()
        ] if holdings else ["없음"]
        hold_str = " ".join(hold_parts)
        best = max(analysis.items(), key=lambda x: len(x[1]["buy_conds"]), default=None) if analysis else None
        if best and len(best[1]["buy_conds"]) > 0:
            bc, bd = best
            n = len(bd["buy_conds"])
            conds = "/".join(bd["buy_conds"][:2])
            reason = f"최고 {bd['name']}({bc}) {n}/{buy_min}개 [{conds}]"
        else:
            reason = "조건없음"
        msg = f"[{now_str}] HOLD | 보유:{hold_str} | {reason}"
    else:
        parts = []
        for act, code, name, pr, d in actions:
            if act == "SELL":
                pr_str = f" {pr:+.1f}%" if pr is not None else ""
                conds = "/".join((d["sell_conds"] or [f"{pr:.1f}%"])[:2])
                parts.append(f"SELL {name}({code}){pr_str} | {conds}")
            else:
                conds = "/".join(d["buy_conds"][:2])
                parts.append(f"BUY {name}({code}) | {conds}")
        msg = f"[{now_str}] " + " / ".join(parts)
    print(f"\nDISCORD_MSG: {msg}")
    _send_discord(msg)
    print("═"*50)


if __name__ == "__main__":
    main()
