"""
notify.py — 트레이딩 결과를 Discord DM으로 전송
사용법:
  python3 notify.py "분석 완료: HOLD 11종목"
  python3 notify.py "BUY 유진로봇 3주 체결"
"""

import sys, os, httpx
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

TOKEN   = os.getenv("DISCORD_BOT_TOKEN", "")
USER_ID = os.getenv("DISCORD_USER_ID", "752684690347130953")

if not TOKEN:
    sys.exit("❌ .env에 DISCORD_BOT_TOKEN이 없습니다.")

if len(sys.argv) < 2:
    sys.exit("사용법: python3 notify.py <메시지>")

message = " ".join(sys.argv[1:])

headers = {"Authorization": f"Bot {TOKEN}", "Content-Type": "application/json"}

# DM 채널 열기
dm = httpx.post("https://discord.com/api/v10/users/@me/channels",
    headers=headers, json={"recipient_id": USER_ID}, timeout=8)
dm.raise_for_status()
channel_id = dm.json()["id"]

# 메시지 전송
msg = httpx.post(f"https://discord.com/api/v10/channels/{channel_id}/messages",
    headers=headers, json={"content": message}, timeout=8)
msg.raise_for_status()
print(f"✅ Discord 전송 완료")
