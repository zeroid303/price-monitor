# price-monitor

인쇄업 경쟁사 가격 모니터링 시스템.
경쟁 인쇄사들의 가격을 자동 크롤링하고 비교한다.
스케줄러는 명함천국(ecard21) 기준으로 전 경쟁사를 수집하지만, **대시보드는 프린트시티(자사)를 기준가로 비교**한다.

## 프로젝트 구조

```
price-monitor/
├── scheduler.py                  # 메인 실행 진입점
├── config/
│   ├── settings.py               # 전역 설정 (출력 경로, 변동 임계값)
│   ├── card_reference.json       # 명함 용지 매칭 맵
│   ├── sticker_reference.json    # 스티커 용지 매칭 맵
│   ├── envelope_reference.json   # 봉투 용지 매칭 맵
│   ├── flyer_reference.json      # 전단지 용지 매칭 맵
│   ├── postcard_reference.json   # 엽서 용지 매칭 맵
│   ├── crawl_whitelist.json      # 크롤링 대상 화이트리스트
│   └── crawl_output_template.json # 출력 스키마 정의
├── crawlers/
│   ├── base.py                   # BaseCrawler + PriceRecord 데이터클래스
│   ├── Ecard21*.py               # 명함천국 크롤러 (Selenium)
│   ├── Bizhows*.py               # 비즈하우스 크롤러 (Playwright)
│   ├── PrintcityCardCrawler.py   # 프린트시티 (HTTP API)
│   ├── NapleCardCrawler.py       # 나플 (HTTP API)
│   ├── SwadpiaCardCrawler.py     # 성원애드피아 (Selenium)
│   ├── DtpiaCardCrawler.py       # 디티피아 (Selenium)
│   └── WowpressCardCrawler.py    # 와우프레스
├── dashboard/
│   ├── app.py                    # Flask 웹 대시보드 (실시간 가격 비교)
│   └── templates/index.html
└── output/                       # 크롤링 결과 JSON/CSV (gitignore)
```

## 제품 카테고리 (5개)

- **명함(card)**: 6개 경쟁사 크롤링 (ecard21, bizhows, printcity, naple, swadpia, dtpia)
  - **대시보드는 ecard21/naple 제외, 프린트시티 기준으로 비교** (크롤링·표시 모두에서 빠짐)
- **스티커(sticker)**: 2개 (ecard21, bizhows)
- **봉투(envelope)**: 2개 (ecard21, bizhows)
- **전단지(flyer)**: 2개 (ecard21, bizhows)
- **엽서(postcard)**: 2개 (ecard21, bizhows)

## 실행 방법

```bash
python scheduler.py              # 전체 카테고리 실행
python scheduler.py card         # 명함만
python scheduler.py sticker      # 스티커만
python scheduler.py envelope     # 봉투만
python scheduler.py flyer        # 전단지
python scheduler.py postcard     # 엽서만
```

대시보드:
```bash
python dashboard/app.py          # http://localhost:5000
```

## 핵심 동작 흐름

1. **크롤링**: 카테고리별 경쟁사 크롤러를 ThreadPoolExecutor로 병렬 실행 (2개 카테고리씩 배치)
2. **매칭**: `config/*_reference.json`의 crawl_key로 경쟁사별 용지를 동일 용지끼리 대응
3. **변동 감지**: 이전 크롤링 결과(price_history_v3.json)와 비교, 5% 이상 변동 시 감지
4. **히스토리 저장**: output/price_history_v3.json에 누적

## reference.json 구조

각 카테고리의 reference는 "우리 용지 ↔ 경쟁사 용지" 매칭 맵이다.

```json
{
  "papers": [
    {
      "paper_id": "snow_white_250_uncoated",
      "paper_name_ko": "스노우화이트 250g 비코팅",
      "ecard21": { "paper_code": "ppk17_1", "crawl_key": "스노우화이트 250g" },
      "bizhows": { "paper_name": "스노우 250g", "crawl_key": "스노우 250g" },
      "printcity": { "crawl_key": "스노우화이트-250g" },
      ...
    }
  ]
}
```

- `crawl_key`: 크롤링 결과에서 부분문자열 매칭에 사용되는 키
- `paper_code`: ecard21 전용, 정확한 코드 매칭
- 경쟁사 값이 `null`이면 해당 용지를 취급하지 않는 것

## 크롤러 기술 스택

| 기술 | 사용처 | 특징 |
|------|--------|------|
| **Selenium** | ecard21, swadpia, dtpia | WebDriver + JS 실행, hidden input 가격 추출 |
| **Playwright** | bizhows | DOM selectOption + JS 파싱, headless 브라우저 |
| **HTTP API** | printcity, naple | requests + BeautifulSoup, 가장 가볍고 안정적 |

## 가격 비교 시 주의사항

- **VAT**: 명함천국/프린트시티는 VAT 포함, 비즈하우스는 VAT 별도 → 비교 시 x1.1 변환 필요
- **수량 기준**: 명함 200매, 스티커 1000매, 봉투 500매 등 카테고리별 다름
- **용지명**: 경쟁사마다 같은 용지를 다른 이름으로 부름 → reference의 crawl_key로 매칭

## 대시보드 (Flask)

- SSE(Server-Sent Events)로 크롤링 진행률 실시간 스트리밍
- `/api/data/comparison`: reference 기반 가격 비교 테이블 (**프린트시티 기준 차액 표시**)
- `/api/data/changes`: past vs now 가격 변동 감지
- "가격 업데이트" 버튼: 비즈하우스 + 성원애드피아 병렬 크롤링
- 프린트시티/디티피아는 대시보드 버튼이 직접 크롤링하지 않고 `*_card_now.json` 파일을 그대로 읽음 → 기준가 갱신은 `python scheduler.py card`로 사전 실행 필요
- ecard21/naple은 대시보드에서 제외됨 (크롤링 + 표시 모두)

## 의존성

```
requests, beautifulsoup4, playwright, lxml, selenium, openpyxl, flask
```
