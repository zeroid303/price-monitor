"""성원애드피아 오프셋 명함 어댑터 (card_offset).

처리 페이지 (6):
  - 일반지명함         (CNC1000/GNC1001)
  - 고급지명함         (CNC2000/GNC2001)
  - 카드명함           (CNC3000/GNC3001)
  - 하이브리드명함     (CNC4000/GNC4001)
  - 투명하이브리드명함 (CNC5000/GNC5001)
  - 디지털박에폭시명함 (CNC6000/GNC6001)

target schema:
  - product_name, category, url, page_type='select_radio'
  - size_value (페이지마다 다름: N0100=90×50, N0400=90×50 카드용, N0300=86×54 투명용)
  - qtys (사이트 가용 한정), color_modes
  - papers[ {paper_code, paper_name_out} ]
  - coatings[ {name, radio_name(paper_gloss/paper_gloss2), value(PAG10/PAG20/PAG99)} ]
"""
from typing import Iterator

from playwright.sync_api import sync_playwright

from adapters._swadpia_card_common import (
    JS_GET_AVAILABLE_QTY, build_item, goto_with_wait, init_browser,
    js_click_radio, js_set_select, price_with_retry,
)
from engine.adapter import SiteAdapter
from engine.context import RawItem, RunContext


class Adapter(SiteAdapter):
    site = "swadpia"
    category = "card_offset"

    def fetch_and_extract(self, ctx: RunContext) -> Iterator[RawItem]:
        cat_cfg = ctx.site_config.get("card_offset", {})
        sel = cat_cfg.get("selectors", {})
        timeouts = cat_cfg.get("timeouts", {})
        guard = cat_cfg.get("low_price_guard", {})

        if not ctx.targets:
            ctx.log.event("fetch.fail", level="warning",
                          error="no targets for swadpia card_offset")
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

        # 가용 qty 교집합
        avail_qtys = page.evaluate(JS_GET_AVAILABLE_QTY, sel.get("qty"))
        target_qtys = [q for q in t.get("qtys", []) if q in avail_qtys]
        if not target_qtys:
            ctx.log.event("extract.warn", product=t["product_name"],
                          error="no matching qty")
            return

        for paper in t.get("papers", []):
            if not js_set_select(page, sel.get("paper_code"), paper["paper_code"]):
                ctx.log.event("extract.warn", product=t["product_name"],
                              error=f"paper 셋팅 실패: {paper.get('paper_code')}")
                continue
            page.wait_for_timeout(timeouts.get("after_select_ms", 600))
            # paper 변경 시 size 가 reset 될 수 있음 — 재셋팅
            if t.get("size_value"):
                js_set_select(page, sel.get("paper_size"), t["size_value"])
                page.wait_for_timeout(300)

            for coating in t.get("coatings", []):
                # coating 라디오 클릭
                if not js_click_radio(page, coating["radio_name"], coating["value"]):
                    ctx.log.event("extract.warn", product=t["product_name"],
                                  error=f"coating 라디오 실패: {coating.get('name')}")
                    continue
                page.wait_for_timeout(timeouts.get("after_select_ms", 600))

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

                        yield build_item(page, t, paper, price, sel, has_coating=True)
