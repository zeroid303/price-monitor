"""프린트시티 디지털 스티커 어댑터.

페이지: DigitalSticker (React-controlled, 합판 sticker 와 다른 페이지)
- materialCode 4옵션 (아트스티커-90g / 모조스티커-80g / 크라프트스티커-57g / 그문드 화이트우드)
- coatingCode 5옵션 (코팅없음/유광/홀로그램 도트/심플/스타)
- colorCode COL:41 양면5도 고정
- sizeKind_code = customSize 고정 (별사이즈 입력만 가능)
- sizeCode = SIZ:CUSTOM 만, width/height input 으로 W/H mm 직접 입력
- quantities (paper_qty) 1000~ 미지원. 표준 100매.

가격: #SumTotalPrice (VAT 포함).
표준 8 사이즈 × paper 4 × coating 5 = 최대 160 조합 (홀로그램은 비교 가능성 별도).
초기 비교: coating 유광 (COT:GS) 만 → 32 조합 (4×8).
"""
import re
from typing import Iterator, Optional

from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

from engine.adapter import SiteAdapter
from engine.context import RawItem, RunContext


CANONICAL_SIZES = [
    (60, 40), (80, 50), (90, 55), (90, 60),
    (90, 70), (90, 80), (90, 100), (90, 120),
]
DEFAULT_COATING = "COT:GS"        # 유광코팅
DEFAULT_COLOR = "COL:41"          # 양면5도 (디지털 페이지 colorCode)
DEFAULT_QTY = "100"


JS_SET_SELECT = """({selector, value}) => {
    const el = document.querySelector(selector);
    if (!el) return 'NO_EL';
    const opt = [...el.options].find(o => o.value === String(value));
    if (!opt) return 'NO_OPT';
    const proto = Object.getPrototypeOf(el);
    const setter = Object.getOwnPropertyDescriptor(proto, 'value') ||
                   Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value');
    if (setter && setter.set) setter.set.call(el, String(value));
    else el.value = String(value);
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return true;
}"""

JS_SET_INPUT = """({selector, value}) => {
    const el = document.querySelector(selector);
    if (!el) return false;
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setter.call(el, String(value));
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return true;
}"""

JS_DUMP_OPTS = """(selector) => {
    const el = document.querySelector(selector);
    if (!el) return [];
    return [...el.options].map(o => ({value: o.value, text: (o.textContent||'').trim()}));
}"""

JS_GET_TEXT = """(selector) => {
    const el = document.querySelector(selector);
    return el ? (el.textContent || '').trim() : null;
}"""


def _parse_price(txt: Optional[str]) -> Optional[int]:
    if not txt: return None
    m = re.search(r"[\d,]+", txt)
    if not m: return None
    try: return int(m.group().replace(",", ""))
    except ValueError: return None


def _js_set(page, selector, value) -> bool:
    try:
        return page.evaluate(JS_SET_SELECT, {"selector": selector, "value": value}) is True
    except Exception:
        return False


class Adapter(SiteAdapter):
    site = "printcity"
    category = "sticker_digital"

    def fetch_and_extract(self, ctx: RunContext) -> Iterator[RawItem]:
        cat_cfg = ctx.site_config.get("sticker_digital", {})
        sel = cat_cfg.get("selectors", {})
        timeouts = cat_cfg.get("timeouts", {})

        if not ctx.targets:
            ctx.log.event("fetch.fail", level="warning", error="no targets")
            return

        with sync_playwright() as pw:
            browser_cfg = ctx.site_config.get("browser", {})
            browser = pw.chromium.launch(headless=browser_cfg.get("headless", True))
            context = browser.new_context(
                viewport=browser_cfg.get("viewport", {"width": 1280, "height": 900}),
                locale=ctx.site_config.get("locale", "ko-KR"),
            )
            for pat in ctx.site_config.get("block_patterns", []):
                context.route(pat, lambda r: r.abort())
            context.on("dialog", lambda d: d.dismiss())
            page = context.new_page()
            try:
                for t in ctx.targets:
                    ctx.log.event("fetch.start", product=t["product_name"])
                    yield from self._crawl(ctx, page, t, sel, timeouts)
            finally:
                browser.close()

    def _crawl(self, ctx, page, t, sel, timeouts) -> Iterator[RawItem]:
        try:
            page.goto(t["url"], wait_until="domcontentloaded",
                      timeout=timeouts.get("page_goto_ms", 30000))
            page.wait_for_timeout(timeouts.get("after_goto_ms", 3000))
        except PwTimeout:
            ctx.log.event("fetch.fail", level="error", product=t["product_name"], error="goto timeout")
            return

        # 공통: customSize + 유광 + 양면5도 + qty=100 + case=1
        _js_set(page, sel["color_code"], DEFAULT_COLOR)
        page.wait_for_timeout(timeouts.get("after_select_ms", 700))
        _js_set(page, sel["coating_code"], DEFAULT_COATING)
        page.wait_for_timeout(timeouts.get("after_select_ms", 700))
        _js_set(page, sel["size_kind_code"], "customSize")
        page.wait_for_timeout(timeouts.get("after_select_ms", 700))
        _js_set(page, sel["quantity"], DEFAULT_QTY)
        page.wait_for_timeout(timeouts.get("after_select_ms", 700))
        if sel.get("case_count"):
            _js_set(page, sel["case_count"], "1")
            page.wait_for_timeout(timeouts.get("after_select_ms", 700))

        # paper 4종 × 표준 8 사이즈
        paper_opts = page.evaluate(JS_DUMP_OPTS, sel["material_code"])
        for paper in paper_opts:
            if not paper["value"]: continue
            if not _js_set(page, sel["material_code"], paper["value"]):
                continue
            page.wait_for_timeout(timeouts.get("after_select_ms", 700))
            # paper 변경 후 qty 재셋팅
            _js_set(page, sel["quantity"], DEFAULT_QTY)
            page.wait_for_timeout(300)

            for w, h in CANONICAL_SIZES:
                # width/height input 으로 사이즈 직접 입력
                page.evaluate(JS_SET_INPUT, {"selector": sel["width"], "value": w})
                page.wait_for_timeout(300)
                page.evaluate(JS_SET_INPUT, {"selector": sel["height"], "value": h})
                page.wait_for_timeout(timeouts.get("after_price_trigger_ms", 1500))

                price = _parse_price(page.evaluate(JS_GET_TEXT, sel["price_total"]))
                if not price or price <= 0:
                    page.wait_for_timeout(timeouts.get("retry_price_ms", 1500))
                    price = _parse_price(page.evaluate(JS_GET_TEXT, sel["price_total"]))
                if not price or price <= 0:
                    ctx.log.event("extract.skip", product=t["product_name"],
                                  paper=paper["text"], size=f"{w}x{h}", reason="가격 0")
                    continue

                yield RawItem(
                    product=t["product_name"],
                    category=t.get("category", "스티커"),
                    paper_name=paper["text"],
                    coating="유광코팅",
                    print_mode="양면5도",
                    size=f"{w}x{h}",
                    qty=int(DEFAULT_QTY),
                    price=price,
                    price_vat_included=True,
                    url=t["url"], url_ok=True,
                    options={
                        "material_code": paper["value"],
                        "width_mm": w, "height_mm": h,
                        "coating_code": DEFAULT_COATING,
                        "shape": "사각형",
                    },
                )
