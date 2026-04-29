# python-only 자동매매 구조

AI 없이 순수 Python + Linux cron으로 동작하는 버전.
뉴스 분석 없음. 기술 지표만으로 매수/매도 결정.

## 구성 요소

```
Linux cron
    └─ scan.py (Python 단독 실행)
            ├─ your-api-server.example.com (브릿지 서버) → 키움증권 API
            ├─ DART API (opendart.fss.or.kr) — 공시 조회
            └─ notify.py → Discord Bot API → DM
```

## 실행 흐름

### cron 스케줄 (cron-setup.sh로 등록)
```
50 8  * * 1-5   python3 scan.py   # 8:50 장 시작 전
*/20 9-15 * * 1-5 python3 scan.py  # 9:00~15:20 20분마다
```

### scan.py 내부 흐름

1. **데이터 수집** (ThreadPoolExecutor 병렬)
   - `GET /kiwoom/account` — 예수금, 보유종목, 수익률
   - `GET /kiwoom/indicators/069500` — KODEX200 RSI/등락률 (시장 과열 판단)
   - `GET /kiwoom/quote/{code}` — 종목별 호가
   - `GET /kiwoom/indicators/{code}` — RSI, BB, MA, 거래량비
   - DART API — 전일 이후 공시 (악재/호재 키워드 매칭)

2. **매도 판단** (보유종목, 신호 3개↑)
   - 수익률 ≥ +5% / RSI ≥ 78 / 당일 ≥ +4% / BB 상단 / 호가비 < 0.5

3. **매수 판단** (관심종목, 신호 2개↑)
   - RSI ≤ 50 / 거래량 ≥ 1.3x / 당일 ≤ -1% / BB 하단 / MA 골든 / 호가비 ≥ 1.3
   - KODEX200 과열 시 기준 3개↑로 강화
   - DART 악재 공시 종목 제외

4. **주문 실행** — `subprocess → trade.py`
   - 호가 확인 → 수량 계산 → HMAC 서명 → `POST /signal/receive`

5. **Discord 전송** — `subprocess → notify.py`

## 장단점

| 장점 | 단점 |
|------|------|
| 빠름 (API 직접 호출) | 뉴스 반영 불가 |
| 비용 없음 (AI 토큰 없음) | 돌발 공시/뉴스 대응 못함 |
| 안정적 (LLM 오작동 없음) | 기술 지표만으로 판단 |

## 환경변수 (.env)
```
TRADING_SERVER_URL=https://your-api-server.example.com
SIGNAL_SECRET_KEY=...
DART_API_KEY=...
DISCORD_BOT_TOKEN=...
DISCORD_USER_ID=...
```
