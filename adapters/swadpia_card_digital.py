"""성원애드피아 디지털 명함 어댑터 (card_digital).

처리 페이지: COD1000/GOD1001
DOM 구조:
- paper_type select (12종) → paper_code select 이 동적으로 변경됨
- coating 없음
- print_color_type, paper_size, paper_qty_select 그대로

target schema:
  - papers[ {paper_type, paper_code, paper_name_out} ]
"""
from typing import Iterator

from playwright.sync_api import sync_playwright

from adapters._swadpia_card_common import (
    JS_GET_AVAILABLE_QTY, build_item, goto_with_wait, init_browser,
    js_set_select, price_with_retry,
)
from engine.adapter import SiteAdapter
from engine.context import RawItem, RunContext


class Adapter(SiteAdapter):
    site = "swadpia"
    category = "card_digital"

    def fetch_and_extract(self, ctx: RunContext) -> Iterator[RawItem]:
        cat_cfg = ctx.site_config.get("card_digital", {})
        sel = cat_cfg.get("selectors", {})
        timeouts = cat_cfg.get("timeouts", {})
        guard = cat_cfg.get("low_price_guard", {})

        if not ctx.targets:
            ctx.log.event("fetch.fail", level="warning",
                          error="no targets for swadpia card_digital")
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

        # size 셋팅
        if t.get("size_value"):
            if not js_set_select(page, sel.get("paper_size"), t["size_value"]):
                ctx.log.event("extract.warn", product=t["product_name"],
                              error=f"size 셋팅 실패: {t['size_value']}")
            page.wait_for_timeout(timeouts.get("after_select_ms", 600))

        # 가용 qty
        avail_qtys = page.evaluate(JS_GET_AVAILABLE_QTY, sel.get("qty"))
        target_qtys = [q for q in t.get("qtys", []) if q in avail_qtys]
        if not target_qtys:
            ctx.log.event("extract.warn", product=t["product_name"],
                          error="no matching qty")
            return

        for paper in t.get("papers", []):
            # paper_type 셋팅 (paper_code 옵션이 동적으로 변함)
            if not js_set_select(page, sel.get("paper_type"), paper["paper_type"]):
                ctx.log.event("extract.warn", product=t["product_name"],
                              error=f"paper_type 셋팅 실패: {paper.get('paper_type')}")
                continue
            page.wait_for_timeout(timeouts.get("after_select_ms", 600))
            # paper_code 셋팅
            if not js_set_select(page, sel.get("paper_code"), paper["paper_code"]):
                ctx.log.event("extract.warn", product=t["product_name"],
                              error=f"paper_code 셋팅 실패: {paper.get('paper_code')}")
                continue
            page.wait_for_timeout(timeouts.get("after_select_ms", 600))
            # size 재셋팅 (paper 변경 시 reset 가능성)
            if t.get("size_value"):
                js_set_select(page, sel.get("paper_size"), t["size_value"])
                page.wait_for_timeout(300)

            for color in t.get("color_modes", []):
                if not js_set_select(page, sel.get("color_mode"), color["value"]):
                    continue
                page.wait_for_timeout(timeouts.get("after_select_ms", 600))

                for qty in target_qtys:
                    if not js_set_select(page, sel.get("qty"), str(qty)):
                        continue
                    page.wait_for_timeout(timeouts.get("after_qty_ms", 400))

                    price = price_with_retry(page, sel.get("price_supply"),
                                             qty, timeouts, guard)
                    if price is None:
                        ctx.log.event("extract.warn", product=t["product_name"],
                                      paper=paper.get("paper_code"),
                                      qty=qty, error="price read failed")
                        continue

                    yield build_item(page, t, paper, price, sel, has_coating=False)
