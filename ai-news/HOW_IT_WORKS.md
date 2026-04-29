# ai-news 자동매매 구조 (레퍼런스 버전)

AI가 뉴스 검색 + 매매 판단 + 주문까지 전담하는 순수 AI 방식.
scan.py는 데이터만 출력하고, 판단은 전부 AI(gpt-4.1-mini)가 담당.

> **참고:** 이 버전은 레퍼런스용. 현재 프로덕션은 루트 `openclaw-setup/`의 하이브리드 방식 사용.

## 구성 요소

```
OpenClaw (cron)
    └─ gpt-4.1-mini (AI 에이전트)
            ├─ scan.py (데이터 출력만)
            │       └─ your-api-server.example.com → 키움증권 API
            ├─ web_fetch (뉴스 검색) → 구글 뉴스 RSS
            ├─ trade.py (AI가 직접 exec)
            └─ notify.py → Discord DM
```

## 실행 흐름

### 매 실행 (9:00~15:20, 20분마다)

1. `exec: python3 scan.py` — 지표/호가/보유 데이터 출력
2. AI가 출력의 「AI 뉴스검색 대상」 종목마다:
   - `web_fetch: https://news.google.com/rss/search?q=종목명+주식&hl=ko&gl=KR`
3. 뉴스 + 기술지표 종합해서 AI 직접 판단:
   - 악재 뉴스 → BUY 제외, SELL 가중
   - 호재 뉴스 → 신호 +1 인정
4. 신호 충족 시 AI가 직접 exec:
   ```
   exec: python3 trade.py --code X --name X --action BUY/SELL --confidence X --ratio X --reason "..."
   ```
5. `exec: python3 notify.py "결과 메시지"` — Discord 전송

### scan.py 출력 형식
```
[AI 뉴스검색 대상]
- 종목명(코드): 매수신호N개 [신호목록]
- 종목명(코드): 보유 수익률+X% 매도신호N개
```

## 장단점

| 장점 | 단점 |
|------|------|
| 실시간 뉴스 반영 | AI 토큰 비용 높음 |
| 매 신호마다 뉴스 확인 | AI 오작동 가능성 |
| 뉴스 근거 Discord 전송 | 응답 느림 (web_fetch 다수) |

## openclaw-config/
- `jobs.json` — OpenClaw cron 잡 설정 템플릿 (YOUR_USERNAME, YOUR_DISCORD_USER_ID 교체 필요)
- `SOUL.md` — AI 에이전트 행동 지침

## 환경변수 (.env)
```
TRADING_SERVER_URL=https://your-api-server.example.com
SIGNAL_SECRET_KEY=...
DART_API_KEY=...
DISCORD_BOT_TOKEN=...
DISCORD_USER_ID=...
```
