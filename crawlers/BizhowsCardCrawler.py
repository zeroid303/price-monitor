"""
비즈하우스 명함 크롤러 (신규 스키마).

흐름:
  1. config/card_targets.json bizhows 섹션에서 (제품, 용지 seq) 타겟 로드
  2. 타겟 제품만 페이지 로드 → combination API로 옵션 구조 파악
  3. 매치된 용지 seq만 순회
  4. 각 용지에 대해: side (양면/단면 등 전 색도) × qty (100/200/500/1000) 조합 순회
  5. URL selectedOptionList 네비게이션 → DOM에서 용지명 + 가격 + 사양 읽기
  6. raw 값 그대로 저장 (정규화는 common.normalize가 담당)

출력: output/bizhows_card_raw_now.json — output_template 포맷
"""
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "card_targets.json"


def _load_targets() -> list[dict]:
    """config/card_targets.json bizhows 섹션 로드.
    각 entry: {category, name, slug, code1, code2, code3, papers:[{seq, name}]}."""
    if not _CONFIG_PATH.exists():
        return []
    cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    return cfg.get("bizhows", [])


TARGETS = _load_targets()

TARGET_QTYS = {100, 200, 500, 1000}
TARGET_SIZE_KEYWORDS = ("90x50", "90mm x 50mm")

BASE_URL = "https://www.bizhows.com/ko/v/option"
SITE_BASE_URL = "https://www.bizhows.com"
COMPANY = "bizhows"
CATEGORY = "card"

BLOCK_PATTERNS = [
    "**/analytics.tiktok.com/**", "**/channel.io/**", "**/criteo.net/**", "**/criteo.com/**",
    "**/google-analytics.com/**", "**/googletagmanager.com/**", "**/facebook.net/**",
    "**/facebook.com/tr/**", "**/doubleclick.net/**", "**/ads-twitter.com/**", "**/f.clarity.ms/**",
]

# ── DOM 파싱 JS ──
JS_READ_PAGE = """() => {
    const opts = {};
    const seen = new Set();
    const dws = document.querySelectorAll('[data-f^="DW-"]');
    for (const dw of dws) {
        const dts = dw.querySelectorAll('[data-f^="DT-"]');
        let label = null;
        for (const dt of dts) {
            const t = dt.textContent.trim();
            if (t && !seen.has(t)) { label = t; seen.add(t); break; }
        }
        if (!label) continue;
        const dd = dw.querySelector('[data-f^="DD-"]');
        if (dd) {
            const txt = dd.textContent.trim();
            opts[label] = txt.replace(new RegExp('^' + label), '').trim();
        }
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
    return { opts, price };
}"""


def parse_price(txt: str) -> int | None:
    if not txt:
        return None
    m = re.search(r"[\d,]+", txt.replace(" ", ""))
    if not m:
        return None
    try:
        return int(m.group().replace(",", ""))
    except ValueError:
        return None


def _find_option_idx(option_list: list, names: set) -> int:
    for i, opt in enumerate(option_list):
        if opt.get("poiName") in names:
            return i
    return -1


class BizhowsCrawler:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.items: list[dict] = []

    def _init_browser(self, pw):
        browser = pw.chromium.launch(headless=self.headless)
        context = browser.new_context(viewport={"width": 1280, "height": 900}, locale="ko-KR")
        for pat in BLOCK_PATTERNS:
            context.route(pat, lambda r: r.abort())
        return browser, context

    def _api_combination(self, page, p: dict, select_seq: int | None = None) -> dict | None:
        url = f"/api/v1/option/combination/{p['code1']}/{p['code2']}/{p['code3']}/{p['slug']}"
        if select_seq is not None:
            url += f"?selectOption={select_seq}"
        try:
            return page.evaluate(f"""() => fetch('{url}').then(r=>r.json()).then(d=>d.data)""")
        except Exception as e:
            log.error(f"  API 실패: {e}")
            return None

    def _nav_and_read(self, page, base_url: str, selections: list[int]) -> dict:
        sel_str = ",".join(str(s) for s in selections)
        nav_url = f"{base_url}&selectedOptionList={sel_str}"
        try:
            page.goto(nav_url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_function("() => document.body.innerText.includes('총 금액')", timeout=10000)
            page.wait_for_timeout(400)
        except PwTimeout:
            pass
        data = page.evaluate(JS_READ_PAGE)
        data["url"] = nav_url
        return data

    def _crawl_product(self, page, p: dict):
        base_url = f"{BASE_URL}?code1={p['code1']}&code2={p['code2']}&code3={p['code3']}&mock={p['slug']}&from=product_list_090"
        log.info(f"▶ {p['category']} / {p['name']}")
        try:
            page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1500)
        except PwTimeout:
            log.error("  ✖ 페이지 타임아웃")
            return

        combo = self._api_combination(page, p)
        if not combo:
            return
        option_list = combo.get("optionList", [])
        paper_idx = _find_option_idx(option_list, {"원단", "용지"})
        qty_idx = _find_option_idx(option_list, {"매수"})
        side_idx = _find_option_idx(option_list, {"양/단면", "인쇄도수", "인쇄"})
        if paper_idx < 0:
            log.warning("  용지 옵션 없음, skip")
            return
        paper_povs = option_list[paper_idx].get("povList", [])

        # config 타겟 seq만 nav
        allowed_seqs = {pap["seq"] for pap in p.get("papers", [])}
        before = len(paper_povs)
        paper_povs = [s for s in paper_povs if s in allowed_seqs]
        log.info(f"  paper pre-filter: {before} → {len(paper_povs)} (config 타겟)")
        if not paper_povs:
            return

        # 각 용지 seq 순회 (whitelist 필터를 name 확보 후 적용)
        for i, paper_seq in enumerate(paper_povs):
            select_data = self._api_combination(page, p, select_seq=paper_seq)
            if not select_data:
                continue
            sel_opts = select_data.get("optionList", [])
            selections = [opt.get("selected") for opt in sel_opts]

            # side와 qty povList 획득
            side_povs = []
            if side_idx >= 0 and side_idx < len(sel_opts):
                side_povs = [s if isinstance(s, int) else s.get("seq") for s in sel_opts[side_idx].get("povList", [])]
            if not side_povs:
                side_povs = [None]

            qty_povs = []
            if qty_idx >= 0 and qty_idx < len(sel_opts):
                qty_povs = sel_opts[qty_idx].get("povList", [])

            # side × qty 순회
            for side_seq in side_povs:
                for qty_pov in qty_povs:
                    q_seq = qty_pov if isinstance(qty_pov, int) else qty_pov.get("seq")
                    # qty_pov name으로 filter 먼저 (숫자 추출)
                    q_name = qty_pov.get("name", "") if isinstance(qty_pov, dict) else ""
                    q_nums = re.findall(r"\d+", q_name.replace(",", ""))
                    q_int = int(q_nums[0]) if q_nums else None
                    if q_int and q_int not in TARGET_QTYS:
                        continue  # 100/200/500/1000 외 skip

                    cur_sel = list(selections)
                    if side_seq is not None and side_idx >= 0:
                        cur_sel[side_idx] = side_seq
                    if q_seq is not None and qty_idx >= 0:
                        cur_sel[qty_idx] = q_seq

                    data = self._nav_and_read(page, base_url, cur_sel)
                    opts = data["opts"]
                    paper_name = opts.get("용지") or opts.get("원단") or ""
                    price = parse_price(data.get("price"))
                    if price is None:
                        continue

                    # qty 정수값 확정: opts.매수 파싱 또는 q_int
                    qty_val = q_int
                    if qty_val is None:
                        mq = re.findall(r"\d+", opts.get("매수", "").replace(",", ""))
                        if mq:
                            qty_val = int(mq[0])
                    # TARGET_QTYS 최종 필터
                    if qty_val not in TARGET_QTYS:
                        continue

                    self.items.append({
                        "product": p["name"],
                        "category": p["category"],
                        "paper_name": paper_name,
                        "coating": opts.get("코팅", ""),
                        "print_mode": opts.get("양/단면") or opts.get("인쇄도수") or opts.get("인쇄", ""),
                        "size": opts.get("사이즈", ""),
                        "qty": qty_val or 0,
                        "price": price,
                        "price_vat_included": False,  # bizhows는 VAT 별도
                        "url": data["url"],
                        "url_ok": True,  # nav 성공 시 true
                        "options": {k: v for k, v in opts.items() if k in ("둥근모서리", "인쇄도수")},
                    })
                    log.info(f"  {paper_name} | {opts.get('양/단면','')} | {qty_val}매 → {price:,}원")

    def run(self):
        log.info(f"=== 비즈하우스 명함 크롤링 시작 ({len(TARGETS)}종 제품) ===")
        if not TARGETS:
            log.error("크롤 타겟 없음 — config/card_targets.json 확인 필요")
            return
        start = time.time()
        with sync_playwright() as pw:
            browser, context = self._init_browser(pw)
            page = context.new_page()
            for i, p in enumerate(TARGETS, 1):
                log.info(f"[{i}/{len(TARGETS)}]")
                try:
                    self._crawl_product(page, p)
                except Exception as e:
                    log.error(f"  ✖ {p['name']}: {e}")
            browser.close()
        elapsed = time.time() - start
        log.info(f"=== 완료: {len(self.items)}건, {elapsed:.1f}초 ===")


# ── 스케줄러 인터페이스 (PrintcityCardCrawler와 동일 시그니처) ──
def crawl_all() -> list[dict]:
    c = BizhowsCrawler(headless=True)
    c.run()
    return c.items


def save(items: list[dict]):
    base = Path(__file__).resolve().parent.parent
    outdir = base / "output"
    outdir.mkdir(exist_ok=True)
    raw_now = outdir / f"{COMPANY}_{CATEGORY}_raw_now.json"
    output = {
        "company": COMPANY,
        "crawled_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "items": items,
    }
    with open(raw_now, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    log.info(f"저장: {raw_now} ({len(items)}건)")


if __name__ == "__main__":
    items = crawl_all()
    save(items)
