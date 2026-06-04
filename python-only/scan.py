"""
scan.py (python-only) — 분석 + 판단 + 주문 + Discord 전부 Python 처리
AI 불필요. Linux cron 2분마다 실행.

전략 요약 (단타):
  매수: 신호 2개↑ (RSI≤55 [5분봉], 거래량1.3x, 하락-1%~-3%, BB하단 [5분봉], MA골 [5분봉], 호가비1.2, buy_rt130)
        거래량≥1.3x 또는 호가비≥1.2 반드시 포함 / 매도신호 없는 종목만 / DART 악재 제외
        9:20 전 / 14:30 후 / 지수 -1%↓ 시 신규 매수 중단 / 당일 -3%↓ 이상 급락 종목 제외
  매도: 수익+2%(단독) / RSI≥70, 급등+5%, BB상단, MA데드 중 2개↑ / 비상손절 -5%
        15:10↑ 장 마감 전 보유 전량 청산
"""
import sys, os, httpx, subprocess, time, threading, json
sys.stdout.reconfigure(line_buffering=True)
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
TRADE_PY      = os.path.join(BASE_DIR, "trade.py")
NOTIFY_PY     = os.path.join(BASE_DIR, "notify.py")
CLOSED_FILE         = os.path.join(BASE_DIR, ".market_closed_today")
PEAKS_FILE          = os.path.join(BASE_DIR, ".profit_peaks.json")
SOLD_TODAY_FILE     = os.path.join(BASE_DIR, ".sold_today.json")
LOCK_FILE           = os.path.join(BASE_DIR, ".scan_lock")
EXIT_TARGETS_FILE   = os.path.join(BASE_DIR, ".exit_targets.json")
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
    "005930":"삼성전자",  "403870":"HPSP",
    "086520":"에코프로",  "247540":"에코프로비엠","003670":"포스코퓨처엠","066970":"엘앤에프",
    "000270":"기아",
    "105560":"KB금융",    "055550":"신한지주",   "086790":"하나금융",  "138040":"메리츠금융",
    "064350":"현대로템",  "042660":"한화오션",   "272210":"한화시스템",
    "329180":"HD현대중공업","011200":"HMM",
    "015760":"한국전력",  "034020":"두산에너빌", "010950":"에쓰오일",  "096770":"SK이노베이션",
    "035720":"카카오",    "017670":"SK텔레콤",   "030200":"KT",
    "000720":"현대건설",  "003490":"대한항공",   "004020":"현대제철",
    "032830":"삼성생명",  "078930":"GS",
    "000150":"두산",      "000880":"한화",
}

WATCHLIST = [
    # 반도체 (2)
    "005930",  # 삼성전자        ~7만  (거래량 1위)
    "403870",  # HPSP            ~5만  (반도체 장비)
    # 2차전지 (4) — 모멘텀 강한 테마
    "086520",  # 에코프로        ~6만
    "247540",  # 에코프로비엠    가격변동
    "003670",  # 포스코퓨처엠    가격변동
    "066970",  # 엘앤에프        가격변동
    # 자동차 (1)
    "000270",  # 기아            ~8만
    # 금융 (4) — 시장 상승 시 추종
    "105560",  # KB금융          ~7만
    "055550",  # 신한지주        ~4만
    "086790",  # 하나금융지주    ~6만
    "138040",  # 메리츠금융지주  ~8만
    # 방산/조선/해운 (5) — 모멘텀 강한 테마
    "064350",  # 현대로템        ~5만  (방산)
    "042660",  # 한화오션        ~6만  (조선)
    "272210",  # 한화시스템      ~2만  (방산 IT)
    "329180",  # HD현대중공업    가격변동 (조선)
    "011200",  # HMM             ~1.5만 (해운, 고변동성)
    # 에너지 (4)
    "015760",  # 한국전력        ~2만
    "034020",  # 두산에너빌리티  ~2만
    "010950",  # 에쓰오일        ~6만
    "096770",  # SK이노베이션    ~9만
    # IT/통신 (3)
    "035720",  # 카카오          ~4만
    "017670",  # SK텔레콤        ~5만
    "030200",  # KT              ~3만
    # 저가 대형주 (5)
    "000720",  # 현대건설        ~3만
    "003490",  # 대한항공        ~2만
    "004020",  # 현대제철        ~3만
    "032830",  # 삼성생명        ~9만
    "078930",  # GS              ~4만
    # 그룹 지주 (2) — 테마 확산 수혜
    "000150",  # 두산            ~2만  (두산에너빌 모회사)
    "000880",  # 한화            ~3만  (한화오션·한화시스템 모회사)
]  # 총 30개

EXCLUDE = ["KODEX","TIGER","KINDEX","RISE","ACE","PLUS","KoAct","HANARO",
           "레버리지","인버스","ETN","ETF","리츠","채권","부동산","인프라",
           "선물","액티브","나스닥","S&P","다우","미국","중국","일본","글로벌"]

CORP_MAP = {
    "005930":"00126380",  # 삼성전자
    "035720":"00266965",  # 카카오
    "078930":"00108670",  # GS
    "015760":"00104427",  # 한국전력
    "034020":"00159161",  # 두산에너빌리티
    "000270":"00106136",  # 기아
    "105560":"00402511",  # KB금융
    "055550":"00191516",  # 신한지주
    "086790":"00547341",  # 하나금융지주
    "064350":"00168151",  # 현대로템
    "000720":"00105969",  # 현대건설
    "003490":"00131956",  # 대한항공
    "017670":"00178817",  # SK텔레콤
    "030200":"00156360",  # KT
    "272210":"01436939",  # 한화시스템
    "004020":"00104267",  # 현대제철
    "010950":"00104894",  # 에쓰오일
    "032830":"00109072",  # 삼성생명
    "003670":"00104799",  # 포스코퓨처엠
    "000150":"00104963",  # 두산
    "000880":"00106636",  # 한화
    # 403870(HPSP), 086520(에코프로), 247540(에코프로비엠), 066970(엘앤에프)
    # 329180(HD현대중공업), 042660(한화오션), 011200(HMM), 096770(SK이노베이션) → 폴백 검색
}

BAD_KW  = ["불성실공시","영업정지","과징금","횡령","손실","부도","감사의견","검찰","수사","파산","회생","거래정지"]
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

def fetch_minute_chart(code):
    return code, get(f"/kiwoom/chart/minute/{code}?tic_scope=5&count=100")

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

def run_trade(cmd, label):
    for attempt in range(3):
        r = subprocess.run(cmd, capture_output=True, text=True)
        print(r.stdout.strip())
        if r.returncode == 0:
            return r
        err = r.stderr.strip() or r.stdout.strip()
        print(f"⚠️ {label} 재시도 {attempt+1}/3: {err[:150]}")
        if attempt < 2:
            time.sleep(3)
    return r

def record_market_closed():
    today = datetime.now().strftime("%Y-%m-%d")
    count = 0
    if os.path.exists(CLOSED_FILE):
        try:
            d, c = open(CLOSED_FILE).read().strip().split(":")
            if d == today:
                count = int(c)
        except Exception:
            pass
    count += 1
    open(CLOSED_FILE, "w").write(f"{today}:{count}")
    if count >= 5:
        print(f"⛔ 장종료 {count}회 감지 → 오늘 스캔 중단")

def acquire_lock():
    if os.path.exists(LOCK_FILE):
        try:
            pid = int(open(LOCK_FILE).read().strip())
            os.kill(pid, 0)  # 프로세스 살아있으면 OSError 안 남
            return False     # 이전 실행 중
        except (ValueError, OSError):
            pass             # 죽은 프로세스 → 스탈 락 제거
        os.remove(LOCK_FILE)
    open(LOCK_FILE, "w").write(str(os.getpid()))
    return True

def release_lock():
    try:
        os.remove(LOCK_FILE)
    except FileNotFoundError:
        pass

def load_peaks():
    today = datetime.now().strftime("%Y-%m-%d")
    if os.path.exists(PEAKS_FILE):
        try:
            d = json.load(open(PEAKS_FILE))
            if d.get("date") == today:
                return d.get("peaks", {})
        except Exception:
            pass
    return {}

def save_peaks(peaks):
    today = datetime.now().strftime("%Y-%m-%d")
    json.dump({"date": today, "peaks": peaks}, open(PEAKS_FILE, "w"))

def load_sold_today():
    today = datetime.now().strftime("%Y-%m-%d")
    if os.path.exists(SOLD_TODAY_FILE):
        try:
            d = json.load(open(SOLD_TODAY_FILE))
            if d.get("date") == today:
                return set(d.get("codes", []))
        except Exception:
            pass
    return set()

def add_sold_today(code):
    today = datetime.now().strftime("%Y-%m-%d")
    codes = load_sold_today()
    codes.add(code)
    json.dump({"date": today, "codes": list(codes)}, open(SOLD_TODAY_FILE, "w"))

def load_exit_targets():
    today = datetime.now().strftime("%Y-%m-%d")
    if os.path.exists(EXIT_TARGETS_FILE):
        try:
            d = json.load(open(EXIT_TARGETS_FILE))
            if d.get("date") == today:
                return d.get("targets", {})
        except Exception:
            pass
    return {}

def save_exit_targets(targets):
    today = datetime.now().strftime("%Y-%m-%d")
    json.dump({"date": today, "targets": targets}, open(EXIT_TARGETS_FILE, "w"))

def send_error(context: str, err: str):
    now = datetime.now().strftime("%H:%M:%S")
    msg = f"🚨 [{now}] 에러 — {context}\n{err[:500]}"
    print(msg)
    send_discord(msg)

def build_discord_msg(now_str, cash, k_rsi, k_chg, overheated, k_dead, buy_min,
                      holdings, analysis, actions):
    has_buy  = any(a[0] == "BUY"  for a in actions)
    has_sell = any(a[0] == "SELL" for a in actions)
    label    = ("🟢BUY" if has_buy else "") + ("🟡SELL" if has_sell else "")
    trend    = "⬇️" if k_dead else "⬆️"
    market   = f"KODEX RSI{k_rsi:.0f}/{k_chg:+.1f}%{trend}{'⚠️' if overheated else ''}"
    lines    = [f"[{now_str}] {label} | {market} | 예수금 {cash:,}원", ""]

    # ── 매매 내역 (한 줄씩) ──
    acted_codes = set()
    for act, code, name, pr, d in actions:
        acted_codes.add(code)
        conds = "·".join(d["buy_conds"] if act == "BUY" else d["sell_conds"])
        if act == "BUY":
            sc = d.get("buy_score", 0)
            tgt = d.get("exit_target", 2.0)
            lines.append(f"🟢 {name}({code})  {sc}pt 목표+{tgt:.0f}%  RSI{d['rsi']:.0f} {d['chg']:+.1f}% {d['vol']:.1f}x  [{conds}]")
        else:
            tag = "🔴손절" if pr <= -3.0 else ("💰수익" if pr >= 0 else "손실")
            lines.append(f"🟡 {name}({code})  {tag}{pr:+.1f}%  RSI{d['rsi']:.0f} BB{d['bb_pos']}  [{conds}]")

    # ── 보유 현황 ──
    if holdings:
        parts = [f"{h.get('stock_name','?')[:4]} {h.get('profit_rate',0):+.1f}%"
                 for c, h in holdings.items()]
        lines.append(f"📦 {' | '.join(parts)}")

    # ── 근접 신호 종목 (매매 제외, 점수 1↑) ──
    near = [(c, d) for c, d in sorted(analysis.items(),
             key=lambda x: x[1].get("buy_score", 0), reverse=True)
            if d.get("buy_score", 0) >= 1 and c not in acted_codes][:6]
    if near:
        lines.append("")
        for code, d in near:
            sc   = d.get("buy_score", 0)
            icon = "🔶" if sc >= buy_min - 1 else "🔹"
            conds = "·".join(d["buy_conds"])
            lines.append(f"{icon} {d['name'][:5]}  {sc}pt/{buy_min}pt  RSI{d['rsi']:.0f} {d['chg']:+.1f}%  [{conds}]")

    msg = "\n".join(lines)
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

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)

def calc_bb(closes, period=20):
    if len(closes) < period:
        return None, None
    recent = closes[-period:]
    mean = sum(recent) / period
    std = (sum((x - mean) ** 2 for x in recent) / period) ** 0.5
    return round(mean + 2 * std), round(mean - 2 * std)

def calc_ma(closes, period):
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def stock_name(code, quote=None, ind=None, holding=None):
    quote = quote or {}
    ind = ind or {}
    holding = holding or {}
    return (quote.get("hts_kor_isnm") or quote.get("stk_nm") or quote.get("kor_isnm")
            or quote.get("name") or ind.get("name") or holding.get("stock_name")
            or NAME_MAP.get(code) or code)[:7]


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
    k_rsi   = float(k200.get("rsi_14")      or 50) if "_err" not in k200 else 50
    k_chg   = float(k200.get("change_rate") or 0)  if "_err" not in k200 else 0
    k_ma5   = float(k200.get("ma_5")        or 0)  if "_err" not in k200 else 0
    k_ma20  = float(k200.get("ma_20")       or 0)  if "_err" not in k200 else 0
    k_dead  = k_ma5 > 0 and k_ma20 > 0 and k_ma5 < k_ma20   # KODEX200 MA 데드크로스
    overheated = k_chg <= -2.0 or k_rsi >= 80
    now     = datetime.now()
    buy_min = 3   # 오전 최소 점수 (상승1%+거래량1x+RSI = 3pt 가능)
    peaks            = load_peaks()
    sold_today_codes = load_sold_today()
    exit_targets_store = load_exit_targets()   # 매수 시점 수익목표 (스캔마다 덮어쓰기 방지)

    now_str = now.strftime("%H:%M")
    k_trend = "하락추세⬇️" if k_dead else "상승추세⬆️"
    print(f"💰 예수금 {cash:,}원 | KODEX200 RSI{k_rsi:.0f}/{k_chg:+.1f}% {k_trend} {'과열⚠️' if overheated else ''} | 매수기준 점수{buy_min}pt↑")
    for code, h in holdings.items():
        pr = h.get('profit_rate', 0)
        warn = " 🔴스탑로스임박" if pr <= -3.5 else (" ⚠️주의" if pr <= -2.0 else "")
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

    # ── Phase 2.5: 5분봉 차트 (ind_codes 대상) ────────────────
    charts: dict = {}
    if ind_codes:
        with ThreadPoolExecutor(max_workers=5) as ex:
            c_futs = {ex.submit(fetch_minute_chart, c): c for c in ind_codes}
            for fut in as_completed(c_futs):
                code, c = fut.result(); charts[code] = c

    print(f"\n{'코드':<8}{'종목명':<8}{'가격':>8}  {'RSI':>5} {'변화율':>7} {'거래량':>6} {'BB':>6} {'호가비':>5}  B/S")
    print("─" * 72)
    print(f"[2단계 분석 대상: {', '.join(ind_codes)}]")

    analysis = {}
    for code in all_codes:
        q   = quotes.get(code, {})
        ind = inds.get(code, {})
        if "_err" in q: continue
        name  = stock_name(code, q, ind, holdings.get(code))
        price = abs(int(q.get("sel_fpr_bid") or 0))
        t_buy = int(q.get("tot_buy_req") or 0)
        t_sel = int(q.get("tot_sel_req") or 1)
        bid_r = round(t_buy / max(t_sel, 1), 2)
        if price == 0:
            if code in holdings:
                now_str_err = datetime.now().strftime("%H:%M:%S")
                send_error(f"보유종목 거래정지 감지 {name}({code})",
                           "호가 0원 — 거래정지 또는 거래중단. 매도 불가 상태.")
            else:
                print(f"⛔ {name}({code}) 호가 없음 (거래정지 의심)")
            continue
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

        # 5분봉 지표 계산 (5분봉 우선, 없으면 일봉 폴백)
        chart     = charts.get(code, {})
        candles   = chart.get("candles", []) if "_err" not in chart else []
        closes_5m = [float(c["close"]) for c in candles if "close" in c]
        rsi_5m           = calc_rsi(closes_5m)        if len(closes_5m) >= 15 else None
        bb_u_5m, bb_l_5m = calc_bb(closes_5m)         if len(closes_5m) >= 20 else (None, None)
        ma5_5m           = calc_ma(closes_5m, 5)
        ma20_5m          = calc_ma(closes_5m, 20)
        rsi_use  = rsi_5m  if rsi_5m  is not None else rsi
        bb_u_use = bb_u_5m if bb_u_5m is not None else bb_u
        bb_l_use = bb_l_5m if bb_l_5m is not None else bb_l
        ma5_use  = ma5_5m  if ma5_5m  is not None else ma5
        ma20_use = ma20_5m if ma20_5m is not None else ma20

        bb_pos = "하단" if bb_l_use and close <= bb_l_use else ("상단" if bb_u_use and close >= bb_u_use else "중간")
        ma_sig = "골" if (ma5_use and ma20_use and ma5_use > ma20_use) else "-"

        # 5분봉으로 실제 당일 변화율 보정 (일봉 API가 0% 반환 시 허위신호 방지)
        if candles and len(candles) >= 2:
            first_open = float(candles[0].get("open") or candles[0].get("close") or 0)
            if first_open > 0:
                chg_real = (close - first_open) / first_open * 100
                if abs(chg) < 0.01 and abs(chg_real) > 0.5:  # 일봉=0%인데 실제 움직임 있으면 보정
                    chg = chg_real

        # ── 매수 신호 ──────────────────────────────────────────────
        buy_score = 0
        buy_conds = []

        # ① 주가 모멘텀 (시장 대비 상대강도)
        rel_str = chg - k_chg
        if   rel_str >= 1.5: buy_score += 3; buy_conds.append(f"강세+{rel_str:.1f}%★★")
        elif rel_str >= 0.3: buy_score += 2; buy_conds.append(f"강세+{rel_str:.1f}%★")
        elif chg >= 0.5:     buy_score += 1; buy_conds.append(f"상승+{chg:.1f}%")

        # ② 거래량
        if   vol >= 3.0: buy_score += 3; buy_conds.append(f"거래량{vol:.1f}x★★")
        elif vol >= 1.5: buy_score += 2; buy_conds.append(f"거래량{vol:.1f}x★")
        elif vol >= 1.0: buy_score += 1; buy_conds.append(f"거래량{vol:.1f}x")

        # ③ 호가비 (매수세 > 매도세)
        if   bid_r >= 2.0: buy_score += 2; buy_conds.append(f"호가{bid_r:.1f}★")
        elif bid_r >= 1.3: buy_score += 1; buy_conds.append(f"호가{bid_r:.1f}")

        # ④ RSI 건강 구간
        if rsi_use and 45 <= rsi_use <= 65:
                             buy_score += 1; buy_conds.append(f"RSI{rsi_use:.0f}")

        # ⑤ 5분봉 연속 상승
        if len(candles) >= 3:
            cls3 = [float(c.get("close", 0)) for c in candles[-3:]]
            if all(v > 0 for v in cls3) and cls3[0] < cls3[1] < cls3[2]:
                buy_score += 1; buy_conds.append("연속상승")

        # ── 수익 목표 고정 2.5% ───────────────────────────────────
        exit_target = 2.5
        sell_conds = []
        if code in holdings:   # 보유 종목만 매도 신호 계산
            if pr is not None:
                effective_target = exit_targets_store.get(code, exit_target)
                if pr >= effective_target:
                    sell_conds.append(f"수익+{pr:.1f}%(목표{effective_target:.0f}%)")
            if rsi_use  and rsi_use  >= 70:                           sell_conds.append(f"RSI{rsi_use:.0f}")
            if chg      and chg      >= 5.0 and pr is not None and pr >= 1.0: sell_conds.append(f"급등+{chg:.1f}%")
            if bb_u_use and close    >= bb_u_use and pr is not None and pr >= 1.0: sell_conds.append("BB상단")
            if ma5_use  and ma20_use and ma5_use < ma20_use:          sell_conds.append("MA데드")

        s = len(sell_conds) if code in holdings else 0
        src = "5분" if rsi_5m is not None else "일봉"
        print(f"{code:<8}{name:<8}{price:>8,}  {rsi_use:>5.1f} {chg:>+7.1f}% {vol:>6.1f}x {bb_pos:>6} {bid_r:>5.2f}  {buy_score}pt/{s} [{src}]")
        analysis[code] = dict(name=name, price=price, pr=pr, rsi=rsi_use, chg=chg,
                               vol=vol, bid_r=bid_r, bb_pos=bb_pos, ma_sig=ma_sig,
                               brt=brt, exit_target=exit_target, buy_score=buy_score,
                               buy_conds=buy_conds, sell_conds=sell_conds)

    print("\n" + "═"*50)
    actions = []

    # ── SELL 판단 ─────────────────────────────────────────────
    force_close = now.hour > 15 or (now.hour == 15 and now.minute >= 10)
    if force_close:
        print("🕐 15:10 이후 — 장 마감 청산 모드")

    # 전일 잔류 포지션 감지: 9:00~9:20 사이에 보유종목이 있으면 WSL 중단 등으로
    # 어제 못 판 것 → 오버나이트 리스크 즉시 제거
    overnight_stale = (now.hour == 9 and now.minute <= 20)
    if overnight_stale and holdings:
        print("⏰ 9:20 이전 보유종목 감지 — 전일 잔류 포지션 즉시 청산")

    for code, d in analysis.items():
        if code not in holdings: continue
        pr   = d["pr"] or 0
        name = d["name"]
        sc   = d["sell_conds"]

        # 최고 수익 갱신 (트레일링 스탑 추적)
        if d["pr"] is not None:
            peaks[code] = max(peaks.get(code, d["pr"]), d["pr"])

        # 전일 잔류 포지션은 손익 무관 즉시 청산
        if overnight_stale:
            d["sell_conds"] = sc + ["전일잔류청산"]
            sc = d["sell_conds"]

        # 비상손절(-1.5%) 즉시 — 새 전략: 빠른 손절로 큰 손실 방지
        emergency_stop = (d["pr"] is not None and d["pr"] <= -1.5)
        # 트레일링: 최고점 대비 1.5% 이상 반락 시 청산 (최고 +3% → +1.5% 이하면 매도)
        trailing_stop  = (peaks.get(code, 0) >= 2.0 and d["pr"] is not None
                          and d["pr"] < peaks.get(code, 0) - 1.5)
        exit_tgt    = exit_targets_store.get(code, d.get("exit_target", 2.0))
        profit_exit = (d["pr"] is not None and d["pr"] >= exit_tgt)
        sell_min = 1 if profit_exit else 2

        # 트레일링: 장중 +2% 도달 후 0% 미만 반락 시 즉시 매도
        if trailing_stop and not emergency_stop:
            d["sell_conds"] = sc + [f"트레일링(최고{peaks[code]:+.1f}%)"]
            sc = d["sell_conds"]

        # 15:10 이후: 수익 +1.5% 이상이면 오버나이트 보유, 미만이면 청산
        if force_close and not emergency_stop and not trailing_stop and len(sc) < sell_min:
            if d["pr"] is not None and d["pr"] >= 1.5:
                print(f"💤 오버나이트 보유 {name}({code}) {pr:+.1f}% ≥ 1.5% — 내일 계속 관리")
            else:
                d["sell_conds"] = sc + ["마감청산"]
                sc = d["sell_conds"]

        if emergency_stop or trailing_stop or len(sc) >= sell_min:
            if "마감청산" in sc:
                conf, label = 0.80, "마감청산🕐"
            elif emergency_stop:
                conf, label = 0.90, "비상손절🔴"
            elif trailing_stop:
                conf, label = 0.85, "트레일링🔒"
            else:
                conf  = 0.85 if len(sc) >= 4 else 0.70
                label = "수익실현💰" if pr >= 0 else "손실축소"
            reason = (f"수익률{pr:+.1f}% | RSI{d['rsi']:.0f}({rsi_label(d['rsi'])}) | "
                      f"당일{d['chg']:+.1f}% | BB{d['bb_pos']} | "
                      f"호가비{d['bid_r']:.2f}({bid_label(d['bid_r'])}) | "
                      f"매도신호 {len(sc)}개: {', '.join(sc)}")
            print(f"🟡 SELL({label}) {name}({code}) {pr:+.1f}% [{', '.join(sc)}] 신뢰도{conf}")
            cmd = [sys.executable, TRADE_PY, "--code", code, "--name", name,
                   "--action", "SELL", "--confidence", str(conf), "--ratio", "1.0", "--reason", reason]
            r = run_trade(cmd, f"SELL {name}({code})")
            if r.returncode != 0:
                send_error(f"SELL {name}({code}) 주문 실패", r.stderr.strip() or r.stdout.strip())
            elif "✅ 접수" not in r.stdout:
                print(f"⚠️ SELL {name}({code}) 거절: {r.stdout.strip()}")
                if "장종료" in r.stdout:
                    record_market_closed()
            else:
                actions.append(("SELL", code, name, pr, d))
                if d["pr"] is not None and d["pr"] < 0:
                    add_sold_today(code)
                peaks.pop(code, None)
                exit_targets_store.pop(code, None)
                save_exit_targets(exit_targets_store)
        elif d["pr"] is not None and d["pr"] <= -1.0:
            print(f"⚠️  손실 주의 {name}({code}) {pr:+.1f}% (비상손절 -1.5% 임박)")

    # ── BUY 판단 ─────────────────────────────────────────────
    sold_codes     = {code for act, code, *_ in actions if act == "SELL"}
    # ── 매수 필터 (시간대 / 지수 방향 / 진입 제한) ──────────────
    # ── 매수 시간대 (시장 미시구조 기반) ─────────────────────────
    # 9:30~10:30 황금타임: 거래량 최고, 모멘텀 명확          → 3pt
    # 10:30~11:30 오전후반: 모멘텀 지속, 약간 엄격           → 4pt
    # 11:30~13:30 점심: 거래량 급감·허수 많음                → 매수 금지
    # 13:30~14:00 오후재개: 보유 없을 때만 허용              → 4pt
    # 14:00~      마감 리스크                                → 매수 금지
    h, m = now.hour, now.minute
    no_buy_time = (
        (h < 9) or
        (h == 9 and m < 30) or
        (h == 11 and m >= 30) or
        (h == 12) or
        (h == 13 and m < 30) or   # 점심 (13:00~13:30 금지)
        (h == 14 and m >= 30) or  # 14:30 이후 금지
        (h >= 15)
    )
    gold_window      = not no_buy_time and (h == 9 or (h == 10 and m < 30))
    late_morning     = not no_buy_time and h == 10 and m >= 30
    afternoon_window = not no_buy_time and ((h == 13 and m >= 30) or (h == 14 and m < 30))

    if gold_window:         effective_buy_min = buy_min          # 3pt
    elif late_morning:      effective_buy_min = buy_min + 1      # 4pt
    elif afternoon_window:  effective_buy_min = buy_min + 1      # 4pt
    else:                   effective_buy_min = buy_min + 2      # fallback (도달 불가)

    # 하락장 감지: -1% 이상 하락 OR 추세 하락(MA데드+RSI약세)
    bear_market = (k_chg <= -2.0 or (k_dead and k_rsi < 35))
    bear_reason = (f"KODEX200 {k_chg:+.1f}% 급락" if k_chg <= -1.5
                   else f"KODEX200 MA데드+RSI{k_rsi:.0f} 하락추세")

    if no_buy_time:
        if h < 9:                  reason_nb = "장 미개장 (9:00 전)"
        elif h == 9 and m < 30:    reason_nb = "시초가 혼조 (9:30 전)"
        elif (h == 11 and m >= 30) or h == 12 or (h == 13 and m < 30): reason_nb = "점심시간 (11:30~13:30)"
        else:                      reason_nb = "매수 세션 종료 (14:30↑)"
        print(f"⏰ {reason_nb} — 신규 매수 중단")
        buy_candidates = []
    elif bear_market:
        print(f"📉 {bear_reason} — 신규 매수 중단")
        buy_candidates = []
    elif afternoon_window and len(holdings) > 0:
        print(f"⏰ 오후 재개 — 보유 종목 있음 → 신규 매수 중단")
        buy_candidates = []
    else:
        buy_candidates = [(c, d) for c, d in analysis.items()
                          if d.get("buy_score", 0) >= effective_buy_min
                          and c not in sold_codes
                          and c not in holdings
                          and len(d["sell_conds"]) == 0
                          and d["chg"] >= -0.5                # 급락 종목 제외
                          and d["chg"] <= 8.0                 # 과열 제외
                          and k_chg >= -2.0                   # bear_market 기준과 통일
                          and d["rsi"] < 78
                          and (d["vol"] > 0 or d["bid_r"] >= 1.3)
                          and d["bid_r"] <= 15.0
                          and len(holdings) < 3]

    if buy_candidates:
        dart_results = {}
        with ThreadPoolExecutor(max_workers=len(buy_candidates)) as ex:
            futs = {ex.submit(get_dart, c): c for c, _ in buy_candidates}
            for fut in as_completed(futs):
                dart_results[futs[fut]] = fut.result()

        valid_buys = []
        for code, d in buy_candidates:
            sig, summary = dart_results.get(code, ("없음", ""))
            n = d.get("buy_score", 0)
            if sig == "악재":
                print(f"⚫ BUY 제외 {d['name']}({code}) 악재공시: {summary[:40]}")
                continue
            if sig == "호재": n += 2   # 호재 공시 = +2점 보너스
            valid_buys.append((code, d, n, summary))

        if valid_buys:
            for code, d, n, dart_summary in valid_buys:
                name = d["name"]
                # 점수 기반 신뢰도: 3점→0.70, 5점→0.80, 7점→0.88, 9점↑→0.94
                conf = min(0.70 + (n - 3) * 0.04, 0.94)
                # 점수 기반 비율: 3점→20%, +1점당+4%, 최대 40%
                # 후보 많을수록 상한 낮춰 리스크 분산
                sig_ratio = min(0.20 + (n - 3) * 0.04, 0.40)
                if   len(valid_buys) >= 3: ratio = min(sig_ratio, 0.25)
                elif len(valid_buys) == 2: ratio = min(sig_ratio, 0.30)
                else:                      ratio = sig_ratio
                conds = "/".join(d["buy_conds"])
                reason = (f"점수{n}pt(기준{buy_min}pt) | "
                          f"RSI{d['rsi']:.0f}({rsi_label(d['rsi'])}) | "
                          f"당일{d['chg']:+.1f}% | 거래량{d['vol']:.1f}x({vol_label(d['vol'])}) | "
                          f"BB{d['bb_pos']} | 호가비{d['bid_r']:.2f}({bid_label(d['bid_r'])}) | "
                          f"목표수익+{d['exit_target']:.0f}% | "
                          f"KODEX200 RSI{k_rsi:.0f} {'과열' if overheated else '정상'}"
                          + (f" | 공시: {dart_summary[:30]}" if dart_summary else ""))
                print(f"🟢 BUY {name}({code}) {n}pt [{conds}] 목표+{d['exit_target']:.0f}% 신뢰도{conf:.2f} ratio{ratio:.2f}")
                cmd = [sys.executable, TRADE_PY, "--code", code, "--name", name,
                       "--action", "BUY", "--confidence", str(conf), "--ratio", str(ratio),
                       "--reason", reason]
                r = run_trade(cmd, f"BUY {name}({code})")
                if r.returncode != 0:
                    send_error(f"BUY {name}({code}) 주문 실패", r.stderr.strip() or r.stdout.strip())
                elif "✅ 접수" not in r.stdout:
                    print(f"⚠️ BUY {name}({code}) 거절: {r.stdout.strip()}")
                    if "장종료" in r.stdout:
                        record_market_closed()
                else:
                    actions.append(("BUY", code, name, None, d))
                    exit_targets_store[code] = d["exit_target"]
                    save_exit_targets(exit_targets_store)

    # 당일 peaks 저장 (매도 완료 종목 제외 후 저장)
    save_peaks(peaks)

    if not actions:
        print("→ 전종목 HOLD")

    # ── Discord ───────────────────────────────────────────────
    if not actions:
        hold = " ".join(f"{h.get('stock_name','?')[:4]}({c}) {h.get('profit_rate',0):+.1f}%"
                        for c, h in holdings.items()) or "없음"
        best = max(analysis.items(), key=lambda x: x[1].get("buy_score", 0), default=None)
        if best and best[1].get("buy_score", 0) >= 1:
            conds = " / ".join(best[1]['buy_conds'])
            hint = f" | 최고신호 {best[1]['name']}({best[0]}) {best[1].get('buy_score',0)}pt/{buy_min}pt [{conds}]"
        elif best:
            hint = f" | 최고신호 {best[1]['name']}({best[0]}) 0pt/{buy_min}pt"
        else:
            hint = ""
        trend     = "⬇️" if k_dead else "⬆️"
        market    = f"KODEX RSI{k_rsi:.0f}/{k_chg:+.1f}%{trend}{'⚠️' if overheated else ''}"
        cash_warn = " | 💸예수금부족" if cash < 50000 else ""
        msg = f"[{now_str}] HOLD | {market} | 보유:{hold}{cash_warn}{hint}"
        send_discord(msg)
    else:
        msg = build_discord_msg(now_str, cash, k_rsi, k_chg, overheated, k_dead,
                                buy_min, holdings, analysis, actions)
        send_discord(msg)

    print(f"\n{msg}")


if __name__ == "__main__":
    if not acquire_lock():
        print("⏭️ 이전 스캔 실행 중 — 이번 실행 건너뜀")
        sys.exit(0)
    try:
        main()
    except Exception as e:
        import traceback
        send_error("scan.py 치명적 오류", traceback.format_exc()[-500:])
        raise
    finally:
        release_lock()
