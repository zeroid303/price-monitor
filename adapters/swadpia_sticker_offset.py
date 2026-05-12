"""성원애드피아 사각형 스티커 어댑터.

페이지: CST1000/GST1001 (사각재단 스티커)
- paper_code 11옵션 (75아트/아트지 90g 초강접/유포지/모조지/크라프트/투명데드롱/은데드롱 등)
- 사이즈: cut_x_size / cut_y_size input (W/H mm 직접 입력)
- coating_type, print_color_type, paper_qty select

표준 8 사이즈 직접 입력 → paper × 사이즈 88 조합 시도, 1000매 0원이면 skip.
coating 기본 = 유광써멀라미네팅(COT70), print_color = SPD10 단면칼라(4도).

가격: #print_estimate_tot 텍스트에서 '총 합계금액 X원' 정규식 추출 (VAT 포함).
"""
import re
from typing import Iterator, Optional

from playwright.sync_api import sync_playwright

from adapters._swadpia_card_common import (
    init_browser, goto_with_wait, js_set_select, js_get_select_text,
)
from engine.adapter import SiteAdapter
from engine.context import RawItem, RunContext


# 표준 사각형 8 사이즈 (W mm, H mm)
CANONICAL_SIZES = [
    (60, 40), (80, 50), (90, 55), (90, 60),
    (90, 70), (90, 80), (90, 100), (90, 120),
]

# 기본 옵션값 (가장 일반적인 비교 조건)
DEFAULT_COATING = "COT70"      # 유광써멀라미네팅 (= 유광코팅)
DEFAULT_COLOR = "SPD10"        # 단면칼라(4도)
DEFAULT_INK = "INK10"          # 일반잉크


JS_GET_PRICE = """(selector) => {
    const e = document.querySelector(selector);
    if (!e) return null;
    return e.textContent.replace(/\\s+/g, ' ').trim();
}"""

JS_DUMP_SELECT_OPTS = """(selector) => {
    const s = document.querySelector(selector);
    if (!s) return [];
    return [...s.options].map(o => ({value: o.value, text: (o.textContent||'').trim()}));
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

_RE_TOTAL = re.compile(r"총\s*합계금액\s*[:：]?\s*[\\￦₩]?\s*([\d,]+)\s*원")


def _parse_total_price(txt: Optional[str]) -> Optional[int]:
    if not txt: return None
    m = _RE_TOTAL.search(txt)
    if not m:
        m2 = re.search(r"([\d,]{4,})", txt)
        if not m2: return None
        try: return int(m2.group(1).replace(",", ""))
        except ValueError: return None
    try: return int(m.group(1).replace(",", ""))
    except ValueError: return None


class Adapter(SiteAdapter):
    site = "swadpia"
    category = "sticker_offset"

    def fetch_and_extract(self, ctx: RunContext) -> Iterator[RawItem]:
        cat_cfg = ctx.site_config.get("sticker_offset", {})
        sel = cat_cfg.get("selectors", {})
        timeouts = cat_cfg.get("timeouts", {})

        if not ctx.targets:
            ctx.log.event("fetch.fail", level="warning", error="no targets")
            return

        with sync_playwright() as pw:
            browser, context = init_browser(pw, ctx)
            page = context.new_page()
            try:
                for t in ctx.targets:
                    ctx.log.event("fetch.start", product=t["product_name"])
                    if not goto_with_wait(page, t["url"], timeouts, ctx, t["product_name"]):
                        continue
                    yield from self._crawl(ctx, page, t, sel, timeouts)
            finally:
                browser.close()

    def _crawl(self, ctx, page, t, sel, timeouts) -> Iterator[RawItem]:
        # 1. 기본 옵션 셋팅 (1회)
        js_set_select(page, sel["print_color_type"], DEFAULT_COLOR)
        page.wait_for_timeout(timeouts.get("after_select_ms", 700))
        js_set_select(page, sel["coating_type"], DEFAULT_COATING)
        page.wait_for_timeout(timeouts.get("after_select_ms", 700))
        if sel.get("sticker_ink"):
            js_set_select(page, sel["sticker_ink"], DEFAULT_INK)
            page.wait_for_timeout(timeouts.get("after_select_ms", 700))

        qty_target = t.get("qty_target", "1000")
        js_set_select(page, sel["paper_qty"], qty_target)
        page.wait_for_timeout(timeouts.get("after_select_ms", 700))

        # 2. paper_code 11옵션 dump
        paper_opts = page.evaluate(JS_DUMP_SELECT_OPTS, sel["paper_code"])
        ctx.log.event("paper.count", n=len(paper_opts))

        for paper in paper_opts:
            if not paper["value"]:
                continue
            if not js_set_select(page, sel["paper_code"], paper["value"]):
                continue
            page.wait_for_timeout(timeouts.get("after_select_ms", 700))

            # paper 변경 후 매수 재셋팅 (사이트가 리셋할 수 있음)
            js_set_select(page, sel["paper_qty"], qty_target)
            page.wait_for_timeout(timeouts.get("after_select_ms", 700))

            for w, h in CANONICAL_SIZES:
                # 사이즈 input 설정
                page.evaluate(JS_SET_INPUT, {"selector": sel["cut_x_size"], "value": w})
                page.wait_for_timeout(200)
                page.evaluate(JS_SET_INPUT, {"selector": sel["cut_y_size"], "value": h})
                page.wait_for_timeout(timeouts.get("after_size_input_ms", 1000))

                # 가격 추출
                txt = page.evaluate(JS_GET_PRICE, sel["price_total"])
                price = _parse_total_price(txt or "")
                if not price or price <= 0:
                    page.wait_for_timeout(timeouts.get("retry_price_ms", 1500))
                    txt = page.evaluate(JS_GET_PRICE, sel["price_total"])
                    price = _parse_total_price(txt or "")
                if not price or price <= 0:
                    ctx.log.event("extract.skip", product=t["product_name"],
                                  paper=paper["text"], size=f"{w}x{h}",
                                  reason="가격 0 또는 추출 실패")
                    continue

                # DOM 실측 (paper/coating/color)
                paper_name = js_get_select_text(page, sel["paper_code"]) or paper["text"]
                coating = js_get_select_text(page, sel["coating_type"]) or None
                print_mode = js_get_select_text(page, sel["print_color_type"]) or None

                yield RawItem(
                    product=t["product_name"],
                    category=t.get("category", "스티커"),
                    paper_name=paper_name,
                    coating=coating,
                    print_mode=print_mode,
                    size=f"{w}x{h}",
                    qty=int(qty_target),
                    price=price,
                    price_vat_included=True,
                    url=t["url"], url_ok=True,
                    options={
                        "paper_code": paper["value"],
                        "cut_x_mm": w, "cut_y_mm": h,
                        "shape": "사각형",
                    },
                )
