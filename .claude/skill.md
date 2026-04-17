# price-monitor

인쇄업 경쟁사 가격 모니터링 시스템.
경쟁 인쇄사들의 가격을 자동 크롤링하고 비교한다.
**프린트시티(자사)를 기준가로 비교**.

## 프로젝트 구조

```
price-monitor/
├── scheduler.py                    # 메인 파이프라인 (크롤링 → raw → normalize → 변동감지)
├── common/
│   └── normalize.py                # raw → normalize 변환 (공통 파서)
├── config/
│   ├── card_mapping_rule.json      # 명함 정규화 규칙 + 사이트 정의
│   ├── card_targets.json           # 명함 크롤링 타겟 (사이트별 용지/코팅/사이즈)
│   ├── sticker_mapping_rule.json   # 스티커 정규화 규칙
│   ├── sticker_targets.json        # 스티커 크롤링 타겟
│   └── output_template.json        # 아웃풋 스키마 정의 (카드/스티커 공용)
├── crawlers/
│   ├── {Site}CardCrawler.py        # 명함 크롤러 (5개 사이트)
│   ├── {Site}StickerCrawler.py     # 스티커 크롤러 (5개 사이트)
│   └── base.py                     # BaseCrawler 베이스 클래스
├── dashboard/
│   ├── app.py                      # Flask 대시보드 (카테고리별 비교/변동)
│   └── templates/index.html
└── output/                         # 크롤링 결과 JSON (gitignore)
    └── {company}_{category}_{raw|normalize}_{now|past}.json
```

## 제품 카테고리

| 카테고리 | 크롤링 사이트 | 비교 기준 |
|----------|--------------|-----------|
| **명함(card)** | printcity, bizhows, swadpia, dtpia, wowpress | 용지×코팅×면(단면/양면)×매수(100~1000) |
| **스티커(sticker)** | printcity, bizhows, swadpia, dtpia, wowpress | 용지×코팅×사이즈(45~95mm)×1000매 |
| 봉투(envelope) | 미구현 | |
| 전단지(flyer) | 미구현 | |
| 엽서(postcard) | 미구현 | |

## 실행

```bash
python scheduler.py card       # 명함 파이프라인
python scheduler.py sticker    # 스티커 파이프라인
python dashboard/app.py        # 대시보드 (localhost:5001)
```

## 파이프라인 흐름

```
1. rotate: *_now.json → *_past.json 복사
2. 크롤링: 각 사이트 크롤러 실행 → *_raw_now.json 생성
3. 정규화: mapping_rule 기반 normalize → *_normalize_now.json 생성
4. 대시보드: normalize_now 읽어 표시, past vs now 변동 감지
```

## 정규화 (common/normalize.py)

용지명 공통 파서 — 전체문자열 alias 매칭 → prefix 코팅 추출 → 괄호 코팅 추출 → weight 분리 → base alias → 재조립.
카드/스티커 동일 로직. mapping_rule.json의 aliases에 등록하면 자동 매칭.

## 크롤러 기술 스택

| 사이트 | 방식 | 특징 |
|--------|------|------|
| printcity | Playwright DOM / HTTP API | 카드는 dtp21 API, 스티커는 DOM 조작 |
| bizhows | Playwright + combination API | selectedOptionList URL로 조합 지정 (stateless API) |
| swadpia | Playwright DOM | select 변경 + #print_estimate_tot 가격 파싱 |
| dtpia | Playwright DOM | select 변경 + callPrice() 트리거 + #est_scroll_total_am |
| wowpress | Playwright DOM | getTemplate/reqMdmDetail + onchange eval 필수 |

## 가격 비교 주의사항

- **VAT**: 비즈하우스만 VAT 별도 → normalize에서 ×1.1 자동 보정
- **평량 오차**: ±25g 허용
- **스티커 EA 보정**: 프린트시티 일부 사이즈는 sheet당 2EA → price/ea_per_sheet로 보정

## 대시보드

- 좌측 사이드바: 카테고리 선택 (명함/스티커), URL hash로 상태 유지
- 가격 비교 탭: 용지×코팅 키별 그리드 (카드: 면×매수 행, 스티커: 사이즈 행)
- 가격 변동 탭: past vs now 차이 감지
- 가격 업데이트 버튼: scheduler.run_category() 호출 → SSE로 진행률 스트리밍
- raw 용지명: 사이트 헤더에 정규화 전 원래 용지명 표시

## 의존성

```
flask, playwright, requests, beautifulsoup4, lxml, openpyxl
```
