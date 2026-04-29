"""와우프레스 디지털 명함 어댑터 (card_digital).

처리 페이지 (2):
  - 디지털AP명함     (ProdNo=40064)
  - 디지털인디고명함 (ProdNo=40061)

UI 메커니즘은 offset 페이지와 동일 (paperno3/4 + size + color + qty).
공급가 = od_00_totalcost - od_00_taxcost.
"""
from typing import Iterator

from playwright.sync_api import sync_playwright

from adapters._wowpress_card_common import (
    JS_AVAIL_OPTIONS, build_item, goto_with_wait, init_browser,
    js_set_select, price_with_retry, select_paper,
)
from engine.adapter import SiteAdapter
from engine.context import RawItem, RunContext


class Adapter(SiteAdapter):
    site = "wowpress"
    category = "card_digital"

    def fetch_and_extract(self, ctx: RunContext) -> Iterator[RawItem]:
        cat_cfg = ctx.site_config.get("card_digital", {})
        sel = cat_cfg.get("selectors", {})
        timeouts = cat_cfg.get("timeouts", {})
        guard = cat_cfg.get("low_price_guard", {})

        if not ctx.targets:
            ctx.log.event("fetch.fail", level="warning",
                          error="no targets for wowpress card_digital")
            return

        with sync_playwright() as pw:
            browser, context = init_browser(pw, ctx)
            page = context.new_page()
            try:
                for i, t in enumerate(ctx.targets, 1):
                    ctx.log.event(
                        "fetch.start",
                        product=t.get("product_name"),
                        index=i, total=len(ctx.targets),
                    )
                    try:
                        yield from self._crawl_target(ctx, page, t, sel, timeouts, guard)
                    except Exception as e:
                        ctx.log.event("fetch.fail", level="error",
                                      product=t.get("product_name"), error=str(e))
            finally:
                browser.close()

    def _crawl_target(
        self, ctx: RunContext, page, t: dict,
        sel: dict, timeouts: dict, guard: dict,
    ) -> Iterator[RawItem]:
        if not goto_with_wait(page, t["url"], timeouts, ctx, t["product_name"]):
            return

        if t.get("size_value"):
            if not js_set_select(page, sel.get("size"), t["size_value"]):
                ctx.log.event("extract.warn", product=t["product_name"],
                              error=f"size 셋팅 실패: {t['size_value']}")
                return
            page.wait_for_timeout(timeouts.get("after_select_ms", 800))

        for color in t.get("color_modes", []):
            if not js_set_select(page, sel.get("color_mode"), color["value"]):
                ctx.log.event("extract.warn", product=t["product_name"],
                              error=f"color 셋팅 실패: {color.get('name')}")
                continue
            page.wait_for_timeout(timeouts.get("after_select_ms", 800))

            avail_qtys = page.evaluate(JS_AVAIL_OPTIONS, sel.get("qty"))
            target_qtys = [q for q in t.get("qtys", []) if str(q) in avail_qtys]
            if not target_qtys:
                ctx.log.event("extract.warn", product=t["product_name"],
                              color=color.get("name"), error="no matching qty")
                continue

            for paper in t.get("papers", []):
                if not select_paper(page, sel, paper["paper_no"]):
                    ctx.log.event("extract.warn", product=t["product_name"],
                                  paper_no=paper.get("paper_no"),
                                  error="paper 셋팅 실패")
                    continue

                for qty in target_qtys:
                    if not js_set_select(page, sel.get("qty"), str(qty)):
                        continue
                    price = price_with_retry(page, sel, qty, timeouts, guard)
                    if price is None:
                        ctx.log.event("extract.warn", product=t["product_name"],
                                      paper_no=paper.get("paper_no"),
                                      qty=qty, error="price read failed")
                        continue
                    yield build_item(page, t, price, sel)
