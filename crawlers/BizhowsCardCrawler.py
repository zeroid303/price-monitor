"""
비즈하우스(bizhows.com) 명함 가격 크롤러 v5.1
핵심 변경 (v4 → v5.1):
 - 모든 UI 클릭 제거 → combination API + selectOption API + URL 네비게이션 방식
 - selectOption API가 매수를 기본값으로 리셋하는 버그 → 200매 seq 강제 교체
 - 독특한명함 제품 목록 전면 교체 (라운드/금박/형압/은박/홀로그램박/먹박/청박/적박)
 - 정규식 이스케이프 수정 (\\\\\\\\d → \\\\d)
 - 2단명함 등 옵션 구조가 다른 제품 동적 대응 (매수/원단 인덱스 자동 탐색)
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
    # 일반명함 (12종)
    {"category": "일반명함", "name": "기본명함",       "mock": "5000_200_3501_7",    "code1": "5000", "code2": "200", "code3": "3501"},
    {"category": "일반명함", "name": "프리미엄명함",   "mock": "5000_200_3501_8",    "code1": "5000", "code2": "200", "code3": "3501"},
    {"category": "일반명함", "name": "3D명함",         "mock": "5000_200_3501_1200", "code1": "5000", "code2": "200", "code3": "3501"},
    {"category": "일반명함", "name": "펄지명함",       "mock": "5000_200_3501_11",   "code1": "5000", "code2": "200", "code3": "3501"},
    {"category": "일반명함", "name": "패턴명함",       "mock": "5000_200_3501_10",   "code1": "5000", "code2": "200", "code3": "3501"},
    {"category": "일반명함", "name": "심플명함",       "mock": "5000_200_3501_16",   "code1": "5000", "code2": "200", "code3": "3501"},
    {"category": "일반명함", "name": "재질명함",       "mock": "5000_200_3501_9",    "code1": "5000", "code2": "200", "code3": "3501"},
    {"category": "일반명함", "name": "빠른출고명함",   "mock": "5000_200_3501_1000", "code1": "5000", "code2": "200", "code3": "3501"},
    {"category": "일반명함", "name": "컬러용지명함",   "mock": "5000_200_3501_400",  "code1": "5000", "code2": "200", "code3": "3501"},
    {"category": "일반명함", "name": "압도적1위명함",  "mock": "5000_200_3501_14",   "code1": "5000", "code2": "200", "code3": "3501"},
    {"category": "일반명함", "name": "카드명함",       "mock": "5000_200_3501_700",  "code1": "5000", "code2": "200", "code3": "3501"},
    {"category": "일반명함", "name": "2단명함",        "mock": "5100_200_1000",      "code1": "5100", "code2": "200", "code3": "1000"},
    # 독특한명함 (8종)
    {"category": "독특한명함", "name": "라운드명함",     "mock": "5000_200_3501_900_1", "code1": "5000", "code2": "200", "code3": "3501"},
    {"category": "독특한명함", "name": "금박명함",       "mock": "5000_200_3501_900_3", "code1": "5000", "code2": "200", "code3": "3501"},
    {"category": "독특한명함", "name": "형압명함",       "mock": "5000_200_3501_900_2", "code1": "5000", "code2": "200", "code3": "3501"},
    {"category": "독특한명함", "name": "은박명함",       "mock": "5000_200_3501_900_4", "code1": "5000", "code2": "200", "code3": "3501"},
    {"category": "독특한명함", "name": "홀로그램박명함", "mock": "5000_200_3501_900_5", "code1": "5000", "code2": "200", "code3": "3501"},
    {"category": "독특한명함", "name": "먹박명함",       "mock": "5000_200_3501_900_6", "code1": "5000", "code2": "200", "code3": "3501"},
    {"category": "독특한명함", "name": "청박명함",       "mock": "5000_200_3501_900_7", "code1": "5000", "code2": "200", "code3": "3501"},
    {"category": "독특한명함", "name": "적박명함",       "mock": "5000_200_3501_900_8", "code1": "5000", "code2": "200", "code3": "3501"},
]
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
API_BASE = "https://www.bizhows.com/api/v1/option/combination"
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
        const m = document.body.innerText.match(/총 금액\\s*([\\d,]+원)/);
        if (m) price = m[1];
    }
    return {opts, price};
}"""
# ── JS: 용지명 읽기 (OT 텍스트) ───────────────────────────
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
class BizhowsCrawler:
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
    # ── API 호출 (page.evaluate + fetch) ──────────────────
    def _api_combination(self, page, product: dict) -> dict | None:
        """combination API 호출 → 옵션 구조 반환"""
        c1, c2, c3, mock = product["code1"], product["code2"], product["code3"], product["mock"]
        url = f"/api/v1/option/combination/{c1}/{c2}/{c3}/{mock}"
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
    def _api_select_option(self, page, product: dict, option_seq: int) -> dict | None:
        """selectOption API 호출 → 특정 옵션 선택 후 전체 구조 반환"""
        c1, c2, c3, mock = product["code1"], product["code2"], product["code3"], product["mock"]
        url = f"/api/v1/option/combination/{c1}/{c2}/{c3}/{mock}?selectOption={option_seq}"
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
    def _find_paper_and_qty(self, option_list: list) -> tuple:
        """옵션 리스트에서 용지(원단)와 매수의 인덱스를 동적으로 찾기"""
        paper_idx = -1
        qty_idx = -1
        for i, opt in enumerate(option_list):
            name = opt.get("poiName", "")
            if name in ("원단", "용지"):
                paper_idx = i
            elif name == "매수":
                qty_idx = i
        return paper_idx, qty_idx

    def _find_coating_idx(self, option_list: list) -> int:
        """옵션 리스트에서 코팅 인덱스 찾기. 없으면 -1."""
        for i, opt in enumerate(option_list):
            name = opt.get("poiName", "")
            if name in ("코팅", "명함 코팅"):
                return i
        return -1
    def _get_target_qty_seq(self, page, product: dict, option_list: list, paper_idx: int, qty_idx: int) -> tuple[int | None, str]:
        """기본 용지의 selectOption을 호출하여 200매(또는 가장 가까운 수량) seq를 확보.
        Returns: (seq, qty_label) — qty_label은 '200매' 등 실제 수량 텍스트"""
        if paper_idx < 0 or qty_idx < 0:
            return None, "200매"
        default_paper_seq = option_list[paper_idx]["selected"]
        select_data = self._api_select_option(page, product, default_paper_seq)
        if not select_data:
            return None, "200매"
        select_options = select_data.get("optionList", [])
        if qty_idx >= len(select_options):
            return None, "200매"
        qty_opt = select_options[qty_idx]
        qty_selected = qty_opt.get("selected")
        qty_pov_list = qty_opt.get("povList", [])
        # povList에서 각 seq의 이름(매수)을 확인하여 200매 seq 탐색
        qty_names = {}  # seq -> name
        for pov in qty_pov_list:
            if isinstance(pov, dict):
                qty_names[pov.get("seq")] = pov.get("name", "")
            else:
                qty_names[pov] = ""
        # 200매 seq 찾기
        target_seq = None
        target_label = "200매"
        for seq, name in qty_names.items():
            if "200" in name:
                target_seq = seq
                target_label = name
                break
        if target_seq is None:
            # 200매 없으면 가장 가까운 수량 찾기
            best_seq = None
            best_diff = float("inf")
            best_label = ""
            for seq, name in qty_names.items():
                # 이름에서 숫자 추출
                nums = re.findall(r"\d+", name.replace(",", ""))
                if nums:
                    qty_val = int(nums[0])
                    diff = abs(qty_val - 200)
                    if diff < best_diff:
                        best_diff = diff
                        best_seq = seq
                        best_label = name
            if best_seq is not None:
                target_seq = best_seq
                target_label = best_label
                log.info(f"  200매 없음 → 가장 가까운 수량: {target_label} (seq={target_seq})")
            else:
                # povList에서 이름을 못 찾으면 기본 선택값 사용
                target_seq = qty_selected
                target_label = "기본"
        log.info(f"  기본 용지({default_paper_seq}) 매수: target={target_seq} ({target_label}), povList 길이={len(qty_pov_list)}")
        return target_seq, target_label
    # ── 단일 제품 크롤링 ──────────────────────────────────
    def _crawl_product(self, page, product: dict):
        mock = product["mock"]
        c1, c2, c3 = product["code1"], product["code2"], product["code3"]
        base_url = f"{BASE_URL}?code1={c1}&code2={c2}&code3={c3}&mock={mock}&from=product_list_090"
        log.info(f"▶ {product['category']} / {product['name']}")
        # Step 1: 제품 페이지로 이동 (API 호출을 위한 도메인 컨텍스트 확보)
        try:
            page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
        except PwTimeout:
            log.error(f"  ✖ 페이지 로딩 타임아웃")
            self.results.append({
                "category": product["category"],
                "product": product["name"],
                "error": "페이지 로딩 타임아웃",
                "url": base_url,
            })
            return
        # Step 2: combination API 호출 → 옵션 구조 파악
        combo_data = self._api_combination(page, product)
        if not combo_data:
            log.error(f"  ✖ combination API 실패")
            self.results.append({
                "category": product["category"],
                "product": product["name"],
                "error": "combination API 실패",
                "url": base_url,
            })
            return
        option_list = combo_data.get("optionList", [])
        paper_idx, qty_idx = self._find_paper_and_qty(option_list)
        if paper_idx < 0:
            log.warning(f"  용지/원단 옵션 없음 → 기본값 1건 수집")
            try:
                page.wait_for_function(
                    "() => document.body.innerText.includes('총 금액')",
                    timeout=15000,
                )
            except PwTimeout:
                pass
            page_data = page.evaluate(JS_READ_PAGE)
            self.results.append({
                "category": product["category"],
                "product": product["name"],
                "paper": page_data["opts"].get("용지", page_data["opts"].get("원단", "기본")),
                "size": page_data["opts"].get("사이즈", ""),
                "side": page_data["opts"].get("양/단면", ""),
                "corner": page_data["opts"].get("둥근모서리", ""),
                "qty": page_data["opts"].get("매수", ""),
                "price": page_data["price"],
                "url": base_url,
            })
            return
        paper_pov_list = option_list[paper_idx].get("povList", [])
        log.info(f"  용지 {len(paper_pov_list)}종 (API povList)")
        if not paper_pov_list:
            log.warning(f"  용지 povList 비어있음 → 기본값 1건 수집")
            try:
                page.wait_for_function(
                    "() => document.body.innerText.includes('총 금액')",
                    timeout=15000,
                )
            except PwTimeout:
                pass
            page_data = page.evaluate(JS_READ_PAGE)
            self.results.append({
                "category": product["category"],
                "product": product["name"],
                "paper": page_data["opts"].get("용지", page_data["opts"].get("원단", "기본")),
                "size": page_data["opts"].get("사이즈", ""),
                "side": page_data["opts"].get("양/단면", ""),
                "corner": page_data["opts"].get("둥근모서리", ""),
                "qty": page_data["opts"].get("매수", ""),
                "price": page_data["price"],
                "url": base_url,
            })
            return
        # Step 3: 기본 용지의 selectOption → 목표 수량 seq 확보
        target_qty_seq, target_qty_label = self._get_target_qty_seq(page, product, option_list, paper_idx, qty_idx)
        if target_qty_seq:
            log.info(f"  목표 수량 seq 확보: {target_qty_seq} ({target_qty_label})")
        else:
            log.warning(f"  수량 seq 확보 실패 → 매수 교체 불가")
        # Step 4: 각 용지별로 selectOption → URL 네비게이션 → 가격 읽기
        for i, paper_seq in enumerate(paper_pov_list):
            log.info(f"  [{i+1}/{len(paper_pov_list)}] 용지 seq={paper_seq}")
            # selectOption API 호출
            select_data = self._api_select_option(page, product, paper_seq)
            if not select_data:
                log.error(f"    selectOption 실패")
                self.results.append({
                    "category": product["category"],
                    "product": product["name"],
                    "paper": f"seq_{paper_seq}",
                    "error": "selectOption API 실패",
                })
                continue
            select_options = select_data.get("optionList", [])
            selections = [opt.get("selected") for opt in select_options]
            # 매수 강제 교체: target_qty_seq가 있고, 이 용지의 매수 기본값이 다르면 교체
            if target_qty_seq and qty_idx >= 0 and qty_idx < len(select_options):
                current_qty_seq = selections[qty_idx]
                qty_pov = select_options[qty_idx].get("povList", [])
                # povList가 dict 리스트일 수 있음
                qty_pov_seqs = [p.get("seq") if isinstance(p, dict) else p for p in qty_pov]
                if current_qty_seq != target_qty_seq:
                    if target_qty_seq in qty_pov_seqs:
                        log.info(f"    매수 교체: {current_qty_seq} → {target_qty_seq} ({target_qty_label})")
                        selections[qty_idx] = target_qty_seq
                    else:
                        # 이 용지에서도 가장 가까운 수량 찾기
                        best_seq = None
                        best_diff = float("inf")
                        for p in qty_pov:
                            seq = p.get("seq") if isinstance(p, dict) else p
                            name = p.get("name", "") if isinstance(p, dict) else ""
                            nums = re.findall(r"\d+", name.replace(",", ""))
                            if nums:
                                diff = abs(int(nums[0]) - 200)
                                if diff < best_diff:
                                    best_diff = diff
                                    best_seq = seq
                        if best_seq and best_seq != current_qty_seq:
                            log.info(f"    목표수량 미지원 → 가장 가까운 수량으로 교체: {best_seq}")
                            selections[qty_idx] = best_seq
                        else:
                            log.info(f"    목표수량 미지원 → 기본 매수 유지 ({current_qty_seq})")
            # 코팅 옵션 확인: 2개 이상이면 코팅별로 순회
            coating_idx = self._find_coating_idx(select_options)
            coating_variants = []
            if coating_idx >= 0:
                coating_povs = select_options[coating_idx].get("povList", [])
                if len(coating_povs) > 1:
                    coating_variants = coating_povs
                    log.info(f"    코팅 옵션 {len(coating_povs)}종 발견 → 코팅별 순회")
            # 코팅 순회 (1종이면 기본값 1회만)
            if not coating_variants:
                coating_variants = [None]  # 기본값 1회
            for coat_seq in coating_variants:
                curr_selections = list(selections)
                if coat_seq is not None and coating_idx >= 0:
                    curr_selections[coating_idx] = coat_seq
                # URL 구성 및 네비게이션
                sel_str = ",".join(str(s) for s in curr_selections)
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
                # DOM에서 용지명 + 가격 읽기
                page_data = page.evaluate(JS_READ_PAGE)
                paper_name = page.evaluate(JS_READ_PAPER_NAME) or f"용지_{paper_seq}"
                price = page_data["price"]
                opts = page_data["opts"]
                coating = opts.get("코팅", "")
                display_name = f"{paper_name} ({coating})" if coating and coating != "없음" else paper_name
                log.info(f"    {display_name} → {price}")
                self.results.append({
                    "category": product["category"],
                    "product": product["name"],
                    "paper": paper_name,
                    "coating": coating,
                    "size": opts.get("사이즈", ""),
                    "side": opts.get("양/단면", ""),
                    "corner": opts.get("둥근모서리", ""),
                    "qty": opts.get("매수", ""),
                    "price": price,
                    "url": nav_url,
                })
    def run(self):
        log.info(f"=== 비즈하우스 크롤링 시작 ({len(PRODUCTS)}개 제품) ===")
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
                    log.error(f"  ✖ 예외: {e}")
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
        import shutil
        outdir = Path(__file__).resolve().parent.parent / "output"
        outdir.mkdir(exist_ok=True)
        now_path = outdir / "bizhows_card_now.json"
        past_path = outdir / "bizhows_card_past.json"
        if now_path.exists():
            shutil.copy2(now_path, past_path)
        # 통일 구조로 변환
        items = []
        for r in self.results:
            if "error" in r or not r.get("price"):
                continue
            price_str = r["price"].replace(",", "").replace("원", "").strip() if isinstance(r["price"], str) else str(r["price"])
            try:
                price = int(price_str)
            except:
                continue
            items.append({
                "product": r.get("product", ""),
                "category": r.get("category", ""),
                "paper_name": r.get("paper", ""),
                "coating": r.get("coating", "없음"),
                "color_mode": r.get("side", "양면"),
                "size": r.get("size", "90x50"),
                "qty": int(str(r.get("qty", "200")).replace("매", "").replace(",", "").strip()) if r.get("qty") else 200,
                "price": price,
                "price_vat_included": False,
                "options": {"corner": r.get("corner", "")},
            })
        output = {
            "company": "bizhows",
            "crawled_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "items": items,
        }
        with open(now_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        log.info(f"JSON 저장: {now_path}")
        success = [r for r in self.results if "error" not in r and r.get("price")]
        fail = [r for r in self.results if "error" in r or not r.get("price")]
        log.info(f"성공: {len(success)}건, 실패: {len(fail)}건")
if __name__ == "__main__":
    crawler = BizhowsCrawler(headless=True)
    crawler.run()