# openclaw-setup

OpenClaw + 키움증권 자동매매 봇. 평일 장중 20분마다 RSI·볼린저밴드·호가비·거래량 등 기술 지표를 분석해 자동 매수/매도 주문을 실행하고, 결과를 Discord DM으로 전송합니다.

---

## 사용 방식 선택

| 폴더 | 방식 | AI 필요 | 뉴스 반영 | 비용 |
|------|------|---------|----------|------|
| `python-only/` | 순수 Python + Linux cron | ❌ | ❌ | 없음 |
| (루트) | Python 신호 + AI 뉴스 판단 | ✅ OpenClaw | ✅ 신호 발생 시 | ~3,200원/월 |
| `ai-news/` | 순수 AI (레퍼런스) | ✅ OpenClaw | ✅ 매회 | 더 높음 |

**AI 없이 쓰고 싶으면 → `python-only/` 폴더**
**OpenClaw + 뉴스 판단까지 원하면 → 루트 폴더**

---

## 폴더 구조

```
openclaw-setup/
├── scan.py              # 전 종목 분석 → PENDING 신호 출력 (AI가 최종 판단)
├── trade.py             # 지정가 주문 실행 (BUY/SELL)
├── query.py             # API 인증 래퍼 (GET/POST)
├── check_time.py        # 장중 여부 확인
├── notify.py            # Discord DM 전송
├── dart.py              # DART 공시 조회 (악재 필터)
├── today_context.json   # 8:50 AI 브리핑 저장 파일 (당일만 유효, gitignore)
├── .env.example         # 환경변수 샘플
├── HOW_IT_WORKS.md      # 상세 동작 구조
├── STRATEGY.md          # 매매 전략 기준
│
├── python-only/         # AI 없이 Linux cron으로 단독 실행
│   ├── scan.py          # 분석 + 판단 + 주문 + Discord 전부 Python 처리
│   ├── cron-setup.sh    # Linux cron 등록 스크립트
│   └── HOW_IT_WORKS.md
│
└── ai-news/             # 레퍼런스: 순수 AI 방식
    ├── scan.py          # 데이터 출력만 (판단은 AI)
    ├── openclaw-config/
    │   ├── jobs.json    # OpenClaw cron 잡 템플릿
    │   └── SOUL.md      # AI 에이전트 지침
    └── HOW_IT_WORKS.md
```

---

## 공통 사전 준비

```bash
pip install httpx python-dotenv
cp .env.example .env
nano .env
```

| 키 | 설명 |
|----|------|
| `TRADING_SERVER_URL` | 키움 API 브릿지 서버 주소 |
| `SIGNAL_SECRET_KEY` | API 서버 인증 키 |
| `DISCORD_BOT_TOKEN` | Discord Bot 토큰 |
| `DISCORD_USER_ID` | DM 수신자 Discord 사용자 ID |
| `DART_API_KEY` | [OpenDART](https://opendart.fss.or.kr) 에서 발급 |

> `TRADING_SERVER_URL`은 키움증권 API와 연결된 브릿지 서버 주소입니다. 직접 구축하거나 동일 스펙의 서버가 필요합니다.

---

## 방식 A — AI 없이 사용 (python-only)

Linux cron이 직접 scan.py를 실행. OpenClaw 불필요. 비용 없음.

```bash
cd python-only
cp ../.env.example .env && nano .env
bash cron-setup.sh
```

등록되는 스케줄:
```
50 8     * * 1-5   python3 ~/openclaw-setup/python-only/scan.py
*/20 9-15 * * 1-5  python3 ~/openclaw-setup/python-only/scan.py
```

**동작:**
```
cron → scan.py
    → 기술 지표 계산 (RSI / BB / MA / 거래량비 / 호가비)
    → DART 공시 악재 확인
    → SELL 신호 3개↑ → trade.py 자동 실행
    → BUY 신호 2개↑ → trade.py 자동 실행
    → Discord DM 전송
```

뉴스 없음. 기술 지표 + DART 공시만으로 판단. 가장 안정적.

---

## 방식 B — OpenClaw + AI 뉴스 판단 (루트 폴더, 현재 권장)

Python이 신호를 계산하고, AI(gpt-4.1-mini)가 뉴스 확인 후 최종 매매를 결정.

### 사전 요구사항

- [OpenClaw](https://openclaw.ai) 설치 및 gpt-4.1-mini 설정

### 설치

```bash
cp .env.example .env && nano .env
```

### SOUL.md 설정

```bash
cp openclaw-config/SOUL.md ~/.openclaw/workspace/SOUL.md
```

### cron 잡 등록

```bash
cp openclaw-config/jobs.json ~/.openclaw/cron/jobs.json
```

`jobs.json` 플레이스홀더 교체:

| 플레이스홀더 | 값 |
|------------|-----|
| `YOUR_USERNAME` | `echo $USER` 결과 |
| `YOUR_DISCORD_USER_ID` | Discord 사용자 ID (개발자 모드 → 우클릭 → ID 복사) |
| `YOUR_JOB_UUID_1/2` | `python3 -c "import uuid; print(uuid.uuid4())"` 로 생성 |

### 동작

**08:50 (AI 브리핑):**
```
이데일리 RSS 수집
→ scan.py 실행 (기술 지표)
→ 종목별 뉴스 확인 (구글 뉴스 RSS, 최대 3개)
→ today_context.json 저장 (sentiment / caution / boost)
→ Discord 브리핑 전송
```

**09:00~15:20 (20분마다):**
```
scan.py 실행
→ PENDING 신호 있으면:
    구글 뉴스 RSS 확인 (신호 종목만)
    심각한 악재 → SKIP / 그 외 → trade.py 실행
    Discord 전송 (뉴스 판단 근거 포함)
→ PENDING 없으면:
    HOLD 메시지만 Discord 전송
```

**비용 (gpt-4.1-mini):** ~145원/일 · ~3,200원/월

---

## 주요 스크립트

### `trade.py` — 수동 주문

```bash
python3 trade.py --code 005930 --name '삼성전자' --action BUY \
  --confidence 0.75 --ratio 0.50 --reason 'RSI45, 거래량급증'
```

| 옵션 | 설명 |
|------|------|
| `--code` | 종목코드 (6자리) |
| `--action` | `BUY` 또는 `SELL` |
| `--confidence` | 신뢰도 0.0~1.0 (0.7 미만이면 실행 안 됨) |
| `--ratio` | 예수금 투입 비율 (기본 0.45) |

### `query.py` — 계좌/시세 조회

```bash
python3 query.py /kiwoom/account          # 잔고 조회
python3 query.py /kiwoom/quote/005930     # 호가 조회
python3 query.py /kiwoom/orders/filled    # 체결 내역
```

---

## 매매 전략 요약

**매도** (보유종목, 신호 3개↑):
수익률 ≥ +5% / RSI ≥ 78 / 당일 ≥ +4% / BB 상단 / 호가비 < 0.5

**매수** (신호 2개↑, 시장 과열·뉴스 부정 시 3개↑):
RSI ≤ 50 / 거래량 ≥ 1.3x / 당일 ≤ -1% / BB 하단 / MA 골든 / 호가비 ≥ 1.3 / 매수강도 ≥ 150

자세한 내용: [HOW_IT_WORKS.md](HOW_IT_WORKS.md) · [STRATEGY.md](STRATEGY.md)

---

## .gitignore

```
.env
today_context.json
__pycache__/
*.pyc
```

---

## 주의사항

- 이 봇은 **실제 자금으로 주식을 자동 매매**합니다. 충분히 테스트한 후 운영하세요.
- `.env` 파일 및 서버 주소는 절대 커밋하지 마세요.
- 투자 결과에 대한 책임은 사용자 본인에게 있습니다.
