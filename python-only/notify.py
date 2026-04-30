"""
notify.py — 트레이딩 결과를 Discord DM으로 전송
사용법:
  python3 notify.py "분석 완료: HOLD 11종목"
  python3 notify.py "BUY 유진로봇 3주 체결"
"""

import sys, os, json, time, httpx
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

TOKEN   = os.getenv("DISCORD_BOT_TOKEN", "")
USER_ID = os.getenv("DISCORD_USER_ID", "752684690347130953")
CACHE   = os.path.join(os.path.dirname(__file__), ".dm_channel_cache")

if not TOKEN:
    sys.exit("❌ .env에 DISCORD_BOT_TOKEN이 없습니다.")

if len(sys.argv) < 2:
    sys.exit("사용법: python3 notify.py <메시지>")

message = " ".join(sys.argv[1:])
headers = {"Authorization": f"Bot {TOKEN}", "Content-Type": "application/json"}

# DM 채널 ID 캐시 (매번 API 호출 방지)
channel_id = None
if os.path.exists(CACHE):
    try:
        channel_id = open(CACHE).read().strip()
    except:
        pass

for attempt in range(3):
    try:
        if not channel_id:
            dm = httpx.post("https://discord.com/api/v10/users/@me/channels",
                headers=headers, json={"recipient_id": USER_ID}, timeout=15)
            dm.raise_for_status()
            channel_id = dm.json()["id"]
            open(CACHE, "w").write(channel_id)

        r = httpx.post(f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers=headers, json={"content": message}, timeout=15)
        r.raise_for_status()
        print(f"✅ Discord 전송 완료")
        sys.exit(0)
    except Exception as e:
        print(f"⚠️ 재시도 {attempt+1}/3: {e}", file=sys.stderr)
        channel_id = None  # 채널 ID 캐시 무효화 후 재시도
        if attempt < 2:
            time.sleep(2)

sys.exit(1)
