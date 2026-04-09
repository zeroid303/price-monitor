# """
# 비즈하우스(bizhows.com) 스티커 가격 크롤러
#
# 기존 명함 크롤러 v5.1 구조 참조:
#   - combination API + selectOption API + URL 네비게이션 방식
#   - DOM에서 가격/옵션명 읽기 (JS_READ_PAGE)
#
# 스티커 특성:
#   - 싱글 규격 스티커: 모양(5종) × 사이즈(모양별 4~9종) × 원단(14종) × 매수
#   - 모양 선택 시 디자인타입 + 사이즈가 연동 변경됨
#   - 옵션 인덱스: 0=디자인타입, 1=모양, 2=사이즈, 3=원단, 4=매수
#   - 고정 수량: 1,000매 (seq=67060)
#   - 용지 전체 크롤링
# """
import json
import time
import logging
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)
# ── 스티커 제품 정의 ──────────────────────────────────────────
# 싱글 규격 스티커 1종 (모양별로 크롤링)
STICKER_PRODUCT = {
    "name": "싱글 규격 스티커",
    "code1": "8000",
    "code2": "200",
    "code3": "7101",
    "mock": "8000_200_7101",
}
# 옵션 인덱스 (combination API optionList)
IDX_DESIGN = 0   # 디자인 타입
IDX_SHAPE  = 1   # 모양
IDX_SIZE   = 2   # 사이즈
IDX_PAPER  = 3   # 원단
IDX_QTY    = 4   # 매수
# 모양 seq 매핑 (분석 결과)
SHAPES = [
    {"seq": 67039, "name": "원형"},  # 원형만 (사이즈 무관 동일가이므로 1개면 충분)
    # {"seq": 67033, "name": "사각형"},
    # {"seq": 67046, "name": "직사각형"},
]
# 고정 매수 seq (1,000매 = 67060)
QTY_1000_SEQ = 67060
# 불필요한 리소스 차단 (속도 향상)
BLOCK_PATTERNS = [
    "**/f.clarity.ms/**",
    "**/analytics.tiktok.com/**",
    "**/channel.io/**",
    "**/criteo.net/**",
    "**/criteo.com/**",
    "**/google-analytics.com/**",
    "**/googletagmanager.com/**",
    "**/facebook.net/**",
    "**/facebook.com/tr/**",
    "**/doubleclick.net/**",
    "**/ads-twitter.com/**",
]
BASE_URL = "https://www.bizhows.com/ko/v/option"
# ── JS: DOM에서 현재 옵션 + 가격 읽기 ─────────────────────
JS_READ_PAGE = """() => {
    const opts = {};
    const seen = new Set();
    const dwRows = document.querySelectorAll('[data-f^="DW-"]');
    for (const dw of dwRows) {
        const ol = dw.querySelector('[data-f^="OL-"]');
        if (!ol) continue;
        const dts = dw.querySelectorAll('[data-f^="DT-"]');
        let label = null;
        for (const dt of dts) {
            const t = dt.textContent.trim();
            if (t && !seen.has(t)) { label = t; seen.add(t); break; }
        }
        if (!label) continue;
        const ot = dw.querySelector('[data-f^="OT-"]');
        if (ot) opts[label] = ot.textContent.trim();
    }
    let price = null;
    const qlEls = document.querySelectorAll('[data-f^="QL-"]');
    for (const ql of qlEls) {
        if (ql.textContent.trim() === '총 금액') {
            const parent = ql.parentElement;
            if (parent) {
                const qr = parent.querySelector('[data-f^="QR-"]');
                if (qr) price = qr.textContent.trim();
            }
        }
    }
    if (!price) {
        const m = document.body.innerText.match(/총 금액\\\\s*([\\\\d,]+원)/);
        if (m) price = m[1];
    }
    return {opts, price};
}"""
# ── JS: 원단(용지)명 읽기 ─────────────────────────────────
JS_READ_PAPER_NAME = """() => {
    const dwRows = document.querySelectorAll('[data-f^="DW-"]');
    for (const dw of dwRows) {
        const ol = dw.querySelector('[data-f^="OL-"]');
        if (!ol) continue;
        const dts = dw.querySelectorAll('[data-f^="DT-"]');
        for (const dt of dts) {
            const t = dt.textContent.trim();
            if (t === '용지' || t === '원단') {
                const ot = dw.querySelector('[data-f^="OT-"]');
                if (ot) return ot.textContent.trim();
            }
        }
    }
    return null;
}"""
class BizhowsStickerCrawler:
    """비즈하우스 스티커 가격 크롤러"""
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.results: list[dict] = []
    def _init_browser(self, pw):
        browser = pw.chromium.launch(headless=self.headless)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="ko-KR",
        )
        for pattern in BLOCK_PATTERNS:
            context.route(pattern, lambda route: route.abort())
        return browser, context
    # ── API 호출 ──────────────────────────────────────────
    def _api_combination(self, page) -> dict | None:
        """combination API 호출 → 옵션 구조 반환"""
        p = STICKER_PRODUCT
        url = f"/api/v1/option/combination/{p['code1']}/{p['code2']}/{p['code3']}/{p['mock']}"
        try:
            result = page.evaluate(f"""() => {{
                return fetch('{url}')
                    .then(r => r.json())
                    .then(data => data.data);
            }}""")
            return result
        except Exception as e:
            log.error(f"  combination API 실패: {e}")
            return None
    def _api_select_option(self, page, option_seq: int) -> dict | None:
        """selectOption API 호출 → 특정 옵션 선택 후 전체 구조 반환"""
        p = STICKER_PRODUCT
        url = f"/api/v1/option/combination/{p['code1']}/{p['code2']}/{p['code3']}/{p['mock']}?selectOption={option_seq}"
        try:
            result = page.evaluate(f"""() => {{
                return fetch('{url}')
                    .then(r => r.json())
                    .then(data => data.data);
            }}""")
            return result
        except Exception as e:
            log.error(f"  selectOption API 실패 (seq={option_seq}): {e}")
            return None
    # ── 옵션 탐색 유틸리티 ────────────────────────────────
    def _find_option_index(self, option_list: list, name: str) -> int:
        """옵션 리스트에서 특정 이름의 인덱스를 동적으로 찾기"""
        for i, opt in enumerate(option_list):
            if opt.get("poiName", "") == name:
                return i
        return -1
    def _build_base_url(self) -> str:
        """기본 제품 URL 생성"""
        p = STICKER_PRODUCT
        return (
            f"{BASE_URL}?code1={p['code1']}&code2={p['code2']}"
            f"&code3={p['code3']}&mock={p['mock']}&from=product_list_001"
        )
    def _navigate_and_read(self, page, selections: list[int]) -> dict:
        """selections 배열로 URL 구성 → 네비게이션 → DOM에서 가격/옵션 읽기"""
        sel_str = ",".join(str(s) for s in selections)
        nav_url = f"{self._build_base_url()}&selectedOptionList={sel_str}"
        try:
            page.goto(nav_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_function(
                "() => document.body.innerText.includes('총 금액')",
                timeout=15000,
            )
            page.wait_for_timeout(500)
        except PwTimeout:
            log.warning(f"    페이지/총금액 대기 타임아웃")
        page_data = page.evaluate(JS_READ_PAGE)
        paper_name = page.evaluate(JS_READ_PAPER_NAME)
        return {
            "opts": page_data.get("opts", {}),
            "price": page_data.get("price"),
            "paper_name": paper_name,
            "url": nav_url,
        }
    # ── 매수 1,000매 seq 확보 ─────────────────────────────
    def _find_qty_1000_seq(self, page, option_list: list) -> int:
        """
        매수 옵션에서 1,000매에 해당하는 seq를 탐색.
        기본적으로 하드코딩된 QTY_1000_SEQ를 사용하되,
        해당 seq가 povList에 없으면 동적 탐색 시도.
        """
        qty_idx = self._find_option_index(option_list, "매수")
        if qty_idx < 0:
            log.warning("  매수 옵션 없음")
            return QTY_1000_SEQ
        qty_pov = option_list[qty_idx].get("povList", [])
        # 1) 하드코딩 seq가 povList에 있으면 그대로 사용
        if QTY_1000_SEQ in qty_pov:
            return QTY_1000_SEQ
        # 2) 없으면 povList에서 1,000매를 동적 탐색
        #    (각 seq를 선택해서 DOM 확인 — 느리지만 확실)
        log.info(f"  1,000매 seq({QTY_1000_SEQ}) 미발견 → 동적 탐색")
        for seq in qty_pov:
            sel_data = self._api_select_option(page, seq)
            if not sel_data:
                continue
            selections = [opt.get("selected") for opt in sel_data["optionList"]]
            # 임시 URL 네비게이션으로 수량 확인
            result = self._navigate_and_read(page, selections)
            qty_text = result["opts"].get("수량", "")
            if "1,000" in qty_text or "1000" in qty_text:
                log.info(f"  1,000매 seq 발견: {seq}")
                return seq
        log.warning("  1,000매 seq를 찾지 못함 → 기본값 사용")
        return QTY_1000_SEQ
    # ── 모양별 사이즈 이름 매핑 ───────────────────────────
    def _map_size_names(self, page, shape_seq: int, shape_name: str) -> list[dict]:
        """
        특정 모양을 선택한 상태에서 사이즈 옵션의 seq→이름 매핑.
        selectOption API + URL 네비게이션으로 이름 확인.
        """
        sel_data = self._api_select_option(page, shape_seq)
        if not sel_data:
            return []
        opt_list = sel_data["optionList"]
        size_idx = self._find_option_index(opt_list, "사이즈")
        if size_idx < 0:
            return []
        size_pov = opt_list[size_idx].get("povList", [])
        base_selections = [opt.get("selected") for opt in opt_list]
        size_map = []
        for size_seq in size_pov:
            # 사이즈 seq를 선택
            size_sel_data = self._api_select_option(page, size_seq)
            if not size_sel_data:
                continue
            selections = [opt.get("selected") for opt in size_sel_data["optionList"]]
            # DOM에서 사이즈 이름 확인
            result = self._navigate_and_read(page, selections)
            size_name = result["opts"].get("사이즈", f"size_{size_seq}")
            size_map.append({
                "seq": size_seq,
                "name": size_name,
                "default_selections": selections,
            })
            log.info(f"    사이즈 매핑: {size_seq} → {size_name}")
        return size_map
    # ── 메인 크롤링 ──────────────────────────────────────
    def _crawl_shape(
        self, page, shape: dict, paper_pov: list[int], qty_seq: int
    ):
        """
        단일 모양에 대해: 전체 사이즈 × 전체 원단(용지) × 1,000매 크롤링.
        """
        shape_seq = shape["seq"]
        shape_name = shape["name"]
        log.info(f"\\n{'─'*50}")
        log.info(f"▶ 모양: {shape_name} (seq={shape_seq})")
        # Step 1: 이 모양의 사이즈 매핑
        size_map = self._map_size_names(page, shape_seq, shape_name)
        if not size_map:
            log.warning(f"  [{shape_name}] 사이즈 매핑 실패")
            return
        log.info(f"  [{shape_name}] 사이즈 {len(size_map)}종 확인")
        # Step 2: 각 사이즈 × 각 원단(용지)
        for sz in size_map:
            size_seq = sz["seq"]
            size_name = sz["name"]
            base_selections = sz["default_selections"]
            log.info(f"\\n  ▷ 사이즈: {size_name}")
            for paper_seq in paper_pov:
                try:
                    # a) 사이즈 선택 상태에서 원단(용지) 교체
                    sel_data = self._api_select_option(page, size_seq)
                    if not sel_data:
                        continue
                    selections = [
                        opt.get("selected")
                        for opt in sel_data["optionList"]
                    ]
                    # b) 원단 교체
                    paper_idx = self._find_option_index(
                        sel_data["optionList"], "원단"
                    )
                    if paper_idx < 0:
                        paper_idx = self._find_option_index(
                            sel_data["optionList"], "용지"
                        )
                    if paper_idx >= 0:
                        paper_pov_current = sel_data["optionList"][paper_idx].get("povList", [])
                        if paper_seq in paper_pov_current:
                            # selectOption으로 원단 교체
                            paper_sel_data = self._api_select_option(page, paper_seq)
                            if paper_sel_data:
                                selections = [
                                    opt.get("selected")
                                    for opt in paper_sel_data["optionList"]
                                ]
                        else:
                            log.info(f"      용지 seq {paper_seq} 미지원 (이 사이즈에서)")
                            continue
                    # c) 매수를 1,000매로 강제 교체
                    qty_idx = self._find_option_index(
                        sel_data["optionList"], "매수"
                    )
                    if qty_idx >= 0 and qty_idx < len(selections):
                        # 현재 매수 povList에 1000매 seq가 있는지 확인
                        # paper 교체 후에는 sel_data가 바뀌었으므로 paper_sel_data 사용
                        check_data = paper_sel_data if paper_sel_data else sel_data
                        qty_pov_current = check_data["optionList"][qty_idx].get("povList", [])
                        if qty_seq in qty_pov_current:
                            selections[qty_idx] = qty_seq
                        else:
                            log.info(f"      1000매 미지원 (용지 {paper_seq})")
                            continue
                    # d) URL 네비게이션 → 가격 읽기
                    result = self._navigate_and_read(page, selections)
                    paper_name = result["paper_name"] or f"용지_{paper_seq}"
                    price = result["price"]
                    opts = result["opts"]
                    log.info(
                        f"      {paper_name} → {price}"
                    )
                    self.results.append({
                        "product": STICKER_PRODUCT["name"],
                        "shape": shape_name,
                        "size": opts.get("사이즈", size_name),
                        "paper": paper_name,
                        "paper_seq": paper_seq,
                        "qty": opts.get("수량", "1,000"),
                        "price": price,
                        "url": result["url"],
                    })
                except Exception as e:
                    log.warning(
                        f"      [{shape_name}/{size_name}/paper_{paper_seq}] "
                        f"오류: {e}"
                    )
                    continue
    def run(self):
        """전체 크롤링 실행"""
        log.info("=" * 60)
        log.info("비즈하우스 스티커 크롤링 시작")
        log.info(f"제품: {STICKER_PRODUCT['name']}")
        log.info(f"모양: {[s['name'] for s in SHAPES]}")
        log.info(f"고정 매수: 1,000매 (seq={QTY_1000_SEQ})")
        log.info("=" * 60)
        start = time.time()
        with sync_playwright() as pw:
            browser, context = self._init_browser(pw)
            page = context.new_page()
            # Step 1: 제품 페이지 접속 (API 도메인 컨텍스트 확보)
            base_url = self._build_base_url()
            try:
                page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2000)
            except PwTimeout:
                log.error("제품 페이지 로딩 타임아웃")
                browser.close()
                return
            # Step 2: combination API → 원단(용지) povList 확보
            combo_data = self._api_combination(page)
            if not combo_data:
                log.error("combination API 실패")
                browser.close()
                return
            option_list = combo_data.get("optionList", [])
            # 원단 povList
            paper_idx = self._find_option_index(option_list, "원단")
            if paper_idx < 0:
                paper_idx = self._find_option_index(option_list, "용지")
            if paper_idx < 0:
                log.error("원단/용지 옵션을 찾을 수 없음")
                browser.close()
                return
            paper_pov = option_list[paper_idx].get("povList", [])
            log.info(f"원단(용지) {len(paper_pov)}종 발견")
            # Step 3: 1,000매 seq 확인
            qty_seq = self._find_qty_1000_seq(page, option_list)
            log.info(f"1,000매 seq: {qty_seq}")
            # Step 4: 모양별 크롤링
            for shape in SHAPES:
                try:
                    self._crawl_shape(page, shape, paper_pov, qty_seq)
                except Exception as e:
                    log.error(f"[{shape['name']}] 크롤링 오류: {e}", exc_info=True)
            browser.close()
        elapsed = time.time() - start
        log.info(f"\\n{'='*60}")
        log.info(f"크롤링 완료: {len(self.results)}건, {elapsed:.1f}초")
        log.info("=" * 60)
        self._save_results()
    # ── 결과 저장 ─────────────────────────────────────────
    def _save_results(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        outdir = Path("output")
        outdir.mkdir(exist_ok=True)
        # JSON 저장
        json_path = outdir / f"bizhows_sticker_{timestamp}.json"
        output_data = {
            "crawled_at": datetime.now().isoformat(),
            "source": "bizhows.com",
            "category": "스티커",
            "product": STICKER_PRODUCT["name"],
            "fixed_options": {
                "quantity": "1,000매",
                "qty_seq": QTY_1000_SEQ,
                "shapes": [s["name"] for s in SHAPES],
            },
            "total_records": len(self.results),
            "records": self.results,
        }
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        log.info(f"JSON 저장: {json_path}")
        # 요약
        success = [r for r in self.results if r.get("price")]
        fail = [r for r in self.results if not r.get("price")]
        log.info(f"성공: {len(success)}건, 가격없음: {len(fail)}건")
# ── 실행 ────────────────────────────────────────────────────
if __name__ == "__main__":
    crawler = BizhowsStickerCrawler(headless=True)
    crawler.run()