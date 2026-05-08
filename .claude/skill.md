# price-monitor

인쇄업 경쟁사 가격 모니터링 시스템.
경쟁 인쇄사들의 가격을 자동 크롤링하고 비교한다.
**프린트시티(자사)를 기준가로 비교**.

## 프로젝트 구조

```
price-monitor/
├── scheduler.py                    # 메인 파이프라인 오케스트레이터
├── engine/                         # 신규 엔진 (adapter 기반)
│   ├── runner.py                   # site × sub_category 단위 실행 (rotate→fetch→normalize→store)
│   ├── adapter.py                  # SiteAdapter 베이스
│   ├── context.py                  # RunContext / RawItem 데이터클래스
│   ├── store.py                    # raw/normalize JSON 입출력
│   └── logger.py                   # 구조화 로깅
├── adapters/                       # 신규 어댑터 (sub_category 단위)
│   ├── {site}_card_offset.py       # 5사이트 × 오프셋 명함
│   ├── {site}_card_digital.py      # 5사이트 × 디지털 명함
│   ├── {site}_flyer.py             # 5사이트 × 합판전단
│   ├── {site}_envelope.py          # 봉투 (printcity/dtpia 만 — WIP)
│   └── _{site}_card_common.py      # 사이트별 카드 공통 헬퍼
├── crawlers/                       # 레거시 크롤러 (sticker, envelope 일부)
│   └── {Site}{Category}Crawler.py
├── common/
│   └── normalize.py                # raw → normalize 공통 파서 (legacy 카테고리용)
├── config/
│   ├── schemas/                    # 신규 정규화 룰 (engine 용)
│   │   ├── card_offset.yaml
│   │   ├── card_digital.yaml
│   │   ├── flyer.yaml
│   │   └── envelope.yaml
│   ├── targets/                    # 신규 타겟 정의 (engine 용)
│   │   ├── card_offset.yaml
│   │   ├── card_digital.yaml
│   │   ├── flyer.yaml
│   │   └── envelope.yaml
│   ├── sites/                      # 사이트별 기술 상수 (selectors, timeouts)
│   │   └── {site}.yaml             # 카테고리별 섹션 분리
│   ├── sticker_mapping_rule.json   # (legacy) 스티커 정규화
│   ├── sticker_targets.json        # (legacy) 스티커 타겟
│   └── output_template.json
├── dashboard/
│   ├── app.py                      # Flask 대시보드
│   └── templates/index.html
├── data/printcity/                 # 프린트시티 정적 source (엑셀)
│   └── envelope_{color,bw}.xlsx
├── scripts/
│   ├── build_printcity_envelope_targets.py  # 엑셀 → targets/envelope.yaml printcity 섹션
│   └── check_urls.py                        # URL 생존 체크
└── output/                         # 크롤링 결과 (gitignore)
    └── {site}_{category}_{raw|normalize}_{now|past}.json
```

## 제품 카테고리

| 카테고리 | 타입 | 사이트 | 비교 기준 |
|----------|------|-------|-----------|
| **명함 오프셋(card_offset)** | engine | printcity, dtpia, swadpia, wowpress, adsland | 용지×코팅×면×매수 |
| **명함 디지털(card_digital)** | engine | 위 5사 | 용지×코팅×면×매수 |
| **합판전단(flyer)** | engine | 위 5사 | 용지×사이즈×매수×도수 |
| **스티커(sticker)** | legacy | printcity, swadpia, dtpia, wowpress | 용지×코팅×사이즈×1000매 |
| **봉투(envelope)** | engine (WIP) | printcity ✓, dtpia ✓, swadpia/wowpress/adsland ✗ | 용지×사이즈×도수×1000매 |
| 엽서(postcard) | 미구현 | | |

## 실행

```bash
python scheduler.py card           # 오프셋+디지털 명함 일괄
python scheduler.py card_offset    # 오프셋만
python scheduler.py card_digital   # 디지털만
python scheduler.py flyer          # 합판전단
python scheduler.py sticker        # 스티커 (legacy)
python scheduler.py envelope       # 봉투 (WIP — scheduler 등록 필요)
python dashboard/app.py            # 대시보드 (localhost:5001)
```

## 파이프라인 흐름

### engine 타입 (card/flyer/envelope)
```
engine.runner.run(site, sub_cat):
  1. rotate: *_now.json → *_past.json
  2. SiteAdapter.fetch_and_extract() → RawItem 스트림
  3. store: raw_now.json 저장
  4. normalize (config/schemas/{cat}.yaml 룰 기반) → normalize_now.json
```

### legacy 타입 (sticker)
```
1. rotate: *_now.json → *_past.json
2. crawlers.{Site}{Cat}Crawler.crawl_all() → save()
3. common.normalize 으로 *_normalize_now.json 생성
```

## 정규화

### 신규 (engine, schemas/*.yaml)
canonical paper_name + weights + 사이트별 aliases. `_match_axes` 로 매칭축 정의.
weight tolerance(±20g 등), coating, print_mode, size, qty 각각 canonical 정의.

### legacy (common/normalize.py)
용지명 공통 파서 — 전체문자열 alias → prefix 코팅 → 괄호 코팅 → weight 분리 → base alias.

## 사이트 기술 스택

| 사이트 | 방식 | 특징 |
|--------|------|------|
| printcity | 카드/스티커: HTTP API · 봉투: **정적 엑셀** | 자사 — 봉투는 `data/printcity/envelope_*.xlsx` |
| swadpia | Playwright DOM | select 변경 + #print_estimate_tot |
| dtpia | Playwright DOM | select 변경 + callPrice() + #est_scroll_*_am |
| wowpress | Playwright DOM | getTemplate/reqMdmDetail + onchange eval |
| adsland | Playwright DOM | shop/order.php?IC=... |

## 가격 비교 주의사항

- **VAT**: dtpia 일부 페이지는 VAT 포함 → normalize 단계에서 ÷1.1 보정. site yaml `vat_included` 또는 RawItem `price_vat_included` 로 표시.
- **평량 오차**: ±20~25g 허용 (schema `weight_tolerance_g`)
- **스티커 EA 보정**: 프린트시티 일부 사이즈 sheet당 2EA → price/ea_per_sheet

## 대시보드

- 좌측 사이드바: 카테고리 선택, URL hash 로 상태 유지
- 가격 비교 탭: 용지×코팅 키별 그리드
- 가격 변동 탭: past vs now diff
- 가격 업데이트 버튼: scheduler.run_category() → SSE 진행률

## 의존성

```
flask, playwright, requests, beautifulsoup4, lxml, openpyxl, pyyaml
```
