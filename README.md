# openclaw-setup

OpenClaw 기반 키움증권 자동매매 봇 설정 파일 모음.

평일 장중 20분마다 RSI · 볼린저밴드 · 호가비 · 거래량 등 기술 지표를 분석해 자동 매수/매도 주문을 실행하고, 결과를 Discord DM으로 전송합니다.

---

## 구조

```
openclaw-setup/
├── scan.py              # 핵심: 전 종목 분석 → 매수/매도 exec 명령 출력 + Discord 전송
├── trade.py             # 키움 API로 실제 주문 실행 (BUY/SELL, 지정가)
├── query.py             # API 인증 래퍼 (GET/POST)
├── check_time.py        # 장중 여부 확인 (SKIP 또는 OK 출력)
├── dart.py              # DART 공시 조회 (악재 필터)
├── notify.py            # Discord DM 전송 유틸
├── STRATEGY.md          # 매매 전략 기준 문서
├── .env.example         # 환경변수 샘플 (실제 .env는 gitignore)
└── openclaw-config/
    ├── SOUL.md          # OpenClaw 에이전트 시스템 프롬프트
    └── jobs.json        # cron 잡 설정 (jobs.json 위치: ~/.openclaw/cron/jobs.json)
```

---

## 사전 준비

| 항목 | 설명 |
|------|------|
| **OpenClaw** | 에이전트 플랫폼. `~/.openclaw/` 아래 설정 파일 위치 |
| **키움증권 API 서버** | `api.mieung.kr` 호환 서버 (별도 구축 필요) |
| **Discord Bot** | [Discord Developer Portal](https://discord.com/developers/applications)에서 봇 생성 후 DM 채널 권한 부여 |
| **DART API 키** | [OpenDART](https://opendart.fss.or.kr) 에서 발급 |

---

## 설치

### 1. 레포 클론 및 패키지 설치

```bash
git clone https://github.com/YOUR_USERNAME/openclaw-setup.git ~/openclaw-setup
cd ~/openclaw-setup
pip install httpx python-dotenv
```

### 2. 환경변수 설정

```bash
cp .env.example .env
nano .env   # 각 항목을 실제 값으로 채우기
```

`.env` 항목 설명:

| 키 | 설명 |
|----|------|
| `TRADING_SERVER_URL` | 키움 API 서버 주소 (e.g. `https://api.example.com`) |
| `SIGNAL_SECRET_KEY` | API 서버 인증 키 (서버와 동일한 값) |
| `DISCORD_BOT_TOKEN` | Discord Bot 토큰 |
| `DISCORD_USER_ID` | Discord DM 수신자 사용자 ID (개발자 모드 → 프로필 우클릭 → ID 복사) |
| `DASHBOARD_PASSWORD` | 대시보드 로그인 비밀번호 |
| `SESSION_SECRET` | 세션 암호화용 랜덤 문자열 |
| `DART_API_KEY` | DART 공시 API 키 |

### 3. OpenClaw SOUL 설정

```bash
cp openclaw-config/SOUL.md ~/.openclaw/workspace/SOUL.md
```

`SOUL.md` 안의 `/home/YOUR_USERNAME/` 경로를 실제 경로로 치환:
```bash
sed -i 's|YOUR_USERNAME|'$USER'|g' ~/.openclaw/workspace/SOUL.md
```

### 4. cron 잡 등록

```bash
cp openclaw-config/jobs.json ~/.openclaw/cron/jobs.json
```

`jobs.json` 안의 플레이스홀더를 실제 값으로 치환:
- `YOUR_USERNAME` → 실제 리눅스 사용자명
- `YOUR_DISCORD_USER_ID` → Discord 사용자 ID
- `YOUR_JOB_UUID_1`, `YOUR_JOB_UUID_2` → `python3 -c "import uuid; print(uuid.uuid4())"` 로 생성한 UUID

---

## 동작 방식

### cron 스케줄

| 잡 | 시간 | 내용 |
|----|------|------|
| `trading-8h50` | 평일 08:50 KST | 장 시작 전 보유 종목 점검 |
| `trading-9h-to-15h20` | 평일 09:00~15:20, 20분마다 | 장중 전 종목 스캔 |

### 실행 흐름

```
check_time.py → 장외시간이면 SKIP (종료)
        ↓
scan.py → API에서 시세/호가/보유현황 조회
        ↓
기술 지표 계산 (RSI, BB, 호가비, 거래량배율)
        ↓
SELL 신호 3개 이상 → trade.py --action SELL exec
BUY  신호 N개 이상 → trade.py --action BUY  exec
        ↓
DISCORD_MSG 출력 + Discord DM 직접 전송
```

### 매매 전략 요약

자세한 내용은 [STRATEGY.md](STRATEGY.md) 참조.

**매도** — 아래 중 3개 이상 동시 충족 시 (손익과 무관한 순수 시장 신호):
- 수익 +5% 이상
- RSI ≥ 78
- 당일 급등 ≥ +4%
- 볼린저 상단 돌파
- 호가비(매수/매도) < 0.5

**매수** — KODEX200 과열 여부에 따라 2~3개 이상 충족 시:
- RSI ≤ 50, 거래량 ≥ 평균 1.3배, 당일 하락 ≤ -1%, BB 하단, MA 골든크로스, 호가비 ≥ 1.3, 매수강도 ≥ 150

---

## 주요 스크립트

### `scan.py`
전 종목 분석의 진입점. 실행하면 표준 출력으로 분석 결과 + trade.py exec 명령이 나옵니다.

```bash
python3 scan.py
```

### `trade.py`
실제 주문 실행. scan.py 출력의 exec 명령을 그대로 실행하면 됩니다.

```bash
python3 trade.py --code 005930 --name '삼성전자' --action BUY \
  --confidence 0.70 --ratio 0.50 --reason 'RSI45,거래량급증'
```

| 옵션 | 설명 |
|------|------|
| `--code` | 종목코드 (6자리) |
| `--name` | 종목명 |
| `--action` | `BUY` 또는 `SELL` |
| `--confidence` | 신뢰도 (0.0~1.0) |
| `--ratio` | 예수금 투입 비율 (0.0~1.0) |
| `--reason` | 매매 사유 (로그용) |

### `query.py`
API 서버 조회 래퍼. 인증 헤더를 자동으로 붙여줍니다.

```bash
python3 query.py /kiwoom/account          # 잔고 조회
python3 query.py /kiwoom/holdings         # 보유 종목
python3 query.py /kiwoom/quote/005930     # 특정 종목 호가
python3 query.py /kiwoom/orders/filled    # 체결 내역
python3 query.py /dashboard/summary       # 대시보드 요약
```

---

## .gitignore

```
.env
__pycache__/
*.pyc
```

---

## 주의사항

- 이 봇은 **실제 자금으로 주식을 자동 매매**합니다. 충분한 테스트 후 운영하세요.
- API 서버, Discord 봇, DART 키 등 모든 인증 정보는 절대 커밋하지 마세요.
- 투자 결과에 대한 책임은 사용자 본인에게 있습니다.
