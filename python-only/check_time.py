"""
장중 여부 확인. SKIP 출력 시 exit(0), OK 출력 시 exit(0).
"""
import os, sys
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo('Asia/Seoul'))
except ImportError:
    import datetime as dt
    now = datetime.now(dt.timezone(dt.timedelta(hours=9)))

if now.weekday() >= 5:
    print('SKIP: 주말')
    sys.exit(0)

try:
    import holidays
    kr_holidays = holidays.KR(years=now.year)
    if now.date() in kr_holidays:
        print(f'SKIP: 공휴일 ({kr_holidays[now.date()]})')
        sys.exit(0)
except ImportError:
    pass

# holidays.KR에 없는 KRX 휴장일
KRX_EXTRA = {(5, 1)}  # 근로자의 날
if (now.month, now.day) in KRX_EXTRA:
    print('SKIP: KRX 휴장일 (근로자의 날)')
    sys.exit(0)

# 장종료 반복 감지 → 당일 자동 SKIP (scan.py가 기록)
CLOSED_FILE = os.path.join(os.path.dirname(__file__), ".market_closed_today")
today_str = now.strftime("%Y-%m-%d")
if os.path.exists(CLOSED_FILE):
    try:
        d, c = open(CLOSED_FILE).read().strip().split(":")
        if d == today_str and int(c) >= 5:
            print(f'SKIP: 장종료 {c}회 감지 (오늘 거래 중단)')
            sys.exit(0)
    except Exception:
        pass

t = now.hour * 100 + now.minute
if t < 900 or t > 1530:
    print(f'SKIP: 장외시간 KST {now.hour}:{now.minute:02d}')
    sys.exit(0)

print(f'OK KST {now.hour}:{now.minute:02d}')
