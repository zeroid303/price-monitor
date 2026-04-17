"""
프린트시티 도무송 스티커 크롤러.
config/sticker_targets.json printcity 섹션의 (용지 x 코팅 x 사이즈) 조합을 크롤링.

설계 원칙:
  - output은 raw 값 그대로 저장 (output_template.json 스티커 예시 참조).
  - 정규화는 common.normalize가 sticker_mapping_rule 보고 적용.
  - 프린트시티 스티커는 priceCalculation 방식이라 고정 가격 테이블이 없음.
    → Playwright로 페이지 DOM 조작 후 견적서에서 총결제액 읽기.

조회 조건:
  - shape: 원형 (STT:ROU)
  - color: 양면5도 (COL:41)
  - sizes: sticker_targets.json에 정의된 사이즈 목록
  - qty: 1000매
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

COMPANY = "printcity"
CATEGORY = "sticker"

BLOCK_PATTERNS = [
    "**/google-analytics.com/**", "**/googletagmanager.com/**",
    "**/facebook.net/**", "**/facebook.com/tr/**", "**/doubleclick.net/**",
    "**/criteo.net/**", "**/criteo.com/**", "**/analytics.tiktok.com/**",
]


def _load_targets() -> list[dict]:
    if not _CONFIG_PATH.exists():
        return []
    cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    return cfg.get("printcity", [])


TARGETS = _load_targets()


def _read_total_price(page) -> int | None:
    """견적서의 총결제액 읽기."""
    text = page.evaluate(r"""() => {
        const allEls = [...document.querySelectorAll('*')];
        for (const el of allEls) {
            const t = el.textContent || '';
            if (t.includes('총결제액') && t.length < 100) {
                const m = t.match(/([\d,]+)\s*원/);
                if (m) return m[1];
            }
        }
        for (const el of allEls) {
            const t = el.textContent || '';
            if (t.includes('총 결제액') && t.length < 200) {
                const matches = [...t.matchAll(/([\d,]+)\s*원/g)];
                if (matches.length > 0) return matches[matches.length - 1][1];
            }
        }
        return null;
    }""")
    if text:
        try:
            return int(text.replace(",", ""))
        except ValueError:
            pass
    return None


def _set_select(page, selector_id: str, value: str):
    """select#id 에 값 설정 + change 이벤트."""
    page.evaluate(f"""() => {{
        const s = document.querySelector('select#{selector_id}');
        if (s) {{ s.value = '{value}'; s.dispatchEvent(new Event('change', {{bubbles: true}})); }}
    }}""")


class PrintcityStickerCrawler:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.items: list[dict] = []

    def _init_browser(self, pw):
        browser = pw.chromium.launch(headless=self.headless)
        context = browser.new_context(viewport={"width": 1400, "height": 900}, locale="ko-KR")
        for pat in BLOCK_PATTERNS:
            context.route(pat, lambda r: r.abort())
        context.on("dialog", lambda d: d.dismiss())
        return browser, context

    def _crawl_product(self, page, t: dict):
        url = t["url"]
        log.info(f"  [{t['product_name']}] {url}")

        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
        except PwTimeout:
            log.warning("    page timeout, continuing")
        page.wait_for_timeout(2000)

        for paper in t["papers"]:
            for coating in t["coatings"]:
                for color in t["color_modes"]:
                    for shape in t["shapes"]:
                        # 기본 옵션 설정
                        _set_select(page, "materialCode", paper["code"])
                        page.wait_for_timeout(1000)
                        _set_select(page, "coatingCode", coating["code"])
                        page.wait_for_timeout(500)
                        _set_select(page, "colorCode", color["code"])
                        page.wait_for_timeout(500)
                        _set_select(page, "stickerTypeCode", shape["code"])
                        page.wait_for_timeout(1000)

                        for qty in t["qtys"]:
                            _set_select(page, "quantities", str(qty))
                            page.wait_for_timeout(500)

                            for size_info in t["sizes"]:
                                # 사이즈 prefix 체크 (원형이면 SIZ:ROU- 인지)
                                if not size_info["code"].startswith(shape.get("size_prefix", "")):
                                    continue

                                _set_select(page, "sizeCode", size_info["code"])
                                page.wait_for_timeout(2000)

                                price = _read_total_price(page)
                                if price is None or price <= 0:
                                    log.warning(f"    price fail: {paper['name']} / {coating['name']} / {size_info['name']} / {qty}")
                                    continue

                                ea = size_info.get("ea_per_sheet", 1)
                                self.items.append({
                                    "product": t["product_name"],
                                    "category": "스티커",
                                    "paper_name": paper["name"],
                                    "coating": coating["name"],
                                    "print_mode": color["name"],
                                    "size": size_info["name"],
                                    "qty": qty,
                                    "price": price,
                                    "price_vat_included": True,
                                    "url": url,
                                    "url_ok": True,
                                    "options": {
                                        "shape": shape["name"],
                                        "ea_per_sheet": ea,
                                    },
                                })
                                log.info(f"    {paper['name']} | {coating['name']} | {size_info['name']} | {qty} -> {price:,} (ea={ea})")

    def run(self):
        log.info(f"=== 프린트시티 스티커 크롤링 시작 ({len(TARGETS)}종 제품) ===")
        if not TARGETS:
            log.error("크롤 타겟 없음 -- config/sticker_targets.json printcity 섹션 확인")
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
    c = PrintcityStickerCrawler(headless=True)
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
