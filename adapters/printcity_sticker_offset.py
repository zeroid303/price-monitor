"""프린트시티 사각형 스티커 어댑터.

페이지: StickerRectangleNew (사각재단 스티커)
- materialCode (paper, 7옵션: 아트스티커-90g/유포지스티커-80g/모조스티커-80g/
  크라프트스티커-57g/투명데드롱-25g/은데드롱-25g/초강접아트-90g 등)
- coatingCode (4옵션: COT:NO/MS/GS/SRB) — 유광(COT:GS) 고정
- colorCode (COL:40 단면4도 고정)
- sizeKind_code (standardSize/customSize) — standardSize 사용
- sizeCode (14옵션: SIZ:ST-60x40 ~ 90X120) → 표준 8 ±5mm 매칭
- quantities (매수: 1000), case_counts (건수: 1)

가격: div.SumTotalPrice ('5,500' 형식) — VAT 포함 (총 결제액).
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
SIZE_TOL_MM = 5

DEFAULT_COATING = "COT:GS"        # 유광코팅
DEFAULT_COLOR = "COL:40"          # 단면4도
DEFAULT_MARGIN = "STM:TWO"        # 배경있음 (기본)
DEFAULT_QTY = "1000"


JS_SET_SELECT = """({selector, value}) => {
    const el = document.querySelector(selector);
    if (!el) return 'NO_EL';
    const opt = [...el.options].find(o => o.value === String(value));
    if (!opt) return 'NO_OPT';
    // React-controlled select: native setter 우회 + input/change 둘 다 dispatch
    const proto = Object.getPrototypeOf(el);
    const setter = Object.getOwnPropertyDescriptor(proto, 'value') ||
                   Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value');
    if (setter && setter.set) setter.set.call(el, String(value));
    else el.value = String(value);
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


def _parse_size(text: str):
    m = re.search(r"(\d+)\s*[x×*X]\s*(\d+)", text)
    if not m: return None
    return (int(m.group(1)), int(m.group(2)))


def _near_canonical(w: int, h: int, tol: int = SIZE_TOL_MM):
    for tw, th in CANONICAL_SIZES:
        if w == tw and h == th: return (tw, th)
    for tw, th in CANONICAL_SIZES:
        if abs(w - tw) <= tol and abs(h - th) <= tol: return (tw, th)
    return None


def _js_set(page, selector, value) -> bool:
    try:
        return page.evaluate(JS_SET_SELECT, {"selector": selector, "value": value}) is True
    except Exception:
        return False


class Adapter(SiteAdapter):
    site = "printcity"
    category = "sticker_offset"

    def fetch_and_extract(self, ctx: RunContext) -> Iterator[RawItem]:
        cat_cfg = ctx.site_config.get("sticker_offset", {})
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

        # 1. 공통 옵션 셋팅 (1회)
        _js_set(page, sel["color_code"], DEFAULT_COLOR)
        page.wait_for_timeout(timeouts.get("after_select_ms", 700))
        _js_set(page, sel["coating_code"], DEFAULT_COATING)
        page.wait_for_timeout(timeouts.get("after_select_ms", 700))
        _js_set(page, sel["size_kind_code"], "standardSize")
        page.wait_for_timeout(timeouts.get("after_select_ms", 700))
        if sel.get("sticker_touch_margin"):
            _js_set(page, sel["sticker_touch_margin"], DEFAULT_MARGIN)
            page.wait_for_timeout(timeouts.get("after_select_ms", 700))
        _js_set(page, sel["quantity"], DEFAULT_QTY)
        page.wait_for_timeout(timeouts.get("after_select_ms", 700))
        if sel.get("case_count"):
            _js_set(page, sel["case_count"], "1")
            page.wait_for_timeout(timeouts.get("after_select_ms", 700))

        # 2. sizeCode 옵션 dump → 표준 매핑
        size_opts = page.evaluate(JS_DUMP_OPTS, sel["size_code"])
        size_matches = []
        for o in size_opts:
            if not o["value"]: continue
            wh = _parse_size(o["text"])
            if not wh: continue
            near = _near_canonical(*wh)
            if near:
                size_matches.append((f"{near[0]}x{near[1]}", o["value"], o["text"]))
        ctx.log.event("size.matched", n=len(size_matches),
                      matches=[m[0] for m in size_matches])

        # 3. materialCode (paper) 7옵션 × 매칭 사이즈
        paper_opts = page.evaluate(JS_DUMP_OPTS, sel["material_code"])
        for paper in paper_opts:
            if not paper["value"]: continue
            if not _js_set(page, sel["material_code"], paper["value"]):
                continue
            page.wait_for_timeout(timeouts.get("after_select_ms", 700))

            # paper 변경 후 매수/건수 재셋팅
            _js_set(page, sel["quantity"], DEFAULT_QTY)
            page.wait_for_timeout(300)
            if sel.get("case_count"):
                _js_set(page, sel["case_count"], "1")
                page.wait_for_timeout(300)

            for canonical, sc_value, sc_text in size_matches:
                if not _js_set(page, sel["size_code"], sc_value):
                    continue
                page.wait_for_timeout(timeouts.get("after_price_trigger_ms", 1500))

                price_txt = page.evaluate(JS_GET_TEXT, sel["price_total"])
                price = _parse_price(price_txt)
                if not price or price <= 0:
                    page.wait_for_timeout(timeouts.get("retry_price_ms", 1500))
                    price = _parse_price(page.evaluate(JS_GET_TEXT, sel["price_total"]))
                if not price or price <= 0:
                    ctx.log.event("extract.skip", product=t["product_name"],
                                  paper=paper["text"], size=canonical, reason="가격 0/추출 실패")
                    continue

                yield RawItem(
                    product=t["product_name"],
                    category=t.get("category", "스티커"),
                    paper_name=paper["text"],
                    coating="유광코팅",
                    print_mode="단면4도",
                    size=canonical,
                    qty=int(DEFAULT_QTY),
                    price=price,
                    price_vat_included=True,
                    url=t["url"], url_ok=True,
                    options={
                        "material_code": paper["value"],
                        "size_code": sc_value,
                        "size_code_text": sc_text,
                        "coating_code": DEFAULT_COATING,
                        "shape": "사각형",
                    },
                )
