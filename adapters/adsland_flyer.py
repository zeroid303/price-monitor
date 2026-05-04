"""애즈랜드 합판전단 어댑터.

페이지: https://www.adsland.com/shop/order.php?IC=IC00023
DOM:
- size_book: A2/A3/A4/B3/B4 등
- paper: 9 paper (80모조/90~180아트/80~180스노우)
- dosu: 4/0(단면4도) / 4/4(양면8도)
- busu: 2000 (=0.5연), 4000(1연), ...
- 가격: bill_ttl_sub (공급가)
"""
from typing import Iterator

from playwright.sync_api import sync_playwright

from adapters._adsland_card_common import (
    goto_with_wait, init_browser, js_set_select, js_get_select_text,
    price_with_retry, trigger_smart, JS_GET_SELECT_VALUE,
)
from engine.adapter import SiteAdapter
from engine.context import RawItem, RunContext


class Adapter(SiteAdapter):
    site = "adsland"
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
            browser, context = init_browser(pw, ctx)
            page = context.new_page()
            try:
                for t in ctx.targets:
                    ctx.log.event("fetch.start", product=t["product_name"])
                    if not goto_with_wait(page, t["url"], timeouts, ctx, t["product_name"]):
                        continue
                    yield from self._crawl(ctx, page, t, sel, timeouts, guard)
            finally:
                browser.close()

    def _crawl(self, ctx, page, t, sel, timeouts, guard) -> Iterator[RawItem]:
        # kind=1 + busu=2000 고정
        if not js_set_select(page, sel["kind"], "1"):
            ctx.log.event("extract.warn", product=t["product_name"], error="kind=1 셋팅 실패")
            return
        page.wait_for_timeout(timeouts.get("after_select_ms", 500))
        if not js_set_select(page, sel["busu"], t["qty_value"]):
            ctx.log.event("extract.warn", product=t["product_name"],
                          error=f"busu {t['qty_value']} 셋팅 실패")
            return
        page.wait_for_timeout(timeouts.get("after_select_ms", 500))

        for paper in t["papers"]:
            if not js_set_select(page, sel["paper"], paper["paper_value"]):
                ctx.log.event("extract.warn", product=t["product_name"],
                              paper=paper["paper_value"], error="paper 셋팅 실패")
                continue
            page.wait_for_timeout(timeouts.get("after_select_ms", 500))

            for size in t["sizes"]:
                if not js_set_select(page, sel["size"], size["value"]):
                    continue
                page.wait_for_timeout(timeouts.get("after_select_ms", 500))
                # busu 재셋팅 (size 변경시 reset 가능)
                js_set_select(page, sel["busu"], t["qty_value"])
                page.wait_for_timeout(300)

                for cm in t["color_modes"]:
                    if not js_set_select(page, sel["dosu"], cm["value"]):
                        continue
                    page.wait_for_timeout(timeouts.get("after_select_ms", 500))
                    trigger_smart(page)

                    price = price_with_retry(page, sel, t["qty_mae"], timeouts, guard)
                    if price is None:
                        ctx.log.event("extract.warn", product=t["product_name"],
                                      paper=paper["paper_value"], size=size["size_label"],
                                      color=cm["name"], error="price read failed")
                        continue

                    yield RawItem(
                        product=t["product_name"], category=t["category"],
                        paper_name=paper["paper_name_out"],
                        coating=None, print_mode=cm["name"],
                        size=size["size_label"],
                        qty=t["qty_mae"], price=price,
                        price_vat_included=False,
                        url=t["url"], url_ok=True,
                        options={"paper_value": paper["paper_value"],
                                 "size_value": size["value"],
                                 "dosu_value": cm["value"],
                                 "busu_raw": t["qty_value"]},
                    )
