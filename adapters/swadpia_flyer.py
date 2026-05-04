"""성원애드피아 합판전단 어댑터.

페이지: https://www.swadpia.co.kr/goods/goods_view/CLF1000/GLF1001
DOM:
- paper_type → paper_code 종속
- paper_size: A2/A3/A4/B3/B4
- fside_color_amount=4 (고정) + bside_color_amount=0(단면)/4(양면)
- paper_qty=2000 (직접)
- 가격: tr.estimate_supply_amt td.price (공급가)
"""
from typing import Iterator

from playwright.sync_api import sync_playwright

from adapters._swadpia_card_common import (
    JS_GET_SELECT_VALUE, init_browser, goto_with_wait,
    js_set_select, js_get_select_text, read_supply_price, parse_price,
)
from engine.adapter import SiteAdapter
from engine.context import RawItem, RunContext


class Adapter(SiteAdapter):
    site = "swadpia"
    category = "flyer"

    def fetch_and_extract(self, ctx: RunContext) -> Iterator[RawItem]:
        cat_cfg = ctx.site_config.get("flyer", {})
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
        # qty 셋팅
        if not js_set_select(page, sel["paper_qty"], t["qty_value"]):
            ctx.log.event("extract.warn", product=t["product_name"], error="qty 셋팅 실패")
            return
        page.wait_for_timeout(timeouts.get("after_select_ms", 600))
        # fside_color_amount 항상 4 (앞면 칼라 고정)
        js_set_select(page, sel["fside_color_amount"], "4")
        page.wait_for_timeout(timeouts.get("after_select_ms", 600))

        for combo in t["paper_combos"]:
            # paper_type → paper_code 종속
            if not js_set_select(page, sel["paper_type"], combo["paper_type"]):
                ctx.log.event("extract.warn", product=t["product_name"],
                              paper=combo["paper_name_out"], error="paper_type 실패")
                continue
            page.wait_for_timeout(timeouts.get("after_select_ms", 600))
            if not js_set_select(page, sel["paper_code"], combo["paper_code"]):
                ctx.log.event("extract.warn", product=t["product_name"],
                              paper=combo["paper_code"], error="paper_code 실패")
                continue
            page.wait_for_timeout(timeouts.get("after_select_ms", 600))
            # qty 재셋팅 (paper 변경 시 reset 가능)
            js_set_select(page, sel["paper_qty"], t["qty_value"])
            page.wait_for_timeout(300)

            for size in t["sizes"]:
                if not js_set_select(page, sel["paper_size"], size["paper_size"]):
                    continue
                page.wait_for_timeout(timeouts.get("after_select_ms", 600))
                # qty 재셋팅 (size 변경시도)
                js_set_select(page, sel["paper_qty"], t["qty_value"])
                page.wait_for_timeout(300)

                for cm in t["color_modes"]:
                    js_set_select(page, sel["fside_color_amount"], cm["fside_color_amount"])
                    page.wait_for_timeout(timeouts.get("after_select_ms", 600))
                    js_set_select(page, sel["bside_color_amount"], cm["bside_color_amount"])
                    page.wait_for_timeout(timeouts.get("after_price_trigger_ms", 700))

                    price = read_supply_price(page, sel["price_supply"], 0)
                    if price is None:
                        page.wait_for_timeout(timeouts.get("retry_price_ms", 1500))
                        price = read_supply_price(page, sel["price_supply"], 0)
                    if price is None:
                        ctx.log.event("extract.warn", product=t["product_name"],
                                      paper=combo["paper_name_out"], size=size["size_label"],
                                      color=cm["name"], error="price read failed")
                        continue

                    yield RawItem(
                        product=t["product_name"], category=t["category"],
                        paper_name=combo["paper_name_out"],
                        coating=None, print_mode=cm["name"],
                        size=size["size_label"],
                        qty=t["qty_mae"], price=price,
                        price_vat_included=False,
                        url=t["url"], url_ok=True,
                        options={"paper_type": combo["paper_type"],
                                 "paper_code": combo["paper_code"],
                                 "paper_size": size["paper_size"],
                                 "qty_raw": t["qty_value"]},
                    )
