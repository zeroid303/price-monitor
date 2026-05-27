# price-monitor

인쇄업 경쟁사 가격 모니터링 시스템.
경쟁 인쇄사 5곳의 가격을 자동 크롤링·정규화하고, **프린트시티(자사)를 기준가로** 비교한다.

- **사이트(5)**: `printcity`(자사·기준), `dtpia`, `swadpia`, `wowpress`, `adsland`
- **카테고리(6)**: `card_offset`, `card_digital`, `flyer`, `envelope`, `sticker_offset`, `sticker_digital`
- 전 카테고리가 **engine 아키텍처**(adapter 기반)로 통일됨. 구 `crawlers/` 직접 호출 방식은 폐기 — 껍데기만 남음.

---

## 한눈에 보는 데이터 흐름

```
scheduler.py <category>
  └─ engine.runner.run(site, sub_category)          ← site × sub_category 1쌍씩 반복
       1. config 로드:  sites/{site}.yaml + schemas/{cat}.yaml + targets/{cat}.yaml[site]
       2. adapter.fetch_and_extract(ctx) → RawItem 스트림   (DOM/엑셀 실측만)
       3. normalize.apply(raw, schema._normalization)        → 용지/코팅/도수/사이즈/매수/VAT 정규화
       4. (옵션) normalize.interpolate_qty_price(...)         → schema._interpolation 있을 때 매수 비례환산
       5. make_item_id(정규화 후 qty 기준)
       6. store.write → output/{site}_{cat}_{raw|normalize}_now.json (기존 now→past 회전)
  └─ scripts.check_urls.run([category])             ← 전 사이트 끝난 뒤 카테고리당 1회, url_ok 갱신
```

핵심 원칙:
- **어댑터는 fetch + extract 만** 한다. 정규화·보간·저장·회전은 전부 engine 책임.
- **RawItem 은 사이트 표시값 그대로** 기록(합성·보정 금지). 매칭/오버라이드는 schema·targets 단계에서.
- 변동 감지(past vs now)는 저장만 해두고, **대시보드가 비교 표시**한다.

---

## 디렉토리 구조

```
price-monitor/
├── scheduler.py              # CLI 진입점. CATEGORIES dict 로 site/sub_category 매핑 후 engine.runner 호출
│
├── engine/                   # 실행 엔진 (사이트 무관 공통 로직)
│   ├── runner.py             # run(site, cat): config 로드 → adapter → normalize → interpolate → store
│   ├── adapter.py            # SiteAdapter ABC — fetch_and_extract(ctx) -> Iterator[RawItem]
│   ├── context.py            # RawItem(DOM 실측 레코드) / RunContext(실행 상태) 데이터클래스
│   ├── store.py              # raw/normalize JSON now→past 회전 + 쓰기
│   └── logger.py             # 구조화 이벤트 로깅(RunLogger)
│
├── adapters/                 # site × sub_category 1쌍당 1파일. 전부 SiteAdapter 구현
│   ├── {site}_card_offset.py     # 오프셋 명함 (5사)
│   ├── {site}_card_digital.py    # 디지털 명함 (5사)
│   ├── {site}_flyer.py           # 합판전단 (5사)
│   ├── {site}_envelope.py        # 봉투 — 단면4도 칼라 전용 (5사)
│   ├── {site}_sticker_offset.py  # 사각재단 스티커 합판 — 8 표준사이즈 ±5mm, 1000매 (5사)
│   ├── {site}_sticker_digital.py # 사각재단 스티커 디지털 — 100매(adsland 105매) (dtpia 제외 4사)
│   └── _{site}_card_common.py    # dtpia/swadpia/wowpress/adsland 카드 공통 헬퍼
│
├── common/
│   └── normalize.py          # raw → 정규화 순수 함수. apply() / interpolate_qty_price()
│
├── config/
│   ├── schemas/{cat}.yaml     # 정규화 룰: _normalization + _match_axes + _interpolation + _defaults
│   ├── targets/{cat}.yaml     # 수집 대상: 경쟁사=list[dict], printcity=dict{items,...} (빌드스크립트 생성)
│   ├── sites/{site}.yaml      # 사이트 기술 상수: base_url, tech, vat_included, selectors, timeouts
│   ├── output_template.json
│   └── settings.py            # OUTPUT_DIR 등 경로 상수 (구 모듈 잔재)
│
├── dashboard/
│   ├── app.py                # Flask 대시보드 (port 5001). normalize_now.json 만 읽어 표시, 값 변환 없음
│   └── templates/index.html
│
├── data/printcity/           # 프린트시티 정적 source (엑셀) — 카드/봉투/전단
│   ├── card.xlsx, premium_card.xlsx, digital_card.xlsx
│   ├── flyer.xlsx, flyer2.xlsx
│   └── envelope_color.xlsx               # 단면4도 칼라봉투 가격표
│   └── ../reference/card_paper_aliases.xlsx   # 용지 alias 참조표
│
├── scripts/                  # 빌드/검증 유틸 (수동 실행)
│   ├── build_printcity_card_targets.py     # data/printcity/*.xlsx → targets/card_*.yaml [printcity]
│   ├── build_printcity_envelope_targets.py # 〃 → targets/envelope.yaml [printcity]
│   ├── build_printcity_flyer_targets.py    # 〃 → targets/flyer.yaml [printcity]
│   ├── build_flyer_targets.py              # 경쟁사 flyer targets 생성
│   ├── build_card_schemas.py               # 카드 schema 빌드
│   ├── build_match_from_schema.py / build_paper_match_table.py / build_paper_match_xlsx.py
│   ├── build_envelope_paper_aliases.py
│   ├── renormalize_card.py / renormalize_flyer.py  # 크롤 안 하고 기존 raw_now 만 재정규화
│   ├── check_urls.py                       # normalize_now.json 의 url_ok HEAD 체크 갱신
│   └── verify_dashboard.py / verify_comprehensive.py / verify_raw_against_dom.py
│
├── research/                 # 사이트별 옵션 탐색용 일회성 probe 스크립트 (운영 파이프라인과 무관)
│
└── output/                   # 크롤링 결과 (gitignore)
    └── {site}_{cat}_{raw|normalize}_{now|past}.json
        # 포맷: { "company": <site>, "crawled_at": "YYYY-MM-DD HH:MM", "items": [...] }
```

---

## 제품 카테고리

| sub_category | 사이트 | 비교 기준(매칭축) | 표준 매수 |
|---|---|---|---|
| `card_offset`  | 5사 | 용지 × 코팅 × 도수 × 매수 | 사이트별 |
| `card_digital` | 5사 | 용지 × 코팅 × 도수 × 매수 | 사이트별 |
| `flyer`        | 5사 | 용지 × 사이즈 × 매수 × 도수 | 사이즈별(`_interpolation.by_size`) |
| `envelope`     | 5사 | 용지 × 사이즈 | **1000매** · 단면4도 칼라 전용 |
| `sticker_offset`  | 5사 | 8 표준사이즈(60x40~90x120, 각 변 ±5mm) × 용지 × 코팅 | **1000매** |
| `sticker_digital` | 4사(**dtpia 제외**) | 위와 동일 8 사이즈 × 용지 | **100매** (adsland 105매) |
| ~~postcard(엽서)~~ | 미구현 | | |

> `sticker_digital` 에서 dtpia 는 PanSticker 사이즈가 표준과 달라 수집 제외 (어댑터 파일 없음 → runner import 실패로 자동 누락).

---

## 실행

```bash
python scheduler.py card             # card_offset + card_digital 일괄
python scheduler.py card_offset      # 오프셋 명함만
python scheduler.py card_digital     # 디지털 명함만
python scheduler.py flyer            # 합판전단
python scheduler.py sticker          # sticker_offset + sticker_digital 일괄
python scheduler.py sticker_offset   # 스티커 합판만
python scheduler.py sticker_digital  # 스티커 디지털만
python scheduler.py envelope         # 봉투

python -m engine.runner <site> <cat> # 단일 site×category 디버그 실행 (예: python -m engine.runner dtpia card_offset)
python dashboard/app.py              # 대시보드 → http://localhost:5001
```

`scheduler.CATEGORIES` 에서 카테고리 → `{sites, sub_categories}` 매핑을 정의한다. 사이트 추가/제외는 여기를 수정.

---

## 핵심 데이터클래스 (engine/context.py)

**RawItem** — DOM/엑셀 실측 1건. 합성·보정 금지.
```
product, category        # target 라벨 (사이트 메뉴 분류 식별용)
paper_name               # select/div 표시 텍스트 그대로
paper_weight_text        # 평량 select 텍스트 (있을 때만)
coating, print_mode, size, qty, price
price_vat_included       # 사이트 정책 (메타) — normalize 가 공급가로 환산
url, url_ok
options                  # 어댑터별 메타. target_mae/raw_qty 등 (운영 raw 엔 최소화)
```
`match_as`·`config_*` 같은 매칭 결정값은 **RawItem 에 절대 넣지 않는다** — targets/schemas 에서 처리.

**RunContext** — runner 가 어댑터에 주입: `site_config`, `schema`, `targets`, `log`, (필요시 `browser`).

---

## 정규화 (common/normalize.py + schemas/*.yaml)

`schema._normalization` 의 dict 룰로 `apply(raw, rule)` 가 처리:
- **paper_name**: `canonical[name].weights[w].aliases[site]` 역인덱스로 매칭 → `"용지명 Wg"` canonical 화. 평량 strip/괄호코팅/`g/㎡→g`/dash 등 다수 변형 fallback.
- **coating / print_mode**: `aliases` 역인덱스 + `to_options`(부분코팅 등 특수 → options 분리).
- **size**: 카드/스티커는 `WxH` mm 정수. 봉투는 `대봉투` 등 카테고리 canonical. 스티커는 ±5mm 매칭.
- **qty**: int. raw 가 None 이면 **None 보존**(임의 default 채우면 오라벨).
- **price (VAT)**: `price_vat_included=True` 면 `÷1.1` 하여 **공급가(VAT 제외) 기준으로 통일**, `options.price_vat_stripped` 흔적 남김.

`_match_axes` 에 비교축(`weight_tolerance_g` ±25, `size_tolerance_mm` ±5 등) 정의.
`_interpolation` (`by_size` 또는 `standard_qty`) 있으면 매수를 표준으로 비례환산하고 `options.raw_qty` 에 원본 보존.

---

## 사이트별 수집 방식

| 사이트 | 카드/봉투/전단 | 스티커 | 비고 |
|---|---|---|---|
| **printcity** (자사) | **정적 xlsx** (`data/printcity/*.xlsx` → 빌드스크립트가 targets 생성) | **Playwright** (StickerRectangleNew / DigitalSticker 페이지) | 봉투/전단/카드 엑셀, 스티커만 크롤 |
| dtpia    | Playwright DOM | Playwright DOM | select 변경 + callPrice() + `#est_scroll_*_am` |
| swadpia  | Playwright DOM | Playwright DOM | select 변경 + `#print_estimate_tot` |
| wowpress | Playwright DOM | Playwright DOM | getTemplate/reqMdmDetail + onchange eval |
| adsland  | Playwright DOM | Playwright DOM | `shop/order.php?IC=...` |

- printcity 엑셀 갱신 절차: `data/printcity/*.xlsx` 교체 → `scripts/build_printcity_*_targets.py` 실행 → targets yaml 갱신. (스티커는 scheduler 가 자동 크롤)
- 사이트 HTML 개편 시 **`config/sites/{site}.yaml` 의 selector/timeout 만 수정** — 어댑터 코드는 가급적 불변.

---

## 가격 비교 주의사항

- **VAT**: dtpia 일부 페이지·printcity 스티커는 VAT 포함 표시 → normalize 가 `÷1.1` 로 공급가 환산. site yaml `vat_included` 또는 RawItem `price_vat_included` 로 표시.
- **평량 오차**: ±25g 허용 (`schema._match_axes.weight_tolerance_g`).
- **사이즈 오차**: 스티커 각 변 ±5mm (`size_tolerance_mm`).
- **스티커 도수 정책 차이**: 사이트별 단면4도/단면칼라/5도 등 1:1 매칭 불가 → '동일 조건' 으로 비교(사용자 합의).
- **EA 보정**: 프린트시티 일부 사이즈 sheet당 복수 EA → price/ea 보정.

### ⚠️ 알려진 표기 오류 — 스티커 print_mode "양면5도" (미수정, 코드 손대기 전 반드시 읽을 것)

스티커 schema/어댑터/output_template 에 박혀 있는 `양면5도` 는 **명칭 오류**다. 원인 파악만 해둔 상태(2026-05-27).

- **"5도" 는 맞다**: `5도 = CMYK + 백색(White) 1도`. 5번째 도는 백색 잉크 — 투명데드롱·은데드롱 같은 투명/메탈 소재에 CMYK 가 비치지 않게 **한 면에 밑판으로 깔아주는** 공정. (alias 근거: `output_template.json` 의 `양면5도: [5도, CMYK+W, CMYK+백판]`)
- **"양면" 이 틀렸다**: 스티커는 뒷면이 접착면이라 **단면 인쇄만** 가능 → 물리적으로 양면 불가. `단면5도(CMYK+백색)` 또는 `5도(CMYK+W)` 가 옳음.
- **발원지**: 최초 스티커 커밋 `83c0d1c` 의 `config/sticker_targets.json` 에서 개발자가 `COL:41` 에 손으로 붙인 라벨. printcity 옵션 원문이 아니라 **카드 도수 어휘(양면칼라/단면칼라)를 습관적으로 차용**한 흔적. 이후 `output_template.json` → `schemas/sticker_*.yaml` → `adapters/printcity_sticker_digital.py` 로 전파.
- **부수 오류**: `schemas/sticker_offset.yaml` 설명문은 "프린트시티 = 양면5도" 라 적었지만, 실제로 **오프셋 어댑터는 `단면4도`(COL:40)**, **양면5도(COL:41)는 디지털 어댑터에서만** 나온다 → 오프셋 스키마에 잘못 귀속됨.
- **수정 시 주의**: `print_mode` 는 `_match_axes` canonical(매칭 키)이라, 이름만 바꾸면 기존 `output/*_now.json` 의 "양면5도" 와 키가 어긋나 대시보드 매칭이 깨진다. **스키마 + 어댑터 + output 재크롤(또는 renormalize)을 한 번에** 가야 함.

---

## 대시보드 (dashboard/app.py · port 5001)

- normalize_now.json 만 읽어 표시 — **값 변환 로직 없음** (정규화는 전적으로 scheduler/engine 책임).
- 좌측 사이드바: 카테고리 선택, URL hash 로 상태 유지.
- 가격 비교 탭: 용지×코팅 등 매칭 키별 그리드. 가격 변동 탭: past vs now diff.
- "가격 업데이트" 버튼: `scheduler.run_category()` 백그라운드 실행 → SSE 진행률.

---

## 의존성

```
flask, playwright, requests, beautifulsoup4, lxml, openpyxl, pyyaml
```
