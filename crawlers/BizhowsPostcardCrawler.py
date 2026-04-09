"""
비즈하우스(bizhows.com) 엽서카드 가격 크롤러
- 엽서카드 제품 페이지에서 용지별 가격을 수집
- 고정 옵션: 102x152mm / 200매
- Playwright 기반 (combination API + URL 네비게이션 방식)
"""
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
# ── 설정 ──────────────────────────────────────────────────
BASE_URL = "https://www.bizhows.com/ko/v/option"
CODE1 = "9900"
CODE2 = "200"
CODE3 = "9901"
MOCK = "9900_200_9901"
# 목표 사이즈: 102x152mm → extraData에서 value1≈10.2~10.6, value2≈15.2~15.6 (cm)
TARGET_SIZE_WIDTH_CM = 10.6   # 102mm
TARGET_SIZE_HEIGHT_CM = 15.6  # 152mm
SIZE_TOLERANCE_CM = 0.5       # 허용 오차
TARGET_QTY = 200  # 목표 매수
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
class BizhowsPostcardCrawler:
    """비즈하우스 엽서카드 크롤러"""
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
        url = f"/api/v1/option/combination/{CODE1}/{CODE2}/{CODE3}/{MOCK}"
        try:
            result = page.evaluate(f"""() => {{
                return fetch('{url}')
                    .then(r => r.json())
                    .then(data => data.data);
            }}""")
            return result
        except Exception as e:
            log.error(f"combination API 실패: {e}")
            return None
    def _api_select_option(self, page, option_seq: int) -> dict | None:
        """selectOption API 호출 → 특정 옵션 선택 후 구조 반환"""
        url = f"/api/v1/option/combination/{CODE1}/{CODE2}/{CODE3}/{MOCK}?selectOption={option_seq}"
        try:
            result = page.evaluate(f"""() => {{
                return fetch('{url}')
                    .then(r => r.json())
                    .then(data => data.data);
            }}""")
            return result
        except Exception as e:
            log.error(f"selectOption API 실패 (seq={option_seq}): {e}")
            return None
    # ── 동적 옵션 탐색 ────────────────────────────────────
    def _find_option_indices(self, option_list: list) -> dict:
        """옵션 리스트에서 종류/원단/사이즈/매수의 인덱스를 동적으로 탐색"""
        indices = {"종류": -1, "원단": -1, "사이즈": -1, "매수": -1}
        for i, opt in enumerate(option_list):
            name = opt.get("poiName", "")
            if name in indices:
                indices[name] = i
            elif name == "용지":
                indices["원단"] = i
        return indices
    def _find_target_size_seq(self, page, size_pov_list: list) -> int | None:
        """사이즈 옵션 중 102x152mm에 해당하는 seq를 extraData로 탐색"""
        for size_seq in size_pov_list:
            data = self._api_select_option(page, size_seq)
            if not data:
                continue
            try:
                extra = json.loads(data["extraData"].replace("%5B", "[")
                    .replace("%5D", "]").replace("%7B", "{")
                    .replace("%7D", "}").replace("%22", '"')
                    .replace("%3A", ":").replace("%2C", ","))
            except Exception:
                try:
                    from urllib.parse import unquote
                    extra = json.loads(unquote(data["extraData"]))
                except Exception:
                    continue
            if not extra or not isinstance(extra, list):
                continue
            for item in extra:
                w = float(item.get("value1", 0))
                h = float(item.get("value2", 0))
                # 102x152mm = 약 10.2~10.6 x 15.2~15.6 cm
                if (abs(w - TARGET_SIZE_WIDTH_CM) < SIZE_TOLERANCE_CM and
                    abs(h - TARGET_SIZE_HEIGHT_CM) < SIZE_TOLERANCE_CM):
                    log.info(f"  102x152mm 사이즈 발견: seq={size_seq} ({w}x{h}cm)")
                    return size_seq
        log.warning("  102x152mm 사이즈를 찾지 못함")
        return None
    def _find_target_qty_seq(self, page, base_url: str,
                              option_indices: dict, selections: list,
                              qty_pov_list: list) -> int | None:
        """매수 옵션 중 200매에 해당하는 seq를 DOM 네비게이션으로 탐색"""
        qty_idx = option_indices["매수"]
        for qty_seq in qty_pov_list:
            test_selections = selections.copy()
            test_selections[qty_idx] = qty_seq
            sel_str = ",".join(str(s) for s in test_selections)
            nav_url = f"{base_url}&selectedOptionList={sel_str}"
            try:
                page.goto(nav_url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_function(
                    "() => document.body.innerText.includes('총 금액')",
                    timeout=10000,
                )
                page.wait_for_timeout(500)
            except PwTimeout:
                continue
            page_data = page.evaluate(JS_READ_PAGE)
            qty_text = page_data["opts"].get("매수", "")
            try:
                qty_val = int(qty_text.replace(",", "").strip())
                if qty_val == TARGET_QTY:
                    log.info(f"  {TARGET_QTY}매 seq 발견: {qty_seq}")
                    return qty_seq
            except (ValueError, TypeError):
                continue
        log.warning(f"  {TARGET_QTY}매 seq를 찾지 못함")
        return None
    # ── 크롤링 ──────────────────────────────────────────
    def run(self):
        log.info("=" * 60)
        log.info("비즈하우스 엽서카드 크롤링 시작")
        log.info("=" * 60)
        start = time.time()
        with sync_playwright() as pw:
            browser, context = self._init_browser(pw)
            page = context.new_page()
            base_url = (
                f"{BASE_URL}?code1={CODE1}&code2={CODE2}"
                f"&code3={CODE3}&mock={MOCK}"
                f"&from=product_list_005"
            )
            # Step 1: 초기 페이지 로딩 (API 도메인 컨텍스트 확보)
            log.info(f"▶ 엽서카드 페이지 접속: {base_url}")
            try:
                page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)
            except PwTimeout:
                log.error("페이지 로딩 타임아웃")
                browser.close()
                return
            # Step 2: combination API → 옵션 구조 파악
            combo_data = self._api_combination(page)
            if not combo_data:
                log.error("combination API 실패")
                browser.close()
                return
            option_list = combo_data.get("optionList", [])
            indices = self._find_option_indices(option_list)
            log.info(f"옵션 인덱스: {indices}")
            if indices["원단"] < 0:
                log.error("원단(용지) 옵션을 찾을 수 없음")
                browser.close()
                return
            paper_pov_list = option_list[indices["원단"]]["povList"]
            log.info(f"용지 {len(paper_pov_list)}종 발견: {paper_pov_list}")
            # Step 3: 기본 selections 구성
            default_selections = [opt["selected"] for opt in option_list]
            log.info(f"기본 selections: {default_selections}")
            # Step 4: 102x152mm 사이즈 seq 탐색
            size_pov_list = option_list[indices["사이즈"]]["povList"]
            target_size_seq = self._find_target_size_seq(page, size_pov_list)
            if not target_size_seq:
                log.error("102x152mm 사이즈를 찾을 수 없음 - 크롤링 중단")
                browser.close()
                return
            # Step 5: 200매 seq 탐색
            # 사이즈 선택 후 매수 목록을 가져오기
            size_data = self._api_select_option(page, target_size_seq)
            if not size_data:
                log.error("사이즈 선택 API 실패")
                browser.close()
                return
            size_option_list = size_data["optionList"]
            qty_pov_list = size_option_list[indices["매수"]]["povList"]
            # 사이즈 선택 후의 selections를 기준으로 매수 탐색
            size_selections = [opt["selected"] for opt in size_option_list]
            target_qty_seq = self._find_target_qty_seq(
                page, base_url, indices, size_selections, qty_pov_list
            )
            if not target_qty_seq:
                log.error(f"{TARGET_QTY}매를 찾을 수 없음 - 크롤링 중단")
                browser.close()
                return
            # Step 6: 각 용지별로 URL 네비게이션 → 가격 수집
            log.info(f"\\n{'='*60}")
            log.info(f"용지별 가격 수집 시작 (사이즈={target_size_seq}, 매수={target_qty_seq})")
            log.info(f"{'='*60}")
            for i, paper_seq in enumerate(paper_pov_list):
                log.info(f"\\n[{i+1}/{len(paper_pov_list)}] 용지 seq={paper_seq}")
                # selectOption으로 이 용지의 기본 selections 조회
                paper_data = self._api_select_option(page, paper_seq)
                if not paper_data:
                    self.results.append({
                        "paper_seq": paper_seq,
                        "error": "selectOption API 실패",
                    })
                    continue
                paper_selections = [opt["selected"] for opt in paper_data["optionList"]]
                # 사이즈와 매수를 고정값으로 교체
                paper_selections[indices["사이즈"]] = target_size_seq
                paper_selections[indices["매수"]] = target_qty_seq
                # URL 구성 및 네비게이션
                sel_str = ",".join(str(s) for s in paper_selections)
                nav_url = f"{base_url}&selectedOptionList={sel_str}"
                try:
                    page.goto(nav_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_function(
                        "() => document.body.innerText.includes('총 금액')",
                        timeout=15000,
                    )
                    page.wait_for_timeout(500)
                except PwTimeout:
                    log.warning(f"  페이지 로딩/총금액 대기 타임아웃")
                # DOM에서 용지명 + 가격 읽기
                page_data = page.evaluate(JS_READ_PAGE)
                opts = page_data["opts"]
                price = page_data["price"]
                paper_name = opts.get("용지", opts.get("원단", f"용지_{paper_seq}"))
                log.info(f"  ✓ {paper_name} | {opts.get('사이즈', '')} | "
                         f"{opts.get('매수', '')}매 | {price}")
                self.results.append({
                    "paper_seq": paper_seq,
                    "paper_name": paper_name,
                    "size": opts.get("사이즈", ""),
                    "quantity": opts.get("매수", ""),
                    "price": price,
                    "url": nav_url,
                    "crawled_at": datetime.now().isoformat(),
                })
            browser.close()
        elapsed = time.time() - start
        log.info(f"\\n{'='*60}")
        log.info(f"크롤링 완료: {len(self.results)}건, {elapsed:.1f}초")
        log.info(f"{'='*60}")
        self._save_results()
    def _save_results(self):
        """결과를 JSON 파일로 저장"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        outdir = Path("output")
        outdir.mkdir(exist_ok=True)
        data = {
            "crawled_at": datetime.now().isoformat(),
            "source": "bizhows.com",
            "product": "엽서카드",
            "page_url": f"{BASE_URL}?code1={CODE1}&code2={CODE2}&code3={CODE3}&mock={MOCK}",
            "fixed_options": {
                "size": "102x152mm",
                "quantity": TARGET_QTY,
            },
            "total_records": len(self.results),
            "papers": self.results,
        }
        json_path = outdir / f"bizhows_postcard_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log.info(f"JSON 저장: {json_path}")
        # 요약 출력
        success = [r for r in self.results if "error" not in r and r.get("price")]
        fail = [r for r in self.results if "error" in r or not r.get("price")]
        log.info(f"성공: {len(success)}건, 실패: {len(fail)}건")
        if fail:
            for r in fail:
                log.info(f"  - {r.get('paper_name', '?')}: {r.get('error', '가격없음')}")
# ── 실행 ────────────────────────────────────────────────────
if __name__ == "__main__":
    crawler = BizhowsPostcardCrawler(headless=True)
    crawler.run()