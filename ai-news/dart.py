"""
dart.py — DART 전자공시 당일 공시 조회
사용법:
  python3 dart.py 001510        # 종목코드로 오늘 공시 조회
  python3 dart.py 001510 3      # 최근 3일치 조회
출력: 공시 제목 목록 (악재/호재 판단용)
"""

import sys, os, json, httpx
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

DART_KEY = os.getenv("DART_API_KEY", "")
if not DART_KEY:
    sys.exit("❌ .env에 DART_API_KEY가 없습니다.")

# 종목코드 → DART 고유번호 매핑 (주요 종목)
CORP_MAP = {
    "005930": "00126380",  # 삼성전자
    "000660": "00164779",  # SK하이닉스
    "035420": "00266961",  # NAVER
    "051910": "00401731",  # LG화학
    "006400": "00164488",  # 삼성SDI
    "078930": "00108670",  # GS
    "061250": "00648826",  # 화일약품
    "217820": "00877422",  # 원익피앤이
    "001510": "00112774",  # SK증권
    "056080": "00631518",  # 유진로봇
    "084850": "00741612",  # 아이티엠반도체
}

if len(sys.argv) < 2:
    sys.exit("사용법: python3 dart.py {종목코드} [일수]")

stock_code = sys.argv[1]
days = int(sys.argv[2]) if len(sys.argv) > 2 else 1

corp_code = CORP_MAP.get(stock_code)
if not corp_code:
    # 미등록 종목은 전체 공시에서 검색
    bgn_de = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    resp = httpx.get("https://opendart.fss.or.kr/api/list.json",
        params={"crtfc_key": DART_KEY, "bgn_de": bgn_de, "sort": "date", "page_count": 20},
        timeout=8)
    data = resp.json()
    items = [i for i in data.get("list", []) if stock_code in i.get("stock_code", "")]
else:
    bgn_de = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    resp = httpx.get("https://opendart.fss.or.kr/api/list.json",
        params={"crtfc_key": DART_KEY, "corp_code": corp_code,
                "bgn_de": bgn_de, "sort": "date", "page_count": 20},
        timeout=8)
    data = resp.json()
    items = data.get("list", [])

if not items:
    print(f"[{stock_code}] 최근 {days}일 공시 없음")
else:
    print(f"[{stock_code}] 최근 {days}일 공시 {len(items)}건:")
    for item in items[:10]:
        date = item.get("rcept_dt", "")
        title = item.get("report_nm", "")
        print(f"  {date} | {title}")
