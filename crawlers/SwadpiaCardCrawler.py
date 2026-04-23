"""
성원애드피아 명함 크롤러.

흐름:
  1. config/card_targets.json swadpia 섹션 로드
  2. 각 (제품, 용지, 코팅, 색도, 수량) 조합마다 페이지 옵션 셋팅 → DOM에서 가격 읽기
  3. raw 값 그대로 저장 (정규화는 common.normalize가 담당)

가격: 페이지 #print_estimate_tot 영역에서 "총 합계금액 : ₩X,XXX원" 추출 (VAT 포함).
출력: output/swadpia_card_raw_now.json — output_template 포맷
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
    if not _CONFIG_PATH.exists():
        return []
    cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    return cfg.get("swadpia", [])


TARGETS = _load_targets()

TARGET_QTYS = [100, 200, 500, 1000]
PAGE_BASE = "https://www.swadpia.co.kr/goods/goods_view"
COMPANY = "swadpia"
CATEGORY = "card"
SIZE_RAW = "90mm*50mm"

BLOCK_PATTERNS = [
    "**/google-analytics.com/**", "**/googletagmanager.com/**",
    "**/facebook.net/**", "**/facebook.com/tr/**", "**/doubleclick.net/**",
    "**/criteo.net/**", "**/criteo.com/**", "**/analytics.tiktok.com/**",
]

_RE_TOTAL = re.compile(r"총\s*합계금액\s*[:：]?\s*[\\￦₩]?\s*([\d,]+)\s*원")


JS_SET_OPTIONS = """({paper, color, qty, gloss_field, gloss_val, paper_size}) => {
    const setSelect = (name, val) => {
        const el = document.querySelector(`select[name="${name}"]`);
        if (!el) return false;
        el.value = val;
        el.dispatchEvent(new Event('change', {bubbles: true}));
        return true;
    };
    const r = {paper:false, color:false, qty:false, size:false, gloss:false};
    r.paper = setSelect('paper_code', paper);
    r.size  = setSelect('paper_size', paper_size);
    r.color = setSelect('print_color_type', color);
    r.qty   = setSelect('paper_qty', qty);
    const radio = document.querySelector(`input[type=radio][name="${gloss_field}"][value="${gloss_val}"]`);
    if (radio) { radio.click(); r.gloss = true; }
    return r;
}"""

JS_GET_PRICE = """() => {
    const e = document.querySelector('#print_estimate_tot');
    if (!e) return null;
    return e.textContent.replace(/\\s+/g, ' ').trim();
}"""

JS_AVAILABLE_QTYS = """() => {
    const sel = document.querySelector('select[name="paper_qty"]');
    if (!sel) return [];
    return [...sel.querySelectorAll('option')]
        .map(o => parseInt(o.value, 10))
        .filter(Number.isFinite);
}"""


JS_READ_DOM_STATE = """() => {
    const sel = (name) => {
        const el = document.querySelector(`select[name="${name}"]`);
        if (!el || el.selectedIndex < 0) return '';
        return (el.options[el.selectedIndex]?.textContent || '').trim();
    };
    const val = (name) => {
        const el = document.querySelector(`select[name="${name}"]`);
        return el ? (el.value || '') : '';
    };
    // checked radio의 라벨 텍스트 (paper_gloss / paper_gloss2 중 하나만 체크됨)
    let coat = '';
    for (const r of document.querySelectorAll('input[type=radio]:checked')) {
        if (!/^paper_gloss/.test(r.name || '')) continue;
        const byFor = r.id ? document.querySelector(`label[for="${r.id}"]`) : null;
        if (byFor) { coat = byFor.innerText.trim(); break; }
        const wrap = r.closest('label');
        if (wrap) { coat = wrap.innerText.trim(); break; }
        const nxt = r.nextSibling;
        if (nxt && nxt.nodeType === 3) { coat = nxt.textContent.trim(); break; }
        // 마지막 fallback: value (코드)
        coat = r.value || '';
    }
    return {
        paper_text:  sel('paper_code'),
        color_text:  sel('print_color_type'),
        size_text:   sel('paper_size'),
        qty_val:     val('paper_qty'),
        coating:     coat,
    };
}"""


def read_dom_state(page) -> dict:
    raw = page.evaluate(JS_READ_DOM_STATE) or {}
    try:
        qty = int(raw.get("qty_val") or "")
    except (TypeError, ValueError):
        qty = 0
    return {
        "paper_name": (raw.get("paper_text") or "").strip(),
        "coating":    (raw.get("coating") or "").strip(),
        "print_mode": (raw.get("color_text") or "").strip(),
        "size":       (raw.get("size_text") or "").strip(),
        "qty":        qty,
    }


def parse_total_price(txt: str) -> int | None:
    if not txt:
        return None
    m = _RE_TOTAL.search(txt)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


class SwadpiaCrawler:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.items: list[dict] = []

    def _init_browser(self, pw):
        browser = pw.chromium.launch(headless=self.headless)
        context = browser.new_context(viewport={"width": 1280, "height": 900}, locale="ko-KR")
        for pat in BLOCK_PATTERNS:
            context.route(pat, lambda r: r.abort())
        # 페이지 alert 자동 dismiss
        context.on("dialog", lambda d: d.dismiss())
        return browser, context

    def _crawl_product(self, page, t: dict):
        url = f"{PAGE_BASE}/{t['category_code']}/{t['product_code']}"
        log.info(f"▶ {t['product_name']} ({t['product_code']})")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1500)
        except PwTimeout:
            log.error("  ✖ 페이지 타임아웃")
            return

        avail_qtys = page.evaluate(JS_AVAILABLE_QTYS)
        target_qtys = [q for q in TARGET_QTYS if q in avail_qtys]
        log.info(f"  qty 매칭: {target_qtys} (사이트 시작 {min(avail_qtys) if avail_qtys else '?'}매)")
        if not target_qtys:
            log.warning("  매칭 qty 없음, skip")
            return

        for paper in t["papers"]:
            for coating in t["coatings"]:
                for color in t["color_modes"]:
                    for qty in target_qtys:
                        sel_args = {
                            "paper": paper["code"],
                            "color": color["value"],
                            "qty": str(qty),
                            "gloss_field": coating["field"],
                            "gloss_val": coating["value"],
                            "paper_size": t.get("paper_size_code", "N0100"),
                        }
                        try:
                            res = page.evaluate(JS_SET_OPTIONS, sel_args)
                            if not res.get("gloss"):
                                continue
                            page.wait_for_timeout(500)
                            txt = page.evaluate(JS_GET_PRICE)
                            price = parse_total_price(txt or "")
                            if price is None:
                                log.warning(f"    가격 파싱 실패: {paper['name']} | {coating['name']} | {color['name']} | {qty}매")
                                continue
                            dom = read_dom_state(page)
                            self.items.append({
                                "product":    t["product_name"],
                                "category":   t["product_name"],
                                "paper_name": dom["paper_name"] or None,
                                "coating":    dom["coating"] or None,
                                "print_mode": dom["print_mode"] or None,
                                "size":       dom["size"] or None,
                                "qty":        dom["qty"] or None,
                                "price":      price,
                                "price_vat_included": True,
                                "url":        url,
                                "url_ok":     True,
                                "options": {
                                    "config_paper_code": paper["code"],
                                    "config_paper_name": paper["name"],
                                    "config_coating":    coating["name"],
                                    "config_color":      color["name"],
                                    "config_qty":        qty,
                                },
                            })
                            log.info(f"    DOM: {dom['paper_name']} | {dom['coating']} | {dom['print_mode']} | {dom['qty']}매 → {price:,}원")
                        except Exception as e:
                            log.error(f"    X {paper['name']} / {coating['name']} / {color['name']} / {qty}: {e}")

    def run(self):
        log.info(f"=== 성원애드피아 명함 크롤링 시작 ({len(TARGETS)}종 제품) ===")
        if not TARGETS:
            log.error("크롤 타겟 없음 — config/card_targets.json swadpia 섹션 확인")
            return
        start = time.time()
        with sync_playwright() as pw:
            browser, context = self._init_browser(pw)
            page = context.new_page()
            for i, t in enumerate(TARGETS, 1):
                log.info(f"[{i}/{len(TARGETS)}]")
                try:
                    self._crawl_product(page, t)
                except Exception as e:
                    log.error(f"  ✖ {t['product_name']}: {e}")
            browser.close()
        elapsed = time.time() - start
        log.info(f"=== 완료: {len(self.items)}건, {elapsed:.1f}초 ===")


def crawl_all() -> list[dict]:
    c = SwadpiaCrawler(headless=True)
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
