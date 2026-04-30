"""
scan.py (python-only) — 분석 + 판단 + 주문 + Discord 전부 Python 처리
AI 불필요. Linux cron 5분마다 실행.

전략 요약:
  매수: 강한 과매도 반등 신호 3개↑ (RSI≤35, 거래량2x, 조정-2%, BB하단 등)
  매도: 수익+7% / RSI≥75 / 급등+5% / BB상단 + 신호 2개↑
  스탑로스: 수익률 -5% 이하 → 즉시 매도
"""
import sys, os, httpx, subprocess, time, threading
sys.stdout.reconfigure(line_buffering=True)
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
TRADE_PY   = os.path.join(BASE_DIR, "trade.py")
NOTIFY_PY  = os.path.join(BASE_DIR, "notify.py")
SERVER_URL = os.getenv("TRADING_SERVER_URL", "")
API_KEY    = os.getenv("SIGNAL_SECRET_KEY", "")
DART_KEY   = os.getenv("DART_API_KEY", "")

if not SERVER_URL: sys.exit("❌ TRADING_SERVER_URL 없음")
if not API_KEY:    sys.exit("❌ SIGNAL_SECRET_KEY 없음")

HEADERS = {"X-Api-Key": API_KEY}
_client = httpx.Client(headers=HEADERS, timeout=10)

# 키움 조회 API: 연속 burst 방지 — 요청 간 최소 1.5초 간격
_rate_lock      = threading.Lock()
_last_req_time  = 0.0

def _rate_wait():
    global _last_req_time
    with _rate_lock:
        elapsed = time.time() - _last_req_time
        if elapsed < 1.5:
            time.sleep(1.5 - elapsed)
        _last_req_time = time.time()

NAME_MAP = {
    "005930":"삼성전자",  "000660":"SK하이닉스", "042700":"한미반도체","403870":"HPSP",
    "058470":"리노공업",  "051910":"LG화학",     "006400":"삼성SDI",   "247540":"에코프로비엠",
    "003670":"포스코퓨처엠","066970":"엘앤에프",  "137040":"피엔티",    "068270":"셀트리온",
    "207940":"삼성바이오", "005380":"현대차",     "000270":"기아",      "012330":"현대모비스",
    "105560":"KB금융",    "055550":"신한지주",   "086790":"하나금융",  "012450":"한화에어로",
    "079550":"LIG넥스원", "035420":"NAVER",      "035720":"카카오",    "259960":"크래프톤",
    "015760":"한국전력",  "034020":"두산에너빌", "078930":"GS",        "061250":"유진로봇",
    "217820":"이루다",    "001510":"BYC",        "056080":"유진에너지", "084850":"유진기업",
}

WATCHLIST = [
    # 반도체
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "042700",  # 한미반도체
    "403870",  # HPSP
    "058470",  # 리노공업
    # 2차전지
    "051910",  # LG화학
    "006400",  # 삼성SDI
    "247540",  # 에코프로비엠
    "003670",  # 포스코퓨처엠
    "066970",  # 엘앤에프
    "137040",  # 피엔티
    # 바이오
    "068270",  # 셀트리온
    "207940",  # 삼성바이오로직스
    # 자동차
    "005380",  # 현대차
    "000270",  # 기아
    "012330",  # 현대모비스
    # 금융
    "105560",  # KB금융
    "055550",  # 신한지주
    "086790",  # 하나금융지주
    # 방산
    "012450",  # 한화에어로스페이스
    "079550",  # LIG넥스원
    # AI/IT
    "035420",  # NAVER
    "035720",  # 카카오
    "259960",  # 크래프톤
    # 에너지
    "015760",  # 한국전력
    "034020",  # 두산에너빌리티
    "078930",  # GS
    # 기타
    "061250",  # 유진로봇
    "217820",  # 이루다
    "001510",  # BYC
    "056080",  # 유진에너지솔루션
    "084850",  # 유진기업
    # "XXXXXX",  # 컨텍 ← 종목코드 확인 후 추가
]

EXCLUDE = ["KODEX","TIGER","KINDEX","RISE","ACE","PLUS","KoAct","HANARO",
           "레버리지","인버스","ETN","ETF","리츠","채권","부동산","인프라",
           "선물","액티브","나스닥","S&P","다우","미국","중국","일본","글로벌"]

CORP_MAP = {
    "005930":"00126380","000660":"00164779","035420":"00266961",
    "051910":"00401731","006400":"00164488","078930":"00108670",
    "061250":"00648826","217820":"00877422","001510":"00112774",
    "056080":"00631518","084850":"00741612",
    "015760":"00104427",  # 한국전력
    "034020":"00159161",  # 두산에너빌리티
    "042700":"00207536",  # 한미반도체
    "068270":"00124534",  # 셀트리온
    "005380":"00164742",  # 현대차
    "000270":"00106136",  # 기아
    "012330":"00164876",  # 현대모비스
    "105560":"00402511",  # KB금융
    "055550":"00191516",  # 신한지주
    "086790":"00547341",  # 하나금융지주
    "012450":"00164884",  # 한화에어로스페이스
    "035720":"00266965",  # 카카오
    "003670":"00104799",  # 포스코퓨처엠
    # 나머지는 stock_code 검색으로 폴백
}

BAD_KW  = ["불성실공시","영업정지","과징금","횡령","손실","부도","감사의견","검찰","수사","파산","회생"]
GOOD_KW = ["수주","계약체결","실적개선","자사주취득","영업이익","매출증가","흑자"]


def get(path):
    _rate_wait()
    for attempt in range(3):
        try:
            r = _client.get(f"{SERVER_URL}{path}")
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < 2:
                time.sleep(1)
                continue
            return {"_err": str(e)}

def fetch_quote(code):
    return code, get(f"/kiwoom/quote/{code}")

def fetch_ind(code):
    return code, get(f"/kiwoom/indicators/{code}")

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

def send_discord(msg):
    for attempt in range(3):
        try:
            r = subprocess.run([sys.executable, NOTIFY_PY, msg],
                               capture_output=True, text=True, timeout=30)
            print(r.stdout.strip() or "(notify 출력없음)")
            if r.returncode == 0:
                return
            print(f"⚠️ Discord 실패(rc={r.returncode}): {r.stderr.strip()[:200]}")
        except Exception as e:
            print(f"⚠️ Discord 전송 실패: {e}")
        if attempt < 2:
            time.sleep(3)

def send_error(context: str, err: str):
    now = datetime.now().strftime("%H:%M:%S")
    msg = f"🚨 [{now}] 에러 — {context}\n{err[:300]}"
    print(msg)
    send_discord(msg)

def build_discord_msg(now_str, cash, k_rsi, k_chg, overheated, buy_min,
                      holdings, analysis, actions):
    lines = []
    market = "⚠️과열" if overheated else "✅정상"
    lines.append(f"{'🟢 BUY' if any(a[0]=='BUY' for a in actions) else '🟡 SELL'} 체결 [{now_str}]")
    lines.append(f"KODEX RSI{k_rsi:.0f}/{k_chg:+.1f}% {market} | 예수금 {cash:,}원 | 기준{buy_min}개↑")

    if holdings:
        hold_parts = [f"{h.get('stock_name','?')[:5]}({c}) {h.get('profit_rate',0):+.1f}%"
                      for c, h in holdings.items()]
        lines.append(f"📦 보유: {' | '.join(hold_parts)}")

    # ── 매매 상세 ──
    for act, code, name, pr, d in actions:
        lines.append("─" * 28)
        if act == "BUY":
            n = len(d["buy_conds"])
            conf_map = {3: 0.75, 4: 0.85, 5: 0.90}
            conf = conf_map.get(n, 0.95)
            lines.append(f"🟢 BUY  {name}({code})")
            lines.append(f"   신뢰도 {conf} | 신호 {n}/{buy_min}개")
            lines.append(f"   RSI {d['rsi']:.0f} ({rsi_label(d['rsi'])})")
            lines.append(f"   당일 {d['chg']:+.1f}% | 거래량 {d['vol']:.1f}x ({vol_label(d['vol'])})")
            lines.append(f"   BB {d['bb_pos']} | 호가비 {d['bid_r']:.2f} ({bid_label(d['bid_r'])})")
            lines.append(f"   충족: {' / '.join(d['buy_conds'])}")
        else:
            emergency = pr <= -30.0
            label = "비상손절🔴" if emergency else ("수익실현💰" if pr >= 0 else "손실축소")
            lines.append(f"🟡 SELL {name}({code})  {label}")
            lines.append(f"   수익률 {pr:+.1f}% | 신뢰도 {'0.90' if emergency else ('0.85' if len(d['sell_conds'])>=4 else '0.70')}")
            lines.append(f"   RSI {d['rsi']:.0f} ({rsi_label(d['rsi'])})")
            lines.append(f"   당일 {d['chg']:+.1f}% | BB {d['bb_pos']} | 호가비 {d['bid_r']:.2f}")
            lines.append(f"   충족: {' / '.join(d['sell_conds'])}")

    # ── 전체 종목 신호 표 ──
    lines.append("─" * 28)
    lines.append("📊 전체 종목 신호 현황")
    sorted_stocks = sorted(analysis.items(), key=lambda x: len(x[1]["buy_conds"]), reverse=True)

    signal_stocks = [(c, d) for c, d in sorted_stocks if len(d["buy_conds"]) >= 1]
    zero_stocks   = [(c, d) for c, d in sorted_stocks if len(d["buy_conds"]) == 0]

    for code, d in signal_stocks:
        n = len(d["buy_conds"])
        icon = "🟢" if n >= buy_min else ("🔶" if n == buy_min - 1 else "🔹")
        lines.append(f"{icon} {d['name'][:6]}({code})  "
                     f"RSI{d['rsi']:.0f} {d['chg']:+.1f}% {d['vol']:.1f}x  "
                     f"[{n}/{buy_min}] {' '.join(d['buy_conds'])}")

    if zero_stocks:
        compact = " ".join(f"{d['name'][:4]}" for _, d in zero_stocks)
        lines.append(f"⬜ 신호없음: {compact}")

    msg = "\n".join(lines)
    # Discord 2000자 제한
    return msg[:1990] if len(msg) > 1990 else msg

def rsi_label(v):
    if v <= 25: return "극과매도"
    if v <= 35: return "강한과매도"
    if v <= 50: return "과매도"
    if v <= 70: return "중립"
    if v <= 80: return "과매수"
    return "극과매수"

def vol_label(v):
    if v >= 3.0: return "폭발적수급"
    if v >= 2.0: return "강한수급유입"
    if v >= 1.3: return "수급유입"
    return "평균이하"

def bid_label(v):
    if v >= 2.0: return "강한매수우위"
    if v >= 1.5: return "매수우위"
    if v >= 0.5: return "균형"
    return "매도우위"


def main():
    # ── Phase 1: quote 전체 + 인프라 (4 + 34회) ────────────────
    with ThreadPoolExecutor(max_workers=10) as ex:
        f_acct  = ex.submit(get, "/kiwoom/account")
        f_k200  = ex.submit(get, "/kiwoom/indicators/069500")
        f_rank1 = ex.submit(get, "/kiwoom/ranking?mrkt_tp=001&sort_tp=1")
        f_rank2 = ex.submit(get, "/kiwoom/ranking?mrkt_tp=001&sort_tp=2")
        q_futs  = {ex.submit(fetch_quote, c): c for c in WATCHLIST}
        acct = f_acct.result(); k200 = f_k200.result()
        rank1 = f_rank1.result(); rank2 = f_rank2.result()
        quotes = {}
        for fut in as_completed(q_futs):
            code, q = fut.result(); quotes[code] = q

    if "_err" in acct:
        send_error("계좌 조회 실패", acct["_err"])
    cash     = int(acct.get("cash", 0)) if "_err" not in acct else 0
    holdings = {}
    if "_err" not in acct:
        for h in acct.get("holdings", []):
            holdings[h["stock_code"]] = h

    if "_err" in k200:
        send_error("KODEX200 지표 조회 실패", k200["_err"])
    k_rsi      = float(k200.get("rsi_14") or 50)    if "_err" not in k200 else 50
    k_chg      = float(k200.get("change_rate") or 0) if "_err" not in k200 else 0
    overheated = k_chg <= -2.0 or k_rsi >= 80
    # ── 고인물 시간대 판단 (매수 자제, 금지 아님) ───────────────
    # 9:00~9:30  장 초반: 변동성 극심          → +1
    # 11:20~13:00 점심: 유동성 감소            → +1
    # 14:30~15:20 장 마감: 기관 정리매물       → +1
    now     = datetime.now()
    hour, minute = now.hour, now.minute
    caution_period  = ((hour == 9 and minute < 30) or
                       (hour == 11 and minute >= 20) or
                       (12 <= hour < 13) or
                       (hour == 14 and minute >= 30))
    buy_min_base    = 3 if overheated else 2
    buy_min         = buy_min_base + (1 if caution_period else 0)

    now_str = now.strftime("%H:%M")
    print(f"💰 예수금 {cash:,}원 | KODEX200 RSI{k_rsi:.0f}/{k_chg:+.1f}% {'과열⚠️' if overheated else '정상'} | 매수기준 {buy_min}개↑")
    for code, h in holdings.items():
        pr = h.get('profit_rate', 0)
        warn = " 🔴스탑로스임박" if pr <= -4.0 else (" ⚠️주의" if pr <= -2.5 else "")
        print(f"📦 보유 {h.get('stock_name','?')}({code}) {h.get('quantity',0)}주 {pr:+.1f}%{warn}")

    # 랭킹 후보 (quote 없이 순위 데이터만 활용)
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
                 key=lambda x: -(2*x[1]["both"]+(x[1]["sig"]=="5")))[:5]]
    all_codes = list(dict.fromkeys(WATCHLIST + top_codes))

    # 랭킹 신규 종목 quote 추가 조회
    extra = [c for c in top_codes if c not in quotes]
    if extra:
        with ThreadPoolExecutor(max_workers=5) as ex:
            futs = {ex.submit(fetch_quote, c): c for c in extra}
            for fut in as_completed(futs):
                code, q = fut.result(); quotes[code] = q

    # ── Phase 2: 호가비 상위 5개 + 보유종목 → indicators ──────
    bid_scores = {}
    for code in all_codes:
        q = quotes.get(code, {})
        if "_err" in q: continue
        t_buy = int(q.get("tot_buy_req") or 0)
        t_sel = int(q.get("tot_sel_req") or 1)
        bid_scores[code] = round(t_buy / max(t_sel, 1), 2)

    top5 = sorted([c for c in bid_scores if c not in holdings],
                  key=bid_scores.get, reverse=True)[:5]
    ind_codes = list(dict.fromkeys(list(holdings.keys()) + top5))

    inds: dict = {}
    if ind_codes:
        with ThreadPoolExecutor(max_workers=5) as ex:
            i_futs = {ex.submit(fetch_ind, c): c for c in ind_codes}
            for fut in as_completed(i_futs):
                code, i = fut.result(); inds[code] = i

    print(f"\n{'코드':<8}{'종목명':<8}{'가격':>8}  {'RSI':>5} {'변화율':>7} {'거래량':>6} {'BB':>6} {'호가비':>5}  B/S")
    print("─" * 72)
    print(f"[2단계 분석 대상: {', '.join(ind_codes)}]")

    analysis = {}
    for code in all_codes:
        q   = quotes.get(code, {})
        ind = inds.get(code, {})
        if "_err" in q: continue
        name  = (NAME_MAP.get(code)
                 or q.get("hts_kor_isnm") or q.get("stk_nm") or q.get("kor_isnm")
                 or q.get("name") or ind.get("name")
                 or holdings.get(code, {}).get("stock_name") or code)[:7]
        price = abs(int(q.get("sel_fpr_bid") or 0))
        t_buy = int(q.get("tot_buy_req") or 0)
        t_sel = int(q.get("tot_sel_req") or 1)
        bid_r = round(t_buy / max(t_sel, 1), 2)
        if price == 0: continue
        if price > cash and code not in holdings: continue
        # indicators 없는 종목은 quote 기반 표시만 (신호 없음)

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
        ma_sig = "골" if (ma5 and ma20 and ma5 > ma20) else "-"  # ① 골든크로스: MA5 > MA20

        # ── 매수 신호 ──────────────────────────────────────────
        buy_conds = []
        if rsi  and rsi  <= 45:   buy_conds.append(f"RSI{rsi:.0f}")
        if vol  and vol  >= 1.3:  buy_conds.append(f"거래량{vol:.1f}x")
        if chg  and chg  <= -1.0: buy_conds.append(f"하락{chg:.1f}%")
        if bb_l and close <= bb_l: buy_conds.append("BB하단")
        if ma5 and ma20 and ma5 > ma20: buy_conds.append("MA골")  # ① 수정
        if bid_r and bid_r >= 1.2: buy_conds.append(f"호가{bid_r:.1f}")
        if brt  and brt  >= 130:  buy_conds.append(f"buy_rt{brt:.0f}")

        # ── 매도 신호 (롱 관점) ────────────────────────────────
        sell_conds = []
        if pr is not None:
            if pr >= 7.0:   sell_conds.append(f"수익+{pr:.1f}%")  # ③ 수익 단독 트리거
            if pr <= -10.0: sell_conds.append(f"손실{pr:.1f}%")
        if rsi  and rsi  >= 80:   sell_conds.append(f"RSI{rsi:.0f}")
        if chg  and chg  >= 7.0:  sell_conds.append(f"급등+{chg:.1f}%")
        if bb_u and close >= bb_u: sell_conds.append("BB상단")
        if bb_u and close >= bb_u and chg < 0: sell_conds.append("BB상단반전")

        b, s = len(buy_conds), len(sell_conds) if code in holdings else 0
        print(f"{code:<8}{name:<8}{price:>8,}  {rsi:>5.1f} {chg:>+7.1f}% {vol:>6.1f}x {bb_pos:>6} {bid_r:>5.2f}  {b}/{s}")
        analysis[code] = dict(name=name, price=price, pr=pr, rsi=rsi, chg=chg,
                               vol=vol, bid_r=bid_r, bb_pos=bb_pos, ma_sig=ma_sig,
                               brt=brt, buy_conds=buy_conds, sell_conds=sell_conds)

    print("\n" + "═"*50)
    actions = []

    # ── SELL 판단 ─────────────────────────────────────────────
    for code, d in analysis.items():
        if code not in holdings: continue
        pr   = d["pr"] or 0
        name = d["name"]
        sc   = d["sell_conds"]

        # 비상손절(-30%) 즉시, 수익구간(+7%↑)은 1개만, 나머지는 2개↑
        emergency_stop = (d["pr"] is not None and d["pr"] <= -30.0)
        profit_exit    = (d["pr"] is not None and d["pr"] >= 7.0)
        sell_min = 1 if profit_exit else 2

        if emergency_stop or len(sc) >= sell_min:
            conf  = 0.90 if emergency_stop else (0.85 if len(sc) >= 4 else 0.70)
            label = "비상손절🔴" if emergency_stop else ("수익실현💰" if pr >= 0 else "손실축소")
            reason = (f"수익률{pr:+.1f}% | RSI{d['rsi']:.0f}({rsi_label(d['rsi'])}) | "
                      f"당일{d['chg']:+.1f}% | BB{d['bb_pos']} | "
                      f"호가비{d['bid_r']:.2f}({bid_label(d['bid_r'])}) | "
                      f"매도신호 {len(sc)}개: {', '.join(sc)}")
            print(f"🟡 SELL({label}) {name}({code}) {pr:+.1f}% [{', '.join(sc)}] 신뢰도{conf}")
            cmd = [sys.executable, TRADE_PY, "--code", code, "--name", name,
                   "--action", "SELL", "--confidence", str(conf), "--ratio", "1.0", "--reason", reason]
            r = subprocess.run(cmd, capture_output=True, text=True)
            print(r.stdout.strip())
            if r.returncode != 0:
                send_error(f"SELL {name}({code}) 주문 실패", r.stderr.strip() or r.stdout.strip())
            actions.append(("SELL", code, name, pr, d))
        elif d["pr"] is not None and d["pr"] <= -8.0:
            print(f"⚠️  손실 주의 {name}({code}) {pr:+.1f}% (신호기준 -10%, 비상 -30%)")

    # ── BUY 판단 ─────────────────────────────────────────────
    sold_codes     = {code for act, code, *_ in actions if act == "SELL"}
    buy_candidates = [(c, d) for c, d in analysis.items()
                      if len(d["buy_conds"]) >= buy_min
                      and c not in sold_codes
                      and c not in holdings]

    if buy_candidates:
        dart_results = {}
        with ThreadPoolExecutor(max_workers=len(buy_candidates)) as ex:
            futs = {ex.submit(get_dart, c): c for c, _ in buy_candidates}
            for fut in as_completed(futs):
                dart_results[futs[fut]] = fut.result()

        valid_buys = []
        for code, d in buy_candidates:
            sig, summary = dart_results.get(code, ("없음", ""))
            n = len(d["buy_conds"])
            if sig == "악재":
                print(f"⚫ BUY 제외 {d['name']}({code}) 악재공시: {summary[:40]}")
                continue
            if sig == "호재": n += 1
            valid_buys.append((code, d, n, summary))

        if valid_buys:
            ratio    = 0.5 if len(valid_buys) == 1 else (0.35 if len(valid_buys) == 2 else 0.25)
            conf_map = {3: 0.75, 4: 0.85, 5: 0.90}
            for code, d, n, dart_summary in valid_buys:
                name  = d["name"]
                conf  = conf_map.get(n, 0.95)
                conds = "/".join(d["buy_conds"])
                reason = (f"RSI{d['rsi']:.0f}({rsi_label(d['rsi'])}) | "
                          f"당일{d['chg']:+.1f}% | 거래량{d['vol']:.1f}x({vol_label(d['vol'])}) | "
                          f"BB{d['bb_pos']} | 호가비{d['bid_r']:.2f}({bid_label(d['bid_r'])}) | "
                          f"MA{'골든크로스' if d['ma_sig']=='골' else '없음'} | "
                          f"매수신호 {n}개(기준{buy_min}개) | "
                          f"KODEX200 RSI{k_rsi:.0f} {'과열' if overheated else '정상'}"
                          + (f" | 공시: {dart_summary[:30]}" if dart_summary else ""))
                print(f"🟢 BUY {name}({code}) 조건{n}개 [{conds}] 신뢰도{conf} ratio{ratio}")
                cmd = [sys.executable, TRADE_PY, "--code", code, "--name", name,
                       "--action", "BUY", "--confidence", str(conf), "--ratio", str(ratio),
                       "--reason", reason]
                r = subprocess.run(cmd, capture_output=True, text=True)
                print(r.stdout.strip())
                if r.returncode != 0:
                    send_error(f"BUY {name}({code}) 주문 실패", r.stderr.strip() or r.stdout.strip())
                actions.append(("BUY", code, name, None, d))

    if not actions:
        print("→ 전종목 HOLD")

    # ── Discord ───────────────────────────────────────────────
    if not actions:
        hold = " ".join(f"{h.get('stock_name','?')[:4]}({c}) {h.get('profit_rate',0):+.1f}%"
                        for c, h in holdings.items()) or "없음"
        best = max(analysis.items(), key=lambda x: len(x[1]["buy_conds"]), default=None)
        if best and best[1]['buy_conds']:
            conds = " / ".join(best[1]['buy_conds'])
            hint = f" | 최고신호 {best[1]['name']}({best[0]}) {len(best[1]['buy_conds'])}/{buy_min}개 [{conds}]"
        elif best:
            hint = f" | 최고신호 {best[1]['name']}({best[0]}) 0/{buy_min}개"
        else:
            hint = ""
        period = "⚠️자제" if caution_period else ""
        market = f"KODEX RSI{k_rsi:.0f}/{k_chg:+.1f}%{'⚠️과열' if overheated else ''}"
        cash_warn = " | 💸예수금부족(매수불가)" if cash < 50000 else ""
        msg = f"[{now_str}] HOLD {period}| {market} | 보유:{hold}{cash_warn}{hint}"
        send_discord(msg)
    else:
        msg = build_discord_msg(now_str, cash, k_rsi, k_chg, overheated,
                                buy_min, holdings, analysis, actions)
        send_discord(msg)

    print(f"\n{msg}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        send_error("scan.py 치명적 오류", traceback.format_exc()[-500:])
        raise
