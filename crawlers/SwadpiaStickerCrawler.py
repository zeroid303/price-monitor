"""
성원애드피아 도무송 스티커 크롤러.
config/sticker_targets.json swadpia 섹션의 (용지 x 코팅 x 사이즈) 조합을 크롤링.

SwadpiaCardCrawler 패턴 기반 Playwright DOM 조작.

가격: #print_estimate_tot 영역에서 "총 합계금액 : X,XXX원" 추출 (VAT 포함).
출력: output/swadpia_sticker_raw_now.json
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

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "sticker_targets.json"

COMPANY = "swadpia"
CATEGORY = "sticker"

BLOCK_PATTERNS = [
    "**/google-analytics.com/**", "**/googletagmanager.com/**",
    "**/facebook.net/**", "**/facebook.com/tr/**", "**/doubleclick.net/**",
    "**/criteo.net/**", "**/criteo.com/**", "**/analytics.tiktok.com/**",
]

_RE_TOTAL = re.compile(r"총\s*합계금액\s*[:：]?\s*[\\￦₩]?\s*([\d,]+)\s*원")

PAGE_BASE = "https://www.swadpia.co.kr/goods/goods_view"

JS_GET_PRICE = r"""() => {
    const e = document.querySelector('#print_estimate_tot');
    if (!e) return null;
    return e.textContent.replace(/\s+/g, ' ').trim();
}"""


JS_READ_DOM_STATE = r"""() => {
    const selText = (name) => {
        const el = document.querySelector(`select[name="${name}"]`);
        if (!el || el.selectedIndex < 0) return '';
        return (el.options[el.selectedIndex]?.textContent || '').trim();
    };
    const selVal = (name) => {
        const el = document.querySelector(`select[name="${name}"]`);
        return el ? (el.value || '') : '';
    };
    const inpVal = (name) => {
        const el = document.querySelector(`input[name="${name}"]`);
        return el ? (el.value || '') : '';
    };
    return {
        paper_text:  selText('paper_code'),
        coating_text:selText('coating_type'),
        color_text:  selText('print_color_type'),
        shape_text:  selText('domusong_type'),
        section_text:selText('domusong_section'),
        qty_val:     selVal('paper_qty'),
        size_x:      inpVal('domusong_x_size'),
        size_y:      inpVal('domusong_y_size'),
    };
}"""


def read_dom_state(page) -> dict:
    raw = page.evaluate(JS_READ_DOM_STATE) or {}
    try:
        qty = int(raw.get("qty_val") or "")
    except (TypeError, ValueError):
        qty = 0
    x = (raw.get("size_x") or "").strip()
    y = (raw.get("size_y") or "").strip()
    size = f"{x}x{y}" if x and y else ""
    return {
        "paper_name": (raw.get("paper_text") or "").strip(),
        "coating":    (raw.get("coating_text") or "").strip(),
        "print_mode": (raw.get("color_text") or "").strip(),
        "shape":      (raw.get("shape_text") or "").strip(),
        "section":    (raw.get("section_text") or "").strip(),
        "size":       size,
        "qty":        qty,
    }


def _load_targets() -> list[dict]:
    if not _CONFIG_PATH.exists():
        return []
    cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    return cfg.get("swadpia", [])


TARGETS = _load_targets()


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


class SwadpiaStickerCrawler:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.items: list[dict] = []

    def _init_browser(self, pw):
        browser = pw.chromium.launch(headless=self.headless)
        context = browser.new_context(viewport={"width": 1280, "height": 900}, locale="ko-KR")
        for pat in BLOCK_PATTERNS:
            context.route(pat, lambda r: r.abort())
        context.on("dialog", lambda d: d.dismiss())
        return browser, context

    def _set_select(self, page, name: str, value: str):
        page.evaluate(f"""() => {{
            const s = document.querySelector('select[name="{name}"]');
            if (s) {{ s.value = '{value}'; s.dispatchEvent(new Event('change', {{bubbles: true}})); }}
        }}""")

    def _set_size(self, page, size_mm: int):
        """가로/세로 input에 사이즈 입력."""
        page.evaluate(f"""() => {{
            const nativeSetter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            const wx = document.querySelector('input[name="domusong_x_size"]');
            const wy = document.querySelector('input[name="domusong_y_size"]');
            if (wx) {{ nativeSetter.call(wx, '{size_mm}'); wx.dispatchEvent(new Event('change', {{bubbles: true}})); }}
            if (wy) {{ nativeSetter.call(wy, '{size_mm}'); wy.dispatchEvent(new Event('change', {{bubbles: true}})); }}
        }}""")

    def _crawl_product(self, page, t: dict):
        url = f"{PAGE_BASE}/{t['category_code']}/{t['product_code']}"
        log.info(f"  [{t['product_name']}] {url}")

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
        except PwTimeout:
            log.warning("    page timeout, continuing")

        for paper in t["papers"]:
            for coating in t["coatings"]:
                for color in t["color_modes"]:
                    for shape in t["shapes"]:
                        # 유형: 자유형(합판)
                        self._set_select(page, "domusong_section", t.get("domusong_section", "DMS41"))
                        page.wait_for_timeout(1500)

                        # 기본 옵션 설정
                        self._set_select(page, "paper_code", paper["code"])
                        self._set_select(page, "print_color_type", color["code"])
                        self._set_select(page, "coating_type", coating["code"])
                        self._set_select(page, "domusong_type", shape["code"])
                        page.wait_for_timeout(1000)

                        for qty in t["qtys"]:
                            self._set_select(page, "paper_qty", str(qty))
                            page.wait_for_timeout(500)

                            for size_info in t["sizes"]:
                                size_mm = size_info["mm"]
                                self._set_size(page, size_mm)
                                page.wait_for_timeout(1500)

                                txt = page.evaluate(JS_GET_PRICE)
                                price = parse_total_price(txt or "")

                                if price is None or price <= 0:
                                    log.warning(f"    price fail: {paper['name']} / {coating['name']} / {size_info['name']} / {qty}")
                                    continue

                                dom = read_dom_state(page)
                                self.items.append({
                                    "product":    t["product_name"],
                                    "category":   "스티커",
                                    "paper_name": dom["paper_name"] or None,
                                    "coating":    dom["coating"]    or None,
                                    "print_mode": dom["print_mode"] or None,
                                    "size":       dom["size"]       or None,
                                    "qty":        dom["qty"]        or None,
                                    "price":      price,
                                    "price_vat_included": True,
                                    "url":        url,
                                    "url_ok":     True,
                                    "options": {
                                        "shape":               dom["shape"] or None,
                                        "domusong_section":    dom["section"] or None,
                                        "ea_per_sheet":        1,
                                        "config_paper_name":   paper["name"],
                                        "config_coating":      coating["name"],
                                        "config_color":        color["name"],
                                        "config_shape":        shape["name"],
                                        "config_size":         size_info["name"],
                                        "config_qty":          qty,
                                    },
                                })
                                log.info(f"    DOM: {dom['paper_name']} | {dom['coating']} | {dom['size']} | {dom['qty']} -> {price:,}")

    def run(self):
        log.info(f"=== 성원애드피아 스티커 크롤링 시작 ({len(TARGETS)}종 제품) ===")
        if not TARGETS:
            log.error("크롤 타겟 없음 -- config/sticker_targets.json swadpia 섹션 확인")
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
                    log.error(f"  crawl error ({t['product_name']}): {e}")
            browser.close()
        elapsed = time.time() - start
        log.info(f"=== 완료: {len(self.items)}건, {elapsed:.1f}초 ===")


def crawl_all() -> list[dict]:
    c = SwadpiaStickerCrawler(headless=True)
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
    log.info(f"saved: {raw_now} ({len(items)} items)")


if __name__ == "__main__":
    items = crawl_all()
    save(items)
