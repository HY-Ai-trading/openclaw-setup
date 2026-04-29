# 자동매매 시스템 구조 (현재 프로덕션)

Python이 신호를 계산하고, AI가 뉴스 확인 후 최종 매매를 결정하는 하이브리드 방식.

```
python-only/   — 순수 Python, AI 없음, Linux cron
ai-news/       — 레퍼런스: 순수 AI 방식 (구형)
(루트)         — 현재 사용: Python 신호 + AI 뉴스 판단 하이브리드
```

---

## 전체 구성 요소

```
OpenClaw (cron)
    └─ gpt-4.1-mini (AI 에이전트)
            ├─ scan.py — 데이터 수집 + 신호 계산 (PENDING 출력)
            │       └─ your-api-server.example.com (브릿지 서버) → 키움증권 API
            ├─ DART API (opendart.fss.or.kr) — 공시 조회
            ├─ 구글 뉴스 RSS — AI가 신호 종목만 검색
            ├─ trade.py — AI가 승인 후 exec
            └─ notify.py → Discord Bot API → DM
```

---

## 1단계 — 오전 8:50 브리핑 (AI 주도)

**트리거:** OpenClaw cron `50 8 * * 1-5` (평일 8:50 KST)

1. AI가 **이데일리 RSS** 수집
   `web_fetch: https://www.edaily.co.kr/rss/economy.xml`
   → 코스피/시장 전반 분위기 파악

2. `exec: python3 scan.py` → 보유종목 + 신호 종목 목록 확인

3. 상위 3개 종목 개별 뉴스 확인
   `web_fetch: https://news.google.com/rss/search?q=종목명+주식&hl=ko&gl=KR`

4. AI가 분석 결과를 **today_context.json** 으로 저장
   ```json
   {
     "date": "2026-04-29",
     "sentiment": "negative",
     "caution": ["001510"],
     "boost": ["078930"],
     "summary": "코스피 약세, 반도체 주의"
   }
   ```
   → 하루 종일 scan.py가 읽어서 매매 기준에 반영

5. `exec: python3 notify.py "[08:50 브리핑] ..."` → Discord 전송

---

## 2단계 — 장중 9:00~15:20 (Python 신호 + AI 판단)

**트리거:** OpenClaw cron `*/20 9-15 * * 1-5` (20분마다)

### scan.py 내부 흐름

#### 데이터 수집 (ThreadPoolExecutor 병렬)
| 호출 | 경로 | 내용 |
|------|------|------|
| 예수금/보유 | `GET /kiwoom/account` | 현금, 보유종목, 수익률 |
| KODEX200 지표 | `GET /kiwoom/indicators/069500` | 시장 RSI, 등락률 |
| 거래량/등락 상위 | `GET /kiwoom/ranking?sort_tp=1/2` | 수급 확인 |
| 종목별 호가 | `GET /kiwoom/quote/{code}` | 매수1호가, 매도1호가 |
| 종목별 지표 | `GET /kiwoom/indicators/{code}` | RSI, BB, MA, 거래량비 |

모든 API는 `TRADING_SERVER_URL`(브릿지 서버)을 통해 키움증권에 접근
인증: `X-Api-Key` 헤더

#### DART 공시 조회
- `GET https://opendart.fss.or.kr/api/list.json` — 전일 이후 공시
- 악재 키워드: `횡령/수사/영업정지/손실/파산/감사의견` → BUY 제외
- 호재 키워드: `수주/계약체결/자사주취득/흑자/실적개선` → 신호 +1

#### today_context.json 반영
| 필드 | 효과 |
|------|------|
| `sentiment: negative` | 매수 기준 신호 수 +1 (더 엄격) |
| `caution` 종목 | BUY 완전 제외, SELL 신호 2개↑로 완화 |
| `boost` 종목 | 매수 신호 +1 추가 인정 |

#### 신호 계산 후 PENDING 출력
```
PENDING_BUY:  {"code":"078930","name":"GS에너지","action":"BUY","confidence":0.75,"ratio":0.5,"reason":"RSI42 | ..."}
PENDING_SELL: {"code":"001510","name":"BYC","action":"SELL","confidence":0.70,"ratio":1.0,"reason":"수익률+5.2% | ..."}
```
→ trade.py를 직접 실행하지 않고 AI에게 넘김

### AI 판단 (PENDING 있을 때만)

```
PENDING 있음
    → 종목명으로 구글 뉴스 RSS fetch
    → 심각한 악재(횡령/수사/영업정지/파산) → SKIP
    → 그 외 → trade.py exec (PENDING 파라미터 그대로)
    → notify.py로 결과 + 뉴스 판단 근거 Discord 전송

PENDING 없음 (HOLD)
    → scan.py 출력의 DISCORD_MSG만 notify.py로 전송
```

### 주문 실행 (trade.py)
```
trade.py
    ├─ GET /kiwoom/account       (예수금 확인)
    ├─ GET /kiwoom/quote/{code}  (호가 확인)
    └─ POST /signal/receive      (지정가 주문, HMAC-SHA256 서명)
              └─ 키움증권 실제 주문
```
- BUY: 매도1호가(`sel_fpr_bid`) 기준 지정가
- SELL: 매수1호가(`buy_fpr_bid`) 기준 지정가
- 예수금 부족 / 호가 0 → 주문 취소

---

## 매매 기준 요약

### SELL (보유종목, 신호 3개↑ / caution 종목은 2개↑)
| 신호 | 기준 |
|------|------|
| 수익실현 | 수익률 ≥ +5% |
| RSI 과매수 | RSI ≥ 78 |
| 급등 | 당일 ≥ +4% |
| BB 상단 | bb_pos = 상단 |
| 호가압력 | 호가비 < 0.5 |

### BUY (관심종목, 신호 2개↑ / 과열·뉴스부정 시 3개↑)
| 신호 | 기준 |
|------|------|
| RSI 과매도 | RSI ≤ 50 |
| 거래량 급증 | ≥ 1.3x |
| 하락 반등 | 당일 ≤ -1% |
| BB 하단 | bb_pos = 하단 |
| MA 골든 | 골든크로스 |
| 호가 우위 | 호가비 ≥ 1.3 |
| 매수강도 | buy_rt ≥ 150 |

---

## 환경변수 (.env)

| 변수 | 용도 |
|------|------|
| `TRADING_SERVER_URL` | 브릿지 서버 주소 |
| `SIGNAL_SECRET_KEY` | API 인증 키 + HMAC 서명 키 |
| `DART_API_KEY` | DART 공시 API 키 |
| `DISCORD_BOT_TOKEN` | Discord Bot 토큰 |
| `DISCORD_USER_ID` | 알림 수신 Discord 유저 ID |

---

## 비용 (gpt-4.1-mini 기준)

| 잡 | 빈도 | 하루 토큰 | 하루 비용 |
|----|------|-----------|-----------|
| 8:50 브리핑 | 1회 | ~209,000 | ~110원 |
| 9:00~15:20 HOLD 시 | ~18회 | ~3,000/회 | ~20원 |
| 9:00~15:20 신호 발생 시 | ~2회 | ~15,000/회 | ~15원 |
| **합계** | | | **~145원/일 · ~3,200원/월** |

> 신호 없는 회차는 뉴스 fetch 없이 HOLD 메시지만 전송 → 비용 최소화
