"""
scan.py (python-only) — 분석 + 판단 + 주문 + Discord 전부 Python 처리
AI 불필요. Linux cron 2분마다 실행.

전략 요약 (단타, 공격적 모드):
  매수: 상대강도·거래량·호가비·체결강도·RSI·연속상승·고점돌파·볼륨스파이크·MA골든·BB반등 점수 합산
        9:20~15:00 매수, 점심/마감직전은 강신호(6pt+)만 / 하락장(-1.5%)·지수롤오버·연속손실3회·일일손실4%p 시 중단
  매도: 신호 강도별 목표수익 2~4% / 비상손절 -1.0%(하락장 -0.7%) / 트레일링(+1.5%↑ 후 -1.0%p, 수수료 이상 수익시만)
        15:10↑ 마감 청산 (수익 1.5%↑면 오버나이트 보유)
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
KCHG_HIST_FILE      = os.path.join(BASE_DIR, ".kchg_history.json")
DAILY_RISK_FILE     = os.path.join(BASE_DIR, ".daily_risk.json")
OVERNIGHT_FILE      = os.path.join(BASE_DIR, ".overnight_hold.json")
LOCK_FILE           = os.path.join(BASE_DIR, ".scan_lock")
ERROR_SUPPRESS_FILE = os.path.join(BASE_DIR, ".error_suppress.json")
COOLDOWN_FILE       = os.path.join(BASE_DIR, ".cooldown_until.json")

MAX_DAILY_BUYS = 5   # 하루 최대 매수 횟수 (과매매 방지)
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
    "005930":"삼성전자",  "084850":"아이티엠반도체",
    "086520":"에코프로",  "247540":"에코프로비엠","003670":"포스코퓨처엠",
    "000270":"기아",
    "105560":"KB금융",    "055550":"신한지주",   "086790":"하나금융",  "138040":"메리츠금융",
    "316140":"우리금융",  "032640":"LG유플러스",
    "064350":"현대로템",  "042660":"한화오션",   "272210":"한화시스템",
    "329180":"HD현대중공업","011200":"HMM",
    "015760":"한국전력",  "034020":"두산에너빌", "010950":"에쓰오일",  "010140":"삼성중공업",
    "066570":"LG전자",    "017670":"SK텔레콤",   "030200":"KT",
    "000720":"현대건설",  "003490":"대한항공",   "004020":"현대제철",
    "047050":"포스코인터내셔널","078930":"GS",
    "000150":"두산",      "000880":"한화",
}

WATCHLIST = [
    # 반도체 (2)
    "005930",  # 삼성전자            ~7만  (거래량 1위)
    "084850",  # 아이티엠반도체      ~8만  ★실적+1,534원
    # 2차전지 (4) — 모멘텀 강한 테마
    "086520",  # 에코프로            ~6만
    "247540",  # 에코프로비엠        가격변동
    "003670",  # 포스코퓨처엠        가격변동
    # "066970",  # 엘앤에프 — 반복손절(3회) 제거
    # 자동차 (1)
    "000270",  # 기아                ~8만
    # 금융 (6) — 시장 상승 시 추종
    "105560",  # KB금융              ~7만
    "055550",  # 신한지주            ~4만
    "086790",  # 하나금융지주        ~6만
    "138040",  # 메리츠금융지주      ~8만
    "316140",  # 우리금융지주        ~1.5만 (저가 금융, 주수 극대화)
    "032640",  # LG유플러스          ~1만   (저가 통신, 주수 극대화)
    # 방산/조선/해운 (5) — 모멘텀 강한 테마
    "064350",  # 현대로템        ~5만  (방산)
    "042660",  # 한화오션        ~6만  (조선)
    "272210",  # 한화시스템      ~2만  (방산 IT)
    "329180",  # HD현대중공업    가격변동 (조선)
    "011200",  # HMM             ~1.5만 (해운, 고변동성)
    # 에너지 (4)
    "015760",  # 한국전력            ~2만  ★실적+1,417원
    "034020",  # 두산에너빌리티      ~9만 (원자력 테마로 급등, 가격 갱신 06/25)
    "010950",  # 에쓰오일            ~6만
    "010140",  # 삼성중공업          ~1만  (초저가 조선 테마, HPSP 대체)
    # IT/통신 + 가전 (4)
    "066570",  # LG전자              ~7만  (고유동성 IT, 카카오 대체)
    "017670",  # SK텔레콤            ~9만 (가격 갱신 06/25)
    "030200",  # KT                  ~3만
    "047050",  # 포스코인터내셔널    ~6만  (에너지무역, 삼성생명 대체)
    # 저가 대형주 (4)
    "000720",  # 현대건설            ~3만
    "003490",  # 대한항공            ~2만  ★실적+1,649원
    "004020",  # 현대제철            ~3만  ★실적+773원
    "078930",  # GS                  ~4만  ★실적+62원
    # 그룹 지주 (2) — 테마 확산 수혜
    "000150",  # 두산                ~2만  (두산에너빌 모회사)
    "000880",  # 한화                ~3만  (한화오션·한화시스템 모회사)
]  # 총 30개

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

def load_kchg_history():
    today = datetime.now().strftime("%Y-%m-%d")
    if os.path.exists(KCHG_HIST_FILE):
        try:
            d = json.load(open(KCHG_HIST_FILE))
            if d.get("date") == today:
                return d.get("hist", [])
        except Exception:
            pass
    return []

def save_kchg_history(hist):
    today = datetime.now().strftime("%Y-%m-%d")
    json.dump({"date": today, "hist": hist}, open(KCHG_HIST_FILE, "w"))

def load_daily_risk():
    today = datetime.now().strftime("%Y-%m-%d")
    if os.path.exists(DAILY_RISK_FILE):
        try:
            d = json.load(open(DAILY_RISK_FILE))
            if d.get("date") == today:
                return {"streak": d.get("streak", 0), "loss_sum": d.get("loss_sum", 0.0),
                        "buy_count": d.get("buy_count", 0),
                        "exit_stats": d.get("exit_stats", {"stop": 0, "trail": 0, "target": 0, "signal": 0})}
        except Exception:
            pass
    return {"streak": 0, "loss_sum": 0.0, "buy_count": 0,
            "exit_stats": {"stop": 0, "trail": 0, "target": 0, "signal": 0}}

def save_daily_risk(streak, loss_sum, buy_count=0, exit_stats=None):
    today = datetime.now().strftime("%Y-%m-%d")
    json.dump({"date": today, "streak": streak, "loss_sum": loss_sum,
               "buy_count": buy_count, "exit_stats": exit_stats or {}},
              open(DAILY_RISK_FILE, "w"))

def load_cooldown() -> float:
    """손절 후 쿨다운 종료 타임스탬프. 0이면 쿨다운 없음."""
    today = datetime.now().strftime("%Y-%m-%d")
    if os.path.exists(COOLDOWN_FILE):
        try:
            d = json.load(open(COOLDOWN_FILE))
            if d.get("date") == today:
                return float(d.get("until_ts", 0))
        except Exception:
            pass
    return 0.0

def save_cooldown(until_ts: float):
    today = datetime.now().strftime("%Y-%m-%d")
    json.dump({"date": today, "until_ts": until_ts}, open(COOLDOWN_FILE, "w"))

def load_overnight_hold():
    if os.path.exists(OVERNIGHT_FILE):
        try:
            d = json.load(open(OVERNIGHT_FILE))
            return set(d.get("codes", []))
        except Exception:
            pass
    return set()

def save_overnight_hold(codes):
    today = datetime.now().strftime("%Y-%m-%d")
    json.dump({"date": today, "codes": list(codes)}, open(OVERNIGHT_FILE, "w"))

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
    # 같은 에러 타입은 30분에 한 번만 Discord 전송 (530 서버 다운 등 반복 에러 폭탄 방지)
    now_ts = datetime.now().timestamp()
    suppressed: dict = {}
    if os.path.exists(ERROR_SUPPRESS_FILE):
        try:
            suppressed = json.load(open(ERROR_SUPPRESS_FILE))
        except Exception:
            pass
    key = context[:60]
    if now_ts - suppressed.get(key, 0) < 1800:
        print(f"🔕 [{datetime.now().strftime('%H:%M:%S')}] 에러(억제) — {context}: {err[:80]}")
        return
    suppressed[key] = now_ts
    suppressed = {k: v for k, v in suppressed.items() if now_ts - v < 7200}
    try:
        json.dump(suppressed, open(ERROR_SUPPRESS_FILE, "w"))
    except Exception:
        pass
    now_s = datetime.now().strftime("%H:%M:%S")
    msg = f"🚨 [{now_s}] 에러 — {context}\n{err[:500]}"
    print(msg)
    send_discord(msg)

def build_discord_msg(now_str, cash, k_rsi, k_chg, overheated, k_dead, buy_min,
                      holdings, analysis, actions, locked_codes=frozenset()):
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
        parts = [f"{h.get('stock_name','?')[:4]} {h.get('profit_rate',0):+.1f}%{'🔒' if c in locked_codes else ''}"
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

def has_momentum_signal(buy_conds: list) -> bool:
    """수급/모멘텀 신호 1개 이상 → 순수 기술적 신호(RSI·MA·BB)만으로 진입 금지."""
    momentum_kw = ("강세", "역행", "거래량", "연속상승", "고점돌파", "볼륨스파이크", "볼륨급증", "VCP", "양봉")
    return any(any(kw in cond for kw in momentum_kw) for cond in buy_conds)


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
        f_acct   = ex.submit(get, "/kiwoom/account")
        f_k200   = ex.submit(get, "/kiwoom/indicators/069500")
        f_locked = ex.submit(get, "/kiwoom/locked")
        q_futs  = {ex.submit(fetch_quote, c): c for c in WATCHLIST}
        acct = f_acct.result(); k200 = f_k200.result()
        locked_resp = f_locked.result()
        if isinstance(locked_resp, list):
            locked_codes = set(locked_resp)
        elif isinstance(locked_resp, dict) and "_err" not in locked_resp:
            locked_codes = set(locked_resp.get("locked") or locked_resp.get("locked_stocks") or [])
        else:
            locked_codes = set()
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
    api_error = "_err" in acct or "_err" in k200   # 핵심 API 오류 시 매수 신뢰 불가
    k_rsi   = float(k200.get("rsi_14")      or 50) if "_err" not in k200 else 50
    k_chg   = float(k200.get("change_rate") or 0)  if "_err" not in k200 else 0
    k_ma5   = float(k200.get("ma_5")        or 0)  if "_err" not in k200 else 0
    k_ma20  = float(k200.get("ma_20")       or 0)  if "_err" not in k200 else 0
    k_dead  = k_ma5 > 0 and k_ma20 > 0 and k_ma5 < k_ma20   # KODEX200 MA 데드크로스
    overheated = k_chg <= -1.5 or k_rsi >= 80   # bear_market 기준(-1.5%)과 통일
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
        lock_tag = " 🔒잠금됨" if code in locked_codes else ""
        print(f"📦 보유 {h.get('stock_name','?')}({code}) {h.get('quantity',0)}주 {pr:+.1f}%{warn}{lock_tag}")

    # 랭킹 기반 비검증 종목 매수는 -15.5% 등 대형 손실 원인이었음 → 워치리스트로만 제한
    all_codes = list(dict.fromkeys(WATCHLIST + list(holdings.keys())))

    # 워치리스트 밖 보유종목(과거 랭킹 매수 잔량 등) quote 보충 조회
    extra = [c for c in holdings if c not in quotes]
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

    top10 = sorted([c for c in bid_scores if c not in holdings],
                   key=bid_scores.get, reverse=True)[:10]
    ind_codes = list(dict.fromkeys(list(holdings.keys()) + top10))

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
                if abs(chg) < 0.01 and abs(chg_real) > 0.1:  # 일봉=0%인데 실제 움직임 있으면 보정
                    chg = chg_real

        # ── 매수 신호 ──────────────────────────────────────────────
        buy_score = 0
        buy_conds = []

        # ① 주가 모멘텀 (시장 대비 상대강도)
        # 시장이 급락 중(-1.5%↓)일 땐 상대강도가 분모 효과로 부풀려짐 → 절대 상승 요구
        rel_str = chg - k_chg
        if k_chg <= -1.5:
            if   chg >= 0.5: buy_score += 2; buy_conds.append(f"역행+{chg:.1f}%★(급락장)")
            elif chg >= 0.0: buy_score += 1; buy_conds.append(f"버팀+{chg:.1f}%(급락장)")
        elif rel_str >= 1.5: buy_score += 3; buy_conds.append(f"강세+{rel_str:.1f}%★★")
        elif rel_str >= 0.3: buy_score += 2; buy_conds.append(f"강세+{rel_str:.1f}%★")
        elif chg >= 0.5:     buy_score += 1; buy_conds.append(f"상승+{chg:.1f}%")

        # ② 거래량
        if   vol >= 3.0: buy_score += 3; buy_conds.append(f"거래량{vol:.1f}x★★")
        elif vol >= 1.5: buy_score += 2; buy_conds.append(f"거래량{vol:.1f}x★")
        elif vol >= 1.0: buy_score += 1; buy_conds.append(f"거래량{vol:.1f}x")

        # ③ 호가비 (매수세 > 매도세)
        if   bid_r >= 2.0: buy_score += 2; buy_conds.append(f"호가{bid_r:.1f}★")
        elif bid_r >= 1.3: buy_score += 1; buy_conds.append(f"호가{bid_r:.1f}")

        # ④ 체결강도 (100=균형, 130+=매수 우세)
        if   brt >= 130: buy_score += 2; buy_conds.append(f"체결{brt:.0f}★")
        elif brt >= 110: buy_score += 1; buy_conds.append(f"체결{brt:.0f}")

        # ⑤ RSI 건강 구간
        if rsi_use and 45 <= rsi_use <= 65:
                             buy_score += 1; buy_conds.append(f"RSI{rsi_use:.0f}")

        # ⑥ 5분봉 연속 상승 (모멘텀 지속)
        if len(candles) >= 3:
            cls3 = [float(c.get("close", 0)) for c in candles[-3:]]
            if all(v > 0 for v in cls3) and cls3[0] < cls3[1] < cls3[2]:
                buy_score += 1; buy_conds.append("연속상승")

        # ⑦ 5분봉 최근 10봉 고점 돌파 (브레이크아웃 핵심 신호)
        if len(closes_5m) >= 11:
            recent_high = max(closes_5m[-11:-1])
            if recent_high > 0 and close > recent_high * 1.002:
                buy_score += 2; buy_conds.append("고점돌파★")
            elif recent_high > 0 and close > recent_high:
                buy_score += 1; buy_conds.append("고점돌파")

        # ⑧ 5분봉 거래량 스파이크 (최근 5봉 평균 대비 현재봉)
        vols_5m = [float(c.get("volume", 0)) for c in candles]
        if len(vols_5m) >= 6:
            avg_vol = sum(vols_5m[-6:-1]) / 5
            last_vol = vols_5m[-1]
            if avg_vol > 0:
                if   last_vol >= avg_vol * 2.0: buy_score += 2; buy_conds.append("볼륨스파이크★")
                elif last_vol >= avg_vol * 1.5: buy_score += 1; buy_conds.append("볼륨급증")

        # ⑨ MA 골든크로스 (5분봉 MA5 > MA20)
        if ma5_use and ma20_use and ma5_use > ma20_use:
            buy_score += 1; buy_conds.append("MA골든")

        # ⑩ BB 하단 반등 (지지선에서 매수 — 눌림목 단타)
        if bb_l_use and close <= bb_l_use * 1.005 and chg >= 0:
            buy_score += 1; buy_conds.append("BB반등")

        # ⑪ MA20 상승추세 (Minervini Stage 2 — MA20이 우상향 = 기관 지속 매수 중)
        # 25봉 이상 있을 때만: 현재 MA20 vs 5봉 전 MA20 비교
        if len(closes_5m) >= 25:
            ma20_prev5 = calc_ma(closes_5m[-25:-5], 20)
            if ma20_use and ma20_prev5 and ma20_use > ma20_prev5 * 1.0005:
                buy_score += 1; buy_conds.append("MA20상승")

        # ⑫ VCP (Volatility Contraction Pattern) — Minervini/O'Neil 핵심 진입 패턴
        # 볼륨 수축(매도 압력 소진) 후 돌파 시 급증 = 기관 매집 완료 후 브레이크아웃
        if len(vols_5m) >= 8:
            mid_avg  = sum(vols_5m[-7:-3]) / 4   # 3~7봉 전 평균 (기준, rec_avg와 겹침 없음)
            rec_avg  = (sum(vols_5m[-3:-1]) / 2) if len(vols_5m) >= 3 else mid_avg
            curr_vol = vols_5m[-1]
            if mid_avg > 0:
                if rec_avg < mid_avg * 0.75 and curr_vol > rec_avg * 2.0 and curr_vol > mid_avg:
                    buy_score += 2; buy_conds.append("VCP★")   # 강한 수축 후 폭발적 돌파
                elif rec_avg < mid_avg * 0.85 and curr_vol > rec_avg * 1.5:
                    buy_score += 1; buy_conds.append("VCP")     # 약한 수축 후 확장

        # ⑬ 양봉 매집 (O'Neil CANSLIM "S" — Supply/Demand)
        # 최근 8봉 중 6봉 이상 양봉(close>open) = 기관이 조용히 지속 매수 중
        if len(candles) >= 8:
            up_cnt = sum(1 for c in candles[-8:]
                         if float(c.get("close", 0)) > float(c.get("open", 0)))
            if up_cnt >= 6:
                buy_score += 1; buy_conds.append(f"양봉{up_cnt}/8")

        # ── 수익 목표: 기대값 분석 결과 수익을 더 길게 가져가야 거래비용 초과 가능
        # 평균수익이 최소 +1.67% 되어야 EV>0 → 기존 2/3/4% → 2.5/3.5/4.5%로 상향
        if   buy_score >= 10: exit_target = 4.5
        elif buy_score >= 7:  exit_target = 3.5
        else:                 exit_target = 2.5
        sell_conds = []
        if code in holdings and code not in locked_codes:   # 잠긴 종목은 매도 신호 계산 자체를 스킵
            if pr is not None:
                effective_target = exit_targets_store.get(code, exit_target)
                if pr >= effective_target:
                    sell_conds.append(f"수익+{pr:.1f}%(목표{effective_target:.1f}%)")
            # RSI매도: 목표가 절반 이상 달성(pr>=1.0)했을 때만 — 너무 이른 RSI 매도가 평균수익을 깎음
            if rsi_use  and rsi_use  >= 72 and pr is not None and pr >= 1.0: sell_conds.append(f"RSI{rsi_use:.0f}")
            if chg      and chg      >= 5.0 and pr is not None and pr >= 1.5: sell_conds.append(f"급등+{chg:.1f}%")
            # BB상단/MA데드: 수익이 충분히 났을 때만 — 0.3%에서 MA데드로 팔면 비용만 남음
            if bb_u_use and close    >= bb_u_use and pr is not None and pr >= 1.5: sell_conds.append("BB상단")
            if ma5_use  and ma20_use and ma5_use < ma20_use and pr is not None and pr >= 0.8: sell_conds.append("MA데드")

        s = len(sell_conds) if code in holdings else 0
        src = "5분" if rsi_5m is not None else "일봉"
        print(f"{code:<8}{name:<8}{price:>8,}  {rsi_use:>5.1f} {chg:>+7.1f}% {vol:>6.1f}x {bb_pos:>6} {bid_r:>5.2f}  {buy_score}pt/{s} [{src}]")
        analysis[code] = dict(name=name, price=price, pr=pr, rsi=rsi_use, chg=chg,
                               vol=vol, bid_r=bid_r, bb_pos=bb_pos, ma_sig=ma_sig,
                               brt=brt, exit_target=exit_target, buy_score=buy_score,
                               buy_conds=buy_conds, sell_conds=sell_conds,
                               ma20_daily=ma20)

    print("\n" + "═"*50)
    actions = []

    # 하락장 감지 (SELL 판단에도 사용 — bear_market 중엔 손절선 강화) — 공격적 모드: 기준 완화
    bear_market = (k_chg <= -1.5 or (k_dead and k_rsi < 35))
    bear_reason = (f"KODEX200 {k_chg:+.1f}% 급락" if k_chg <= -1.5
                   else f"KODEX200 MA데드+RSI{k_rsi:.0f} 하락추세")

    # 지수 롤오버 감지: 최근 30분 고점 대비 1.5%p 이상 빠지면 "오전 펌핑 후 꺾임"으로 판단
    # (개별 종목 반등신호만 보고 꺾이는 지수에 계속 추가매수하다 손실 반복하는 패턴 차단)
    kchg_hist = load_kchg_history()
    now_ts = now.timestamp()
    kchg_hist.append({"t": now_ts, "v": k_chg})
    kchg_hist = [p for p in kchg_hist if now_ts - p["t"] <= 1800]  # 최근 30분만 유지
    save_kchg_history(kchg_hist)
    k_chg_peak30 = max((p["v"] for p in kchg_hist), default=k_chg)
    rollover = (k_chg_peak30 - k_chg) >= 1.5 and not bear_market
    rollover_reason = f"지수 고점({k_chg_peak30:+.1f}%)대비 {k_chg_peak30-k_chg:.1f}%p 롤오버"

    # ── SELL 판단 ─────────────────────────────────────────────
    force_close = now.hour > 15 or (now.hour == 15 and now.minute >= 10)
    if force_close:
        print("🕐 15:10 이후 — 장 마감 청산 모드")

    # 전일 잔류 포지션 감지: 9:00~9:20 사이에 보유종목이 있으면 WSL 중단 등으로
    # 어제 못 판 것 → 오버나이트 리스크 즉시 제거 (단, 어제 의도적으로 오버나이트 보유 결정한
    # 종목은 제외 — 안 그러면 "수익+1.5%면 보유" 기능이 다음날 아침 무조건 청산으로 무효화됨)
    overnight_stale = (now.hour == 9 and now.minute <= 20)
    intentional_overnight = load_overnight_hold()
    if overnight_stale and holdings:
        print("⏰ 9:20 이전 보유종목 감지 — 의도적 오버나이트 보유 외 즉시 청산")
    overnight_holds_today = set()

    _exit_types: list = []   # 당일 청산 유형 집계용 (stop/trail/target/signal)

    for code, d in analysis.items():
        if code not in holdings: continue
        if code in locked_codes:
            print(f"🔒 {d['name']}({code}) 잠금 — 매도 평가 스킵")
            continue
        pr   = d["pr"] or 0
        name = d["name"]
        sc   = d["sell_conds"]
        is_intentional_overnight = overnight_stale and code in intentional_overnight

        # 최고 수익 갱신 (트레일링 스탑 추적)
        if d["pr"] is not None:
            peaks[code] = max(peaks.get(code, d["pr"]), d["pr"])

        # 전일 잔류 포지션은 손익 무관 즉시 청산 — 의도적 오버나이트 보유는 예외
        if overnight_stale and not is_intentional_overnight:
            d["sell_conds"] = sc + ["전일잔류청산"]
            sc = d["sell_conds"]

        # 비상손절 — 거래비용(0.21%)을 감안, 손실을 더 빠르게 끊어야 기대값이 양수가 됨
        # -0.8% 기준: 왕복 비용 0.21% 고려 시 실질 손실 ~1.0% = 목표수익 2.5%의 2.5배 손익비
        stop_pct = -0.5 if (bear_market or rollover) else -0.8
        emergency_stop = (d["pr"] is not None and d["pr"] <= stop_pct)
        # 트레일링: 목표가 2.5%에 맞춰 +1.2% 이후 -0.8%p 반락 시 청산 (조기 이익 보호)
        # 기존 +1.5%/−1.0%p → 목표가 2.5%의 절반 수준에서 보호 시작
        trailing_stop  = (peaks.get(code, 0) >= 1.2 and d["pr"] is not None
                          and d["pr"] < peaks.get(code, 0) - 0.8
                          and d["pr"] >= 0.4)
        exit_tgt    = exit_targets_store.get(code, d.get("exit_target", 2.0))
        profit_exit = (d["pr"] is not None and d["pr"] >= exit_tgt)
        sell_min = 1 if profit_exit else 2

        # 트레일링: 장중 +2% 도달 후 0% 미만 반락 시 즉시 매도
        if trailing_stop and not emergency_stop:
            d["sell_conds"] = sc + [f"트레일링(최고{peaks[code]:+.1f}%)"]
            sc = d["sell_conds"]

        # 15:10 이후: 수익 +1.5% 이상이면 오버나이트 보유, 미만이면 무조건 청산
        # (마감청산은 sell_min 충족 여부와 무관하게 강제 — 안 그러면 신호 부족으로 안 팔리고
        #  그대로 밤새 남아 다음날 9시 전일잔류청산으로 더 나쁜 가격에 팔리는 사고가 났었음)
        force_close_liquidate = False
        if force_close and not emergency_stop and not trailing_stop and len(sc) < sell_min:
            if d["pr"] is not None and d["pr"] >= 1.5:
                print(f"💤 오버나이트 보유 {name}({code}) {pr:+.1f}% ≥ 1.5% — 내일 계속 관리")
                overnight_holds_today.add(code)
            else:
                d["sell_conds"] = sc + ["마감청산"]
                sc = d["sell_conds"]
                force_close_liquidate = True

        if emergency_stop or trailing_stop or force_close_liquidate or len(sc) >= sell_min:
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
                _exit_types.append("stop" if emergency_stop else "trail" if trailing_stop else
                                   "target" if profit_exit else "signal")
                add_sold_today(code)   # 수익/손실 무관하게 당일 재매수 금지
                # 비상손절 후 30분 쿨다운 — 나쁜 진입이었음을 인정하고 시장 재확인
                if emergency_stop:
                    cd_until = datetime.now().timestamp() + 1800
                    save_cooldown(cd_until)
                    print(f"⏸️  손절 쿨다운 발동 → {datetime.fromtimestamp(cd_until).strftime('%H:%M')}까지 신규 매수 차단")
                peaks.pop(code, None)
                exit_targets_store.pop(code, None)
                save_exit_targets(exit_targets_store)
        elif d["pr"] is not None and d["pr"] <= stop_pct + 0.3:
            print(f"⚠️  손실 주의 {name}({code}) {pr:+.1f}% (비상손절 {stop_pct:.1f}% 임박)")

    # 마감 시점에 결정된 "내일까지 보유" 목록을 저장 (다음날 9:00~9:20 강제청산에서 제외됨)
    if force_close:
        save_overnight_hold(overnight_holds_today)

    # ── 연속손실/일일손실 한도 — 진짜 고수는 나쁜 날엔 스스로 멈춤 ──
    daily_risk = load_daily_risk()
    streak, loss_sum, buy_count = daily_risk["streak"], daily_risk["loss_sum"], daily_risk["buy_count"]
    exit_stats = daily_risk["exit_stats"]

    # 손절 쿨다운 — 비상손절 후 30분간 신규 매수 차단 (연속 손절 패턴 방지)
    cooldown_ts = load_cooldown()
    in_cooldown = cooldown_ts > datetime.now().timestamp()

    for act, code, name, pr, d in actions:
        if act != "SELL" or pr is None: continue
        if pr < 0:
            streak += 1
            loss_sum += pr
        else:
            streak = 0
    for etype in _exit_types:
        exit_stats[etype] = exit_stats.get(etype, 0) + 1
    save_daily_risk(streak, loss_sum, buy_count, exit_stats)
    risk_halt = streak >= 3 or loss_sum <= -4.0 or buy_count >= MAX_DAILY_BUYS
    risk_halt_reason = (f"연속손실{streak}회" if streak >= 3
                        else f"일일매수{buy_count}회한도({MAX_DAILY_BUYS}회)" if buy_count >= MAX_DAILY_BUYS
                        else f"일일누적손실{loss_sum:.1f}%p")

    # ── BUY 판단 ─────────────────────────────────────────────
    sold_codes     = {code for act, code, *_ in actions if act == "SELL"}
    # ── 매수 필터 (시간대 / 지수 방향 / 진입 제한) ──────────────
    # ── 매수 시간대 (시장 미시구조 기반) ─────────────────────────
    # 9:20~10:30 황금타임: 거래량 최고, 모멘텀 명확          → 3pt
    # 10:30~11:30 오전후반: 모멘텀 지속, 약간 엄격           → 4pt
    # 11:30~13:30 점심: 거래량 급감·허수 많음 → 완전금지 대신 강신호만(6pt)
    # 13:30~14:30 오후재개                                   → 4pt
    # 14:30~15:00 마감 직전 → 완전금지 대신 강신호만(6pt)
    # 15:00~      장 마감                                     → 매수 금지
    h, m = now.hour, now.minute
    no_buy_time = (
        (h < 9) or
        (h == 9 and m < 20) or    # 9:20 이전만 금지 (시초가 안정 후 바로 진입)
        (h >= 15)
    )
    gold_window      = not no_buy_time and (h == 9 or (h == 10 and m < 30))
    late_morning     = not no_buy_time and ((h == 10 and m >= 30) or (h == 11 and m < 30))
    afternoon_window = not no_buy_time and ((h == 13 and m >= 30) or (h == 14 and m < 30))
    lunch_window     = not no_buy_time and ((h == 11 and m >= 30) or h == 12 or (h == 13 and m < 30))
    late_window      = not no_buy_time and (h == 14 and m >= 30)

    if gold_window:         effective_buy_min = buy_min          # 3pt
    elif late_morning:      effective_buy_min = buy_min + 1      # 4pt
    elif afternoon_window:  effective_buy_min = buy_min + 1      # 4pt
    elif lunch_window:      effective_buy_min = buy_min + 3      # 6pt — 점심엔 강신호만
    elif late_window:       effective_buy_min = buy_min + 3      # 6pt — 마감직전 강신호만
    else:                   effective_buy_min = buy_min + 2

    # KODEX RSI 약세 구간: 기준 +2pt 가중 — 7/8, 7/10 RSI33 같은 약세장엔 강신호만 진입
    # 기대값 분석에서 RSI<45인 날 대부분 손절 → 고점수 신호만 허용
    if k_rsi < 45 and not bear_market:
        if k_chg < 1.0:  # 약세 지속 → 보수적
            effective_buy_min += 2
            print(f"📊 KODEX RSI{k_rsi:.0f} 약세({k_chg:+.1f}%) — 매수기준 {effective_buy_min}pt로 상향")
        else:  # 과매도 반등장 → 기준 유지 (7/21 RSI30/+3.8% 같은 날 허용)
            print(f"📊 KODEX RSI{k_rsi:.0f} 과매도 반등{k_chg:+.1f}% — 기준 유지")

    block_reason = None
    if no_buy_time:
        if h < 9:                  reason_nb = "장 미개장 (9:00 전)"
        elif h == 9 and m < 20:    reason_nb = "시초가 혼조 (9:20 전)"
        else:                      reason_nb = "장 마감 (15:00 이후)"
        print(f"⏰ {reason_nb} — 신규 매수 중단")
        block_reason = reason_nb
        buy_candidates = []
    elif api_error:
        print(f"⚠️ API 오류 (계좌/시세 신뢰 불가) — 신규 매수 중단")
        block_reason = "API 오류(계좌/시세 신뢰 불가)"
        buy_candidates = []
    elif bear_market:
        print(f"📉 {bear_reason} — 신규 매수 중단")
        block_reason = bear_reason
        buy_candidates = []
    elif rollover:
        print(f"📉 {rollover_reason} — 신규 매수 중단")
        block_reason = rollover_reason
        buy_candidates = []
    elif risk_halt:
        print(f"🛑 {risk_halt_reason} — 오늘 신규 매수 중단 (내일 리셋)")
        block_reason = risk_halt_reason
        buy_candidates = []
    elif in_cooldown:
        cd_str = datetime.fromtimestamp(cooldown_ts).strftime("%H:%M")
        print(f"⏸️  손절 쿨다운 중 ({cd_str}까지) — 시장 재확인 대기")
        block_reason = f"손절쿨다운({cd_str}까지)"
        buy_candidates = []
    elif afternoon_window and len(holdings) >= 4:
        print(f"⏰ 오후 재개 — 보유 4종목 이상 → 신규 매수 중단")
        block_reason = "오후 재개·보유 4종목↑"
        buy_candidates = []
    else:
        buy_candidates = [(c, d) for c, d in analysis.items()
                          if d.get("buy_score", 0) >= effective_buy_min
                          and c not in sold_codes             # 이번 세션 매도 종목 재매수 금지
                          and c not in sold_today_codes       # 오늘 이전 세션 매도 종목 재매수 금지
                          and c not in holdings
                          and c not in exit_targets_store     # 오늘 이미 매수 접수한 종목 중복 매수 방지
                          and len(d["sell_conds"]) == 0
                          and d["chg"] >= (0.3 if (k_chg >= 1.5 and d.get("buy_score",0) < 4) else -0.5)
                          and d["chg"] <= (5.0 if d.get("buy_score", 0) >= 7 else 3.0)  # 오버익스텐션 방지
                          # ── 고수 규칙 (기대값 분석 기반) ────────────────────────
                          and d["rsi"] >= 35                  # RSI 35 미만 = 아직 하락 중, 바닥 잡기 금지
                          and d["rsi"] < 75                   # RSI 75 이상 = 과매수, 천장 매수 금지
                          and (k_chg >= 0.0 or d.get("buy_score", 0) >= 7)  # 지수 하락 중엔 강신호만
                          and (d["vol"] >= 1.0 or d["bid_r"] >= 2.0)        # 수급 확인 필수: 평균거래량 이상(1.0x) or 강매수세
                          and has_momentum_signal(d["buy_conds"])            # 모멘텀 신호 1개 이상 필수
                          # 애널리스트 진입 원칙: 주가가 MA20 위 = 추세 상승 중인 종목만 매수
                          # (MA20 아래는 하락추세로 간주 — 반등을 기대하는 바닥 잡기 금지)
                          and (analysis[c].get("ma_sig") == "골"              # MA5>MA20 = 단기 골든크로스
                               or d.get("buy_score", 0) >= 8)                # 강신호는 예외 허용
                          # 일봉 MA20 아래 하락추세 종목 진입 금지
                          # ma20_daily=0(데이터 없음)이면 보수적으로 차단 (단, 강신호 9pt+ 예외)
                          and (d.get("buy_score", 0) >= 9
                               or (d.get("ma20_daily", 0) > 0 and d["price"] >= d.get("ma20_daily", 0) * 0.97))
                          # ───────────────────────────────────────────────────────
                          and k_chg >= -1.5                   # bear_market 기준과 통일
                          and d["bid_r"] <= 15.0
                          and len(holdings) < 5]

    # 점수 충분하지만 필터에 막힌 종목 이유 출력
    for c, d in analysis.items():
        sc = d.get("buy_score", 0)
        if sc < effective_buy_min or c in holdings or no_buy_time or bear_market or rollover or risk_halt or api_error: continue
        reasons = []
        if c in sold_codes or c in sold_today_codes: reasons.append("당일매도(재매수금지)")
        if c in exit_targets_store:                  reasons.append("오늘매수완료(중복방지)")
        if d["chg"] < (0.3 if (k_chg >= 1.5 and sc < 4) else -0.5): reasons.append(f"chg{d['chg']:+.1f}%<기준")
        if d["chg"] > (5.0 if sc >= 7 else 3.0):    reasons.append(f"오버익스텐션chg{d['chg']:+.1f}%")
        if d["rsi"] <= 0:                            reasons.append("RSI=0(데이터없음)")
        if d["rsi"] < 35:                            reasons.append(f"RSI하락중{d['rsi']:.0f}(바닥잡기금지)")
        if d["rsi"] >= 75:                           reasons.append(f"RSI과열{d['rsi']:.0f}(천장매수금지)")
        if k_chg < 0.0 and sc < 7:                  reasons.append(f"지수하락중(k_chg{k_chg:+.1f}%,강신호7pt미만)")
        if d["vol"] < 1.0 and d["bid_r"] < 2.0:     reasons.append(f"수급부족(vol{d['vol']:.1f}x<1.0,호가{d['bid_r']:.1f})")
        if not has_momentum_signal(d["buy_conds"]):  reasons.append("모멘텀신호없음(기술적만)")
        if d["bid_r"] > 15.0:                        reasons.append(f"호가비이상{d['bid_r']:.1f}")
        if len(holdings) >= 5:                       reasons.append("보유5종목만")
        if reasons:
            print(f"🚫 {d['name']}({c}) {sc}pt 차단: {' / '.join(reasons)}")

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
            # 점수 내림차순 → 동점 시 저가 종목 우선 (소액 자본에서 주수 극대화)
            valid_buys.sort(key=lambda x: (x[2], -analysis[x[0]]["price"]), reverse=True)
            valid_buys = valid_buys[:1]
            for code, d, n, dart_summary in valid_buys:
                name = d["name"]
                # 점수 기반 신뢰도: 3점→0.70, 5점→0.80, 7점→0.88, 9점↑→0.94
                conf = min(0.70 + (n - 3) * 0.04, 0.94)
                # 점수 기반 비율: 3pt→45%, +1pt당+8%, 최대 90%
                sig_ratio = min(0.45 + (n - 3) * 0.08, 0.90)
                if   len(valid_buys) >= 4: ratio = min(sig_ratio, 0.45)
                elif len(valid_buys) == 3: ratio = min(sig_ratio, 0.55)
                elif len(valid_buys) == 2: ratio = min(sig_ratio, 0.65)
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
                    buy_count += 1
                    save_daily_risk(streak, loss_sum, buy_count, exit_stats)
                    print(f"📊 오늘 매수 {buy_count}/{MAX_DAILY_BUYS}회")

    # 당일 peaks 저장 (매도 완료 종목 제외 후 저장)
    save_peaks(peaks)

    if not actions:
        print("→ 전종목 HOLD")

    # ── Discord ───────────────────────────────────────────────
    if not actions:
        hold = " ".join(f"{h.get('stock_name','?')[:4]}({c}) {h.get('profit_rate',0):+.1f}%{'🔒' if c in locked_codes else ''}"
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
        block_tag = f" | 🚫매수중단:{block_reason}" if block_reason else ""
        es = exit_stats
        total_exits = sum(es.values())
        exit_tag = (f" | 청산통계 손절{es.get('stop',0)}/트레일{es.get('trail',0)}"
                    f"/목표가{es.get('target',0)}/신호{es.get('signal',0)}"
                    if total_exits > 0 else "")
        msg = f"[{now_str}] HOLD | {market} | 보유:{hold}{cash_warn}{block_tag}{exit_tag}{hint}"
        send_discord(msg)
    else:
        msg = build_discord_msg(now_str, cash, k_rsi, k_chg, overheated, k_dead,
                                buy_min, holdings, analysis, actions, locked_codes)
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
