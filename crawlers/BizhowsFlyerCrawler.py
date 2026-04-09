"""
비즈하우스(bizhows.com) 전단지 가격 크롤러 v4.0
수집 대상:
 1) 대량 특가 전단지 (양면) — A4, 양면 고정, 4000매, 용지 3종
 2) 소량 고급 전단지 (양면) — A4, 양면 고정, 500매, 용지 3종
핵심:
 - combination API → 옵션 구조 파악
 - selectOption API → 용지 변경 시 매수 리셋 버그 대응 (4000매/500매 seq 강제 교체)
 - URL selectedOptionList 네비게이션 → DOM에서 용지명/가격 읽기
 - API "원단" ↔ DOM "용지" 라벨 불일치 → 다중 alias fallback
"""
import json
import re
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
# ── 제품 목록 ──────────────────────────────────────────────
PRODUCTS = [
    {
        "category": "대량특가",
        "name": "대량 특가 전단지 (양면)",
        "mock": "8100_200_8004",
        "code1": "8100", "code2": "200", "code3": "8004",
        "target_qty": 4000,
        # 검증 완료된 고정 seq
        "a4_seq": 64354,      # 세로형 A4(21.0cm x 29.7cm) — 기본값
        "qty_seq": 64358,     # 4000매 — 모든 용지에서 유효
    },
    {
        "category": "소량고급",
        "name": "소량 고급 전단지 (양면)",
        "mock": "8100_200_8002",
        "code1": "8100", "code2": "200", "code3": "8002",
        "target_qty": 500,
        # 검증 완료된 고정 seq
        "a4_seq": 64332,      # 세로형 A4(21.0cm x 29.7cm) — 기본값
        "qty_seq": 64342,     # 500매 — 모든 용지에서 유효
    },
]
BLOCK_PATTERNS = [
    "**/f.clarity.ms/**", "**/analytics.tiktok.com/**",
    "**/channel.io/**", "**/criteo.net/**", "**/criteo.com/**",
    "**/google-analytics.com/**", "**/googletagmanager.com/**",
    "**/facebook.net/**", "**/facebook.com/tr/**",
    "**/doubleclick.net/**", "**/ads-twitter.com/**",
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
class BizhowsFlyerCrawler:
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
    def _build_base_url(self, product: dict) -> str:
        c1, c2, c3, mock = product["code1"], product["code2"], product["code3"], product["mock"]
        return f"{BASE_URL}?code1={c1}&code2={c2}&code3={c3}&mock={mock}&from=product_list_005"
    # ── API 호출 ──────────────────────────────────────────
    def _api_combination(self, page, product: dict) -> dict | None:
        c1, c2, c3, mock = product["code1"], product["code2"], product["code3"], product["mock"]
        url = f"/api/v1/option/combination/{c1}/{c2}/{c3}/{mock}"
        try:
            return page.evaluate(f"""() => {{
                return fetch('{url}').then(r => r.json()).then(d => d.data);
            }}""")
        except Exception as e:
            log.error(f"  combination API 실패: {e}")
            return None
    def _api_select_option(self, page, product: dict, option_seq: int) -> dict | None:
        c1, c2, c3, mock = product["code1"], product["code2"], product["code3"], product["mock"]
        url = f"/api/v1/option/combination/{c1}/{c2}/{c3}/{mock}?selectOption={option_seq}"
        try:
            return page.evaluate(f"""() => {{
                return fetch('{url}').then(r => r.json()).then(d => d.data);
            }}""")
        except Exception as e:
            log.error(f"  selectOption API 실패 (seq={option_seq}): {e}")
            return None
    # ── 옵션 인덱스 동적 탐색 ─────────────────────────────
    def _find_option_indices(self, option_list: list) -> dict:
        indices = {"paper": -1, "size": -1, "side": -1, "qty": -1}
        for i, opt in enumerate(option_list):
            name = opt.get("poiName", "")
            if name in ("원단", "용지"):
                indices["paper"] = i
            elif name == "사이즈":
                indices["size"] = i
            elif name in ("양/단면", "양단면"):
                indices["side"] = i
            elif name in ("매수", "수량"):
                indices["qty"] = i
        return indices
    # ── DOM에서 용지명 읽기 (API "원단" ↔ DOM "용지" 대응) ─
    @staticmethod
    def _read_paper_name(opts: dict) -> str:
        """DOM opts에서 용지명 추출. API는 '원단'이지만 DOM은 '용지'로 표시됨"""
        return opts.get("용지") or opts.get("원단") or ""
    @staticmethod
    def _read_qty(opts: dict) -> str:
        return opts.get("매수") or opts.get("수량") or ""
    # ── 단일 제품 크롤링 ──────────────────────────────────
    def _crawl_product(self, page, product: dict):
        base_url = self._build_base_url(product)
        log.info(f"▶ {product['category']} / {product['name']} ({product['target_qty']}매)")
        # Step 1: 페이지 로딩 (API 호출용 도메인 컨텍스트 확보)
        try:
            page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
        except PwTimeout:
            log.error(f"  ✖ 페이지 로딩 타임아웃")
            self.results.append({
                "category": product["category"], "product": product["name"],
                "error": "페이지 로딩 타임아웃", "url": base_url,
            })
            return
        # Step 2: combination API → 용지 목록 확보
        combo_data = self._api_combination(page, product)
        if not combo_data:
            self.results.append({
                "category": product["category"], "product": product["name"],
                "error": "combination API 실패", "url": base_url,
            })
            return
        option_list = combo_data.get("optionList", [])
        indices = self._find_option_indices(option_list)
        paper_idx = indices["paper"]
        if paper_idx < 0:
            self.results.append({
                "category": product["category"], "product": product["name"],
                "error": "용지 옵션 없음", "url": base_url,
            })
            return
        paper_pov_list = option_list[paper_idx].get("povList", [])
        log.info(f"  용지 {len(paper_pov_list)}종 크롤링 시작")
        # Step 3: 용지별 순회
        for i, paper_seq in enumerate(paper_pov_list):
            log.info(f"  [{i+1}/{len(paper_pov_list)}] 용지 seq={paper_seq}")
            # selectOption API → 용지 변경 후 selections 확보
            select_data = self._api_select_option(page, product, paper_seq)
            if not select_data:
                self.results.append({
                    "category": product["category"], "product": product["name"],
                    "paper": f"seq_{paper_seq}", "error": "selectOption API 실패",
                })
                continue
            select_options = select_data.get("optionList", [])
            selections = [opt.get("selected") for opt in select_options]
            # A4 사이즈 강제 적용
            if indices["size"] >= 0 and indices["size"] < len(selections):
                selections[indices["size"]] = product["a4_seq"]
            # 매수 강제 적용 (selectOption이 매수를 리셋할 수 있으므로)
            if indices["qty"] >= 0 and indices["qty"] < len(selections):
                selections[indices["qty"]] = product["qty_seq"]
            # URL 네비게이션 → DOM 읽기
            sel_str = ",".join(str(s) for s in selections)
            nav_url = f"{base_url}&selectedOptionList={sel_str}"
            try:
                page.goto(nav_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_function(
                    "() => document.body.innerText.includes('총 금액')",
                    timeout=15000,
                )
                page.wait_for_timeout(500)
            except PwTimeout:
                log.warning(f"    페이지 로딩/총금액 대기 타임아웃")
            page_data = page.evaluate(JS_READ_PAGE)
            opts = page_data.get("opts", {})
            paper_name = self._read_paper_name(opts)
            price = page_data.get("price")
            qty_text = self._read_qty(opts)
            size_text = opts.get("사이즈", "")
            # 검증
            qty_num = re.sub(r"[^\\d]", "", qty_text)
            if qty_num and int(qty_num) != product["target_qty"]:
                log.warning(f"    매수 불일치: 기대={product['target_qty']}, 실제={qty_num}")
            if not paper_name:
                paper_name = f"용지_{paper_seq}"
                log.warning(f"    용지명 읽기 실패 → fallback: {paper_name}")
            log.info(f"    ✔ {paper_name} | {size_text} | {qty_text}매 → {price}")
            self.results.append({
                "category": product["category"],
                "product": product["name"],
                "paper": paper_name,
                "size": size_text,
                "qty": qty_text,
                "price": price,
                "url": nav_url,
            })
    def run(self):
        log.info(f"=== 비즈하우스 전단지 크롤링 시작 ({len(PRODUCTS)}개 제품) ===")
        start = time.time()
        with sync_playwright() as pw:
            browser, context = self._init_browser(pw)
            page = context.new_page()
            for idx, product in enumerate(PRODUCTS):
                log.info(f"\\n{'='*60}")
                log.info(f"[{idx+1}/{len(PRODUCTS)}] {product['name']}")
                try:
                    self._crawl_product(page, product)
                except Exception as e:
                    log.error(f"  ✖ 예외: {e}", exc_info=True)
                    self.results.append({
                        "category": product["category"],
                        "product": product["name"],
                        "error": str(e),
                    })
            browser.close()
        elapsed = time.time() - start
        log.info(f"\\n=== 크롤링 완료: {len(self.results)}건, {elapsed:.1f}초 ===")
        self._save_results()
    def _save_results(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        outdir = Path("output")
        outdir.mkdir(exist_ok=True)
        # JSON
        json_path = outdir / f"bizhows_flyer_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2)
        log.info(f"JSON 저장: {json_path}")
        # 요약
        success = [r for r in self.results if "error" not in r and r.get("price")]
        fail = [r for r in self.results if "error" in r or not r.get("price")]
        log.info(f"성공: {len(success)}건, 실패: {len(fail)}건")
        if fail:
            for r in fail:
                log.info(f"  ✖ {r.get('product','?')} / {r.get('paper','?')}: "
                         f"{r.get('error','가격없음')}")
if __name__ == "__main__":
    crawler = BizhowsFlyerCrawler(headless=True)
    crawler.run()