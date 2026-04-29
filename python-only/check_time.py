"""
장중 여부 확인. SKIP 출력 시 exit(0), OK 출력 시 exit(0).
"""
from datetime import datetime
import sys
try:
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo('Asia/Seoul'))
except ImportError:
    import datetime as dt
    now = datetime.now(dt.timezone(dt.timedelta(hours=9)))

if now.weekday() >= 5:
    print('SKIP: 주말')
    sys.exit(0)

t = now.hour * 100 + now.minute
if t < 900 or t > 1530:
    print(f'SKIP: 장외시간 KST {now.hour}:{now.minute:02d}')
    sys.exit(0)

print(f'OK KST {now.hour}:{now.minute:02d}')
