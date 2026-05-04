"""디티피아 합판전단 어댑터.

페이지: https://dtpia.co.kr/Order/Flyer/Happan.aspx
DOM 구조:
- mtrl_cd select: 5 paper (아트 90/120/150/180g, 모조 80g)
- sdiv select: A=국전 / B=4*6전 → sdiv_cd 동적 변경
- prn_clr_cn_gb: 4=단면칼라 / 8=양면칼라
- ream_cn: 0.5R = 2000매 (1R = 4000매)
- 가격: est_scroll_ord_am (공급가)
"""
import re
from typing import Iterator, Optional

from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

from engine.adapter import SiteAdapter
from engine.context import RawItem, RunContext


JS_SET_SELECT = """({sel_id, value}) => {
    const el = document.getElementById(sel_id);
    if (!el) return 'NO_EL';
    const opt = [...el.options].find(o => o.value === String(value));
    if (!opt) return 'NO_OPT';
    el.value = String(value);
    const oc = el.getAttribute('onchange');
    if (oc) { try { eval(oc); } catch(e) {} }
    el.dispatchEvent(new Event('change', {bubbles: true}));
    if (window.jQuery) { try { window.jQuery(el).trigger('change'); } catch(e) {} }
    return true;
}"""

JS_GET_SELECT_TEXT = """(sel_id) => {
    const el = document.getElementById(sel_id);
    if (!el || el.selectedIndex < 0) return '';
    return (el.options[el.selectedIndex]?.textContent || '').trim();
}"""

JS_GET_PRICE = """(sel_id) => {
    const el = document.getElementById(sel_id);
    return el ? el.textContent.trim() : null;
}"""


def _parse_price(txt) -> Optional[int]:
    if not txt: return None
    m = re.search(r"[\d,]+", txt)
    if not m: return None
    try:
        return int(m.group().replace(",", ""))
    except ValueError:
        return None


class Adapter(SiteAdapter):
    site = "dtpia"
    category = "flyer"

    def fetch_and_extract(self, ctx: RunContext) -> Iterator[RawItem]:
        cat_cfg = ctx.site_config.get("flyer", {})
        sel = cat_cfg.get("selectors", {})
        timeouts = cat_cfg.get("timeouts", {})
        guard = cat_cfg.get("low_price_guard", {})

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
            page = context.new_page()
            try:
                for t in ctx.targets:
                    ctx.log.event("fetch.start", product=t.get("product_name"))
                    yield from self._crawl(ctx, page, t, sel, timeouts, guard)
            finally:
                browser.close()

    def _crawl(self, ctx, page, t, sel, timeouts, guard) -> Iterator[RawItem]:
        try:
            page.goto(t["url"], wait_until="domcontentloaded",
                      timeout=timeouts.get("page_goto_ms", 30000))
            page.wait_for_timeout(timeouts.get("after_goto_ms", 2500))
        except PwTimeout:
            ctx.log.event("fetch.fail", level="error", product=t["product_name"], error="goto timeout")
            return

        # qty 셋팅 (0.5R 고정)
        page.evaluate(JS_SET_SELECT, {"sel_id": sel["qty"], "value": t["qty_value"]})
        page.wait_for_timeout(timeouts.get("after_select_ms", 500))

        for paper in t["papers"]:
            # paper 셋팅
            r = page.evaluate(JS_SET_SELECT, {"sel_id": sel["mtrl_cd"], "value": paper["mtrl_cd"]})
            if r is not True:
                ctx.log.event("extract.warn", product=t["product_name"],
                              error=f"paper {paper['mtrl_cd']} 셋팅 실패")
                continue
            page.wait_for_timeout(timeouts.get("after_select_ms", 500))

            for size in t["sizes"]:
                # sdiv (국전/4*6전) 토글
                page.evaluate(JS_SET_SELECT, {"sel_id": sel["sdiv"], "value": size["sdiv"]})
                page.wait_for_timeout(timeouts.get("after_select_ms", 500))
                r = page.evaluate(JS_SET_SELECT, {"sel_id": sel["sdiv_cd"], "value": size["sdiv_cd"]})
                if r is not True:
                    continue
                page.wait_for_timeout(timeouts.get("after_select_ms", 500))

                for cm in t["color_modes"]:
                    page.evaluate(JS_SET_SELECT, {"sel_id": sel["color_mode"], "value": cm["value"]})
                    page.wait_for_timeout(timeouts.get("after_price_trigger_ms", 900))

                    price_txt = page.evaluate(JS_GET_PRICE, sel["price"])
                    price = _parse_price(price_txt)
                    if price is None:
                        page.wait_for_timeout(timeouts.get("retry_price_ms", 1500))
                        price_txt = page.evaluate(JS_GET_PRICE, sel["price"])
                        price = _parse_price(price_txt)
                    if price is None:
                        ctx.log.event("extract.warn", product=t["product_name"],
                                      paper=paper["mtrl_cd"], size=size["size_label"],
                                      color=cm["name"], error="price read failed")
                        continue

                    yield RawItem(
                        product=t["product_name"],
                        category=t["category"],
                        paper_name=paper["paper_name_out"],
                        coating=None,
                        print_mode=cm["name"],
                        size=size["size_label"],
                        qty=t["qty_mae"],
                        price=price,
                        price_vat_included=False,
                        url=t["url"],
                        url_ok=True,
                        options={"mtrl_cd": paper["mtrl_cd"],
                                 "sdiv": size["sdiv"], "sdiv_cd": size["sdiv_cd"],
                                 "color_value": cm["value"], "qty_R": t["qty_value"]},
                    )
