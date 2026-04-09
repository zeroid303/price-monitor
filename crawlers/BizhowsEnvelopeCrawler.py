"""
비즈하우스(bizhows.com) 봉투 가격 크롤러 v4
- 대봉투/소봉투만 크롤링 (각대봉투 제외)
- 후가공 없음 + 500매 기준
- 명함 크롤러 v5.1 패턴: combination API + selectOption API + URL 네비게이션 + DOM 가격 읽기
- 별도 price API 사용하지 않음 (v3 실패 원인)
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
# ── 설정 ──────────────────────────────────────────────────
PRODUCT_KEYWORDS = ["대봉투", "소봉투"]
EXCLUDE_KEYWORDS = ["각대봉투", "각대"]
QTY_TARGET = "500"  # 목표 매수
FINISH_TARGET = "후가공 없음"  # 목표 후가공
SEARCH_URL = "https://www.bizhows.com/ko/v/search?keyword=%EB%B4%89%ED%88%AC"
BASE_URL = "https://www.bizhows.com/ko/v/option"
CMS_BASE = "https://asset.cms.miricanvas.com/resources/content/cp/bizhows/ko/commodity-option"
API_BASE = "/api/v1/option/combination"
BLOCK_PATTERNS = [
    "**/f.clarity.ms/**", "**/analytics.tiktok.com/**", "**/channel.io/**",
    "**/criteo.net/**", "**/criteo.com/**", "**/google-analytics.com/**",
    "**/googletagmanager.com/**", "**/facebook.net/**", "**/facebook.com/tr/**",
    "**/doubleclick.net/**", "**/ads-twitter.com/**",
]
# ── JS: DOM에서 현재 옵션 + 가격 읽기 ─────────────────────
JS_READ_PAGE = r"""() => {
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
        const m = document.body.innerText.match(/총\\s*금액\\s*([\\d,]+원)/);
        if (m) price = m[1];
    }
    return {opts, price};
}"""
# ── JS: 특정 라벨의 드롭다운 열어서 항목 읽기 ─────────────
JS_READ_DROPDOWN = r"""(labelName) => {
    return new Promise((resolve) => {
        const dwRows = document.querySelectorAll('[data-f^="DW-"]');
        for (const dw of dwRows) {
            const dts = dw.querySelectorAll('[data-f^="DT-"]');
            let isTarget = false;
            for (const dt of dts) {
                if (dt.textContent.trim() === labelName) { isTarget = true; break; }
            }
            if (!isTarget) continue;
            const dd = dw.querySelector('[data-f^="DD-"]');
            if (dd) dd.click();
            setTimeout(() => {
                const ol = dw.querySelector('[data-f^="OL-"]');
                const labels = [];
                if (ol) {
                    const odItems = ol.querySelectorAll('[data-f^="OD-"]');
                    for (const od of odItems) {
                        const span = od.querySelector('[data-f^="OT-"]');
                        labels.push(span ? span.textContent.trim() : od.textContent.trim());
                    }
                }
                document.body.click();
                resolve(labels);
            }, 600);
        }
        setTimeout(() => resolve([]), 700);
    });
}"""
class BizhowsEnvelopeCrawler:
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
    # ── 검색 페이지에서 제품 목록 수집 ────────────────────
    def _collect_products(self, page) -> list[dict]:
        """검색 페이지에서 대봉투/소봉투 제품만 필터링하여 수집"""
        log.info("검색 페이지에서 제품 수집 중...")
        page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
        # 스크롤하여 모든 제품 로드
        for _ in range(20):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(800)
        # 제품 링크 수집
        products_raw = page.evaluate(r"""() => {
            const anchors = document.querySelectorAll('a');
            const result = [];
            const seenMock = new Set();
            anchors.forEach(a => {
                const text = a.textContent.trim();
                if (text.length > 100) return;
                const href = a.getAttribute('href') || '';
                const mockMatch = href.match(/mock=([^&]+)/);
                if (!mockMatch) return;
                const mock = mockMatch[1];
                if (seenMock.has(mock)) return;
                // 이름 추출 (숫자/가격 앞부분)
                const name = text.split(/\\d+매|\\d+장|\\d+,\\d+원|\\d+원/)[0].trim();
                if (!name) return;
                seenMock.add(mock);
                result.push({name, mock});
            });
            return result;
        }""")
        # 필터링: 대봉투/소봉투 포함 + 각대봉투 제외
        filtered = []
        for p in products_raw:
            name = p["name"]
            has_keyword = any(kw in name for kw in PRODUCT_KEYWORDS)
            has_exclude = any(kw in name for kw in EXCLUDE_KEYWORDS)
            if has_keyword and not has_exclude:
                filtered.append(p)
                log.info(f"  ✓ {name} (mock={p['mock']})")
        log.info(f"필터링 결과: {len(filtered)}개 제품")
        return filtered
    # ── CMS API로 실제 코드 조회 ──────────────────────────
    def _get_real_codes(self, page, mock: str) -> dict | None:
        """CMS API에서 실제 code1/code2/code3 조회 (URL 코드와 다를 수 있음)"""
        try:
            result = page.evaluate(f"""() => {{
                return fetch('{CMS_BASE}/{mock}')
                    .then(r => r.json())
                    .then(data => data.key);
            }}""")
            if result:
                return {
                    "code1": str(result.get("code1", "")),
                    "code2": str(result.get("code2", "")),
                    "code3": str(result.get("code3", "")),
                }
        except Exception as e:
            log.error(f"  CMS API 실패 ({mock}): {e}")
        return None
    # ── Combination API 호출 ──────────────────────────────
    def _api_combination(self, page, c1, c2, c3, mock) -> dict | None:
        try:
            result = page.evaluate(f"""() => {{
                return fetch('{API_BASE}/{c1}/{c2}/{c3}/{mock}')
                    .then(r => r.json())
                    .then(data => data.data);
            }}""")
            return result
        except Exception as e:
            log.error(f"  combination API 실패: {e}")
            return None
    # ── SelectOption API 호출 ─────────────────────────────
    def _api_select_option(self, page, c1, c2, c3, mock, option_seq) -> dict | None:
        try:
            result = page.evaluate(f"""() => {{
                return fetch('{API_BASE}/{c1}/{c2}/{c3}/{mock}?selectOption={option_seq}')
                    .then(r => r.json())
                    .then(data => data.data);
            }}""")
            return result
        except Exception as e:
            log.error(f"  selectOption API 실패 (seq={option_seq}): {e}")
            return None
    # ── 옵션 구조 분석 ────────────────────────────────────
    def _analyze_options(self, option_list: list) -> dict:
        """옵션 리스트에서 각 그룹의 인덱스와 역할을 파악"""
        analysis = {
            "variable_idx": -1,   # 원단/용지/사이즈/상품명 (여러 옵션 순회 대상)
            "variable_name": "",
            "qty_idx": -1,        # 매수/수량
            "qty_api_name": "",   # API에서의 이름 (봉투 매수, 수량 등)
            "finish_idx": -1,     # 후가공
            "finish_api_name": "",
        }
        for i, opt in enumerate(option_list):
            api_name = opt.get("poiName", "")
            pov_count = len(opt.get("povList", []))
            # 매수/수량 찾기
            if api_name in ("봉투 매수", "매수", "수량"):
                analysis["qty_idx"] = i
                analysis["qty_api_name"] = api_name
            # 후가공 찾기
            elif api_name in ("봉투 후가공", "후가공"):
                analysis["finish_idx"] = i
                analysis["finish_api_name"] = api_name
            # 변수 옵션 찾기 (여러 값이 있고, 매수/후가공이 아닌 것)
            elif pov_count > 1 and api_name not in ("봉투 매수", "매수", "수량", "봉투 후가공", "후가공"):
                analysis["variable_idx"] = i
                analysis["variable_name"] = api_name
        return analysis
    # ── 드롭다운 라벨 → 500매 seq 찾기 ───────────────────
    def _find_qty_500_seq(self, page, option_list, qty_idx, dom_label) -> tuple:
        """
        드롭다운을 열어 라벨을 읽고, povList와 위치 매칭하여 500매 seq 반환
        Returns: (seq_500, all_qty_labels)
        """
        if qty_idx < 0:
            return None, []
        pov_list = option_list[qty_idx].get("povList", [])
        # DOM에서 드롭다운 라벨 읽기
        labels = page.evaluate(JS_READ_DROPDOWN, dom_label)
        log.info(f"  {dom_label} 드롭다운 라벨: {labels}")
        if not labels or len(labels) != len(pov_list):
            log.warning(f"  라벨 수({len(labels)}) != povList 수({len(pov_list)})")
            # 라벨 수가 맞지 않으면 기본 selected 사용
            return option_list[qty_idx].get("selected"), labels
        # 500 찾기
        for pos, label in enumerate(labels):
            if label.strip() == QTY_TARGET:
                seq = pov_list[pos]
                log.info(f"  500매 발견: position={pos}, seq={seq}")
                return seq, labels
        log.warning(f"  500매 옵션 없음. 사용 가능: {labels}")
        return None, labels
    # ── 드롭다운 라벨 → 후가공없음 seq 찾기 ──────────────
    def _find_finish_none_seq(self, page, option_list, finish_idx, dom_label) -> int | None:
        """후가공 드롭다운에서 '후가공 없음' seq 반환"""
        if finish_idx < 0:
            return None
        pov_list = option_list[finish_idx].get("povList", [])
        if len(pov_list) == 1:
            log.info(f"  후가공 옵션 1개뿐 → seq={pov_list[0]} 사용")
            return pov_list[0]
        labels = page.evaluate(JS_READ_DROPDOWN, dom_label)
        log.info(f"  {dom_label} 드롭다운 라벨: {labels}")
        if not labels or len(labels) != len(pov_list):
            log.warning(f"  후가공 라벨 매칭 실패, 기본값 사용")
            return option_list[finish_idx].get("selected")
        for pos, label in enumerate(labels):
            if FINISH_TARGET in label:
                seq = pov_list[pos]
                log.info(f"  후가공 없음 발견: position={pos}, seq={seq}")
                return seq
        log.warning(f"  '후가공 없음' 미발견. 사용 가능: {labels}")
        return option_list[finish_idx].get("selected")
    # ── DOM 라벨 매핑 ─────────────────────────────────────
    def _get_dom_labels(self, page) -> dict:
        """현재 페이지 DOM에서 보이는 옵션 라벨명을 수집"""
        return page.evaluate(r"""() => {
            const labels = {};
            const seen = new Set();
            const dwRows = document.querySelectorAll('[data-f^="DW-"]');
            for (const dw of dwRows) {
                const ol = dw.querySelector('[data-f^="OL-"]');
                if (!ol) continue;
                const dts = dw.querySelectorAll('[data-f^="DT-"]');
                for (const dt of dts) {
                    const t = dt.textContent.trim();
                    if (t && !seen.has(t)) {
                        seen.add(t);
                        labels[t] = true;
                        break;
                    }
                }
            }
            return Object.keys(labels);
        }""")
    # ── 단일 제품 크롤링 ──────────────────────────────────
    def _crawl_product(self, page, product: dict):
        name = product["name"]
        mock = product["mock"]
        log.info(f"▶ {name} (mock={mock})")
        # Step 1: CMS에서 실제 코드 조회
        codes = self._get_real_codes(page, mock)
        if not codes:
            log.error(f"  ✖ CMS 코드 조회 실패")
            self.results.append({"product": name, "mock": mock, "error": "CMS 코드 조회 실패"})
            return
        c1, c2, c3 = codes["code1"], codes["code2"], codes["code3"]
        log.info(f"  실제 코드: {c1}/{c2}/{c3}")
        # Step 2: 제품 페이지 이동 (API 호출용 도메인 컨텍스트)
        option_url = f"{BASE_URL}?code1={c1}&code2={c2}&code3={c3}&mock={mock}"
        try:
            page.goto(option_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
        except PwTimeout:
            log.error(f"  ✖ 페이지 로딩 타임아웃")
            self.results.append({"product": name, "mock": mock, "error": "페이지 로딩 타임아웃"})
            return
        # Step 3: Combination API → 옵션 구조 파악
        combo_data = self._api_combination(page, c1, c2, c3, mock)
        if not combo_data:
            log.error(f"  ✖ combination API 실패")
            self.results.append({"product": name, "mock": mock, "error": "combination API 실패"})
            return
        option_list = combo_data.get("optionList", [])
        analysis = self._analyze_options(option_list)
        log.info(f"  옵션 분석: variable={analysis['variable_name']}(idx={analysis['variable_idx']}), "
                 f"qty={analysis['qty_api_name']}(idx={analysis['qty_idx']}), "
                 f"finish={analysis['finish_api_name']}(idx={analysis['finish_idx']})")
        # Step 4: DOM 라벨 확인 (API 이름과 DOM 이름이 다를 수 있음)
        dom_labels = self._get_dom_labels(page)
        log.info(f"  DOM 라벨: {dom_labels}")
        # 매수 DOM 라벨 찾기
        qty_dom_label = None
        for lbl in dom_labels:
            if lbl in ("매수", "수량"):
                qty_dom_label = lbl
                break
        # 후가공 DOM 라벨 찾기
        finish_dom_label = None
        for lbl in dom_labels:
            if "후가공" in lbl:
                finish_dom_label = lbl
                break
        # Step 5: 500매 seq 찾기
        qty_500_seq = None
        available_qtys = []
        if analysis["qty_idx"] >= 0 and qty_dom_label:
            qty_500_seq, available_qtys = self._find_qty_500_seq(
                page, option_list, analysis["qty_idx"], qty_dom_label
            )
        elif analysis["qty_idx"] >= 0:
            # DOM 라벨을 못 찾았지만 API에는 있는 경우 → 기본 selected 사용
            log.warning(f"  매수 DOM 라벨 미발견, 기본값 사용")
            qty_500_seq = option_list[analysis["qty_idx"]].get("selected")
        # Step 6: 후가공없음 seq 찾기
        finish_none_seq = None
        if analysis["finish_idx"] >= 0 and finish_dom_label:
            finish_none_seq = self._find_finish_none_seq(
                page, option_list, analysis["finish_idx"], finish_dom_label
            )
        elif analysis["finish_idx"] >= 0:
            # 후가공이 1개뿐이면 그냥 사용
            pov = option_list[analysis["finish_idx"]].get("povList", [])
            if len(pov) == 1:
                finish_none_seq = pov[0]
            else:
                finish_none_seq = option_list[analysis["finish_idx"]].get("selected")
        log.info(f"  500매 seq={qty_500_seq}, 후가공없음 seq={finish_none_seq}")
        # 500매 옵션이 없는 제품 처리
        if qty_500_seq is None:
            log.warning(f"  ⚠ 500매 옵션 없음 → 사용 가능 매수만 기록")
            self.results.append({
                "product": name,
                "mock": mock,
                "code": f"{c1}/{c2}/{c3}",
                "price": None,
                "qty_target": QTY_TARGET,
                "available_quantities": ", ".join(available_qtys) if available_qtys else "확인 필요",
                "note": "500매 옵션 없음",
                "url": option_url,
            })
            return
        # Step 7: 변수 옵션(원단/용지/사이즈 등) 순회
        var_idx = analysis["variable_idx"]
        if var_idx >= 0:
            var_pov_list = option_list[var_idx].get("povList", [])
            log.info(f"  {analysis['variable_name']} {len(var_pov_list)}종 순회")
        else:
            # 변수 옵션 없으면 기본값 1건만
            var_pov_list = [None]
            log.info(f"  변수 옵션 없음 → 기본값 1건")
        for vi, var_seq in enumerate(var_pov_list):
            if var_seq is not None:
                log.info(f"  [{vi+1}/{len(var_pov_list)}] {analysis['variable_name']} seq={var_seq}")
                # selectOption API 호출
                select_data = self._api_select_option(page, c1, c2, c3, mock, var_seq)
                if not select_data:
                    log.error(f"    selectOption 실패")
                    self.results.append({
                        "product": name, "mock": mock,
                        "error": f"selectOption 실패 (seq={var_seq})"
                    })
                    continue
                select_options = select_data.get("optionList", [])
                selections = [opt.get("selected") for opt in select_options]
                # 매수 강제 교체 (selectOption이 리셋할 수 있음)
                qty_idx = analysis["qty_idx"]
                if qty_idx >= 0 and qty_idx < len(selections):
                    current_qty = selections[qty_idx]
                    qty_pov = select_options[qty_idx].get("povList", [])
                    if current_qty != qty_500_seq and qty_500_seq in qty_pov:
                        log.info(f"    매수 강제 교체: {current_qty} → {qty_500_seq}")
                        selections[qty_idx] = qty_500_seq
                    elif qty_500_seq not in qty_pov:
                        log.warning(f"    이 옵션에서 500매 미지원, 기본 매수 유지")
                # 후가공 강제 교체
                fin_idx = analysis["finish_idx"]
                if fin_idx >= 0 and fin_idx < len(selections) and finish_none_seq:
                    fin_pov = select_options[fin_idx].get("povList", [])
                    if selections[fin_idx] != finish_none_seq and finish_none_seq in fin_pov:
                        log.info(f"    후가공 강제 교체: {selections[fin_idx]} → {finish_none_seq}")
                        selections[fin_idx] = finish_none_seq
            else:
                # 변수 옵션 없는 경우 → 기본 selections에서 매수/후가공만 교체
                selections = [opt.get("selected") for opt in option_list]
                qty_idx = analysis["qty_idx"]
                if qty_idx >= 0 and qty_idx < len(selections):
                    selections[qty_idx] = qty_500_seq
                fin_idx = analysis["finish_idx"]
                if fin_idx >= 0 and fin_idx < len(selections) and finish_none_seq:
                    selections[fin_idx] = finish_none_seq
            # URL 네비게이션으로 가격 로드
            sel_str = ",".join(str(s) for s in selections)
            nav_url = f"{option_url}&selectedOptionList={sel_str}"
            try:
                page.goto(nav_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_function(
                    "() => document.body.innerText.includes('총 금액')",
                    timeout=15000,
                )
                page.wait_for_timeout(800)
            except PwTimeout:
                log.warning(f"    페이지/가격 로딩 타임아웃")
            # DOM에서 옵션값 + 가격 읽기
            page_data = page.evaluate(JS_READ_PAGE)
            opts = page_data.get("opts", {})
            price = page_data.get("price")
            # 용지/원단 이름 (DOM 라벨로 읽기)
            paper_name = opts.get("용지") or opts.get("원단") or opts.get("상품명") or f"seq_{var_seq}"
            log.info(f"    {paper_name} → {price}")
            self.results.append({
                "product": name,
                "mock": mock,
                "code": f"{c1}/{c2}/{c3}",
                "paper": paper_name,
                "size": opts.get("사이즈", ""),
                "side": opts.get("양/단면", ""),
                "finish": opts.get("후가공", ""),
                "qty": opts.get("매수") or opts.get("수량", ""),
                "price": price,
                "url": nav_url,
            })
    # ── 실행 ──────────────────────────────────────────────
    def run(self):
        log.info("=== 비즈하우스 봉투 크롤링 시작 ===")
        start = time.time()
        with sync_playwright() as pw:
            browser, context = self._init_browser(pw)
            page = context.new_page()
            # 1. 검색 페이지에서 제품 수집
            products = self._collect_products(page)
            if not products:
                log.error("수집된 제품이 없습니다!")
                browser.close()
                return
            # 2. 각 제품 크롤링
            for idx, product in enumerate(products):
                log.info(f"\\n{'='*60}")
                log.info(f"[{idx+1}/{len(products)}] {product['name']}")
                try:
                    self._crawl_product(page, product)
                except Exception as e:
                    log.error(f"  ✖ 예외: {e}", exc_info=True)
                    self.results.append({
                        "product": product["name"],
                        "mock": product["mock"],
                        "error": str(e),
                    })
            browser.close()
        elapsed = time.time() - start
        log.info(f"\\n=== 크롤링 완료: {len(self.results)}건, {elapsed:.1f}초 ===")
        self._save_results()
    # ── 결과 저장 ─────────────────────────────────────────
    def _save_results(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        outdir = Path("output")
        outdir.mkdir(exist_ok=True)
        # JSON
        json_path = outdir / f"bizhows_envelope_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2)
        log.info(f"JSON 저장: {json_path}")
        # 요약
        success = [r for r in self.results if r.get("price") and "error" not in r]
        no_price = [r for r in self.results if not r.get("price") and "error" not in r]
        errors = [r for r in self.results if "error" in r]
        log.info(f"가격 있음: {len(success)}건, 가격 없음(500매 미지원 등): {len(no_price)}건, 에러: {len(errors)}건")
        if no_price:
            for r in no_price:
                log.info(f"  - {r.get('product','?')}: {r.get('note','')}")
        if errors:
            for r in errors:
                log.info(f"  - {r.get('product','?')}: {r.get('error','')}")
if __name__ == "__main__":
    crawler = BizhowsEnvelopeCrawler(headless=True)
    crawler.run()