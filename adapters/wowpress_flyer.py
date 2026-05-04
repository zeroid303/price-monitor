"""와우프레스 합판전단 어댑터.

페이지: https://wowpress.co.kr/ordr/prod/dets?ProdNo=40026
DOM:
- pdata_00_sizeno: A2/A3/A4/B3/B4 등 14 size
- pdata_00_colorno: 단면 칼라4도(255) / 양면 칼라8도(256)
- spdata_00_paperno3: 아트지 (1종)
- spdata_00_paperno4: 100g / 150g
- spdata_00_ordqty: 0.5/1/2/3/.../4 연 (1연=500매, 4연=2000매)
- 가격: od_00_totalcost - od_00_taxcost (공급가)
"""
from typing import Iterator

from playwright.sync_api import sync_playwright

from adapters._wowpress_card_common import (
    JS_AVAIL_OPTIONS, build_item, goto_with_wait, init_browser,
    js_set_select, price_with_retry, select_paper, read_dom_state,
)
from engine.adapter import SiteAdapter
from engine.context import RawItem, RunContext


class Adapter(SiteAdapter):
    site = "wowpress"
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
        # qty 셋팅
        if not js_set_select(page, sel["qty"], t["qty_value"]):
            ctx.log.event("extract.warn", product=t["product_name"], error="qty 셋팅 실패")
            return
        page.wait_for_timeout(timeouts.get("after_select_ms", 800))

        for size in t["sizes"]:
            if not js_set_select(page, sel["size"], size["sizeno"]):
                continue
            page.wait_for_timeout(timeouts.get("after_select_ms", 800))

            for cm in t["color_modes"]:
                if not js_set_select(page, sel["color_mode"], cm["value"]):
                    continue
                page.wait_for_timeout(timeouts.get("after_select_ms", 800))

                # qty 가용 체크
                avail = page.evaluate(JS_AVAIL_OPTIONS, sel["qty"])
                if str(t["qty_value"]) not in avail:
                    ctx.log.event("extract.warn", product=t["product_name"],
                                  size=size["size_label"], color=cm["name"],
                                  error=f"qty {t['qty_value']} 가용 X")
                    continue
                js_set_select(page, sel["qty"], t["qty_value"])
                page.wait_for_timeout(timeouts.get("after_select_ms", 800))

                for paper in t["papers"]:
                    if not select_paper(page, sel, paper["paper_no"]):
                        ctx.log.event("extract.warn", product=t["product_name"],
                                      paper_no=paper["paper_no"], error="paper 셋팅 실패")
                        continue
                    # qty 재셋팅 (paper 변경 시)
                    js_set_select(page, sel["qty"], t["qty_value"])
                    page.wait_for_timeout(timeouts.get("after_qty_ms", 1200))

                    price = price_with_retry(page, sel, t["qty_mae"], timeouts, guard)
                    if price is None:
                        ctx.log.event("extract.warn", product=t["product_name"],
                                      paper_no=paper["paper_no"], size=size["size_label"],
                                      color=cm["name"], error="price read failed")
                        continue

                    # build_item 은 명함용 — 여기서 직접 build
                    yield RawItem(
                        product=t["product_name"], category=t["category"],
                        paper_name=paper["paper_name_out"],
                        coating=None, print_mode=cm["name"],
                        size=size["size_label"],
                        qty=t["qty_mae"], price=price,
                        price_vat_included=False,
                        url=t["url"], url_ok=True,
                        options={"paper_no": paper["paper_no"],
                                 "sizeno": size["sizeno"],
                                 "color_value": cm["value"],
                                 "qty_yeon": t["qty_value"]},
                    )
