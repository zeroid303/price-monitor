"""디티피아(dtpia.co.kr) 디지털 명함 어댑터 (card_digital).

처리 페이지 (2):
  - 소량명함     (SmallQuantity.aspx)  type=D mtrl_split, sel_a=mtrl_cd_01, sel_b=mtrl_cd_02
  - 인디고명함   (Indigo.aspx)         type=D mtrl_split, sel_a=mtrl_01,    sel_b=mtrl_02

target 구조 type-D:
  papers:
    - sel_a: 'mtrl_cd_01'    # 사용 select id
      sel_b: 'mtrl_cd_02'
      paper_value: 'RSW'     # paper select 의 value
      weight_value: '250'    # weight select 의 value
      paper_name_out: '...'  # 추적용
"""
from typing import Iterator

from playwright.sync_api import sync_playwright

from adapters._dtpia_card_common import (
    JS_AVAIL_OPTIONS, init_browser, goto_with_wait, js_set,
    set_paper_type_d, yield_items_for_paper,
)
from engine.adapter import SiteAdapter
from engine.context import RawItem, RunContext


class Adapter(SiteAdapter):
    site = "dtpia"
    category = "card_digital"

    def fetch_and_extract(self, ctx: RunContext) -> Iterator[RawItem]:
        cat_cfg = ctx.site_config.get("card_digital", {})
        sel = cat_cfg.get("selectors", {})
        timeouts = cat_cfg.get("timeouts", {})
        guard = cat_cfg.get("low_price_guard", {})

        if not ctx.targets:
            ctx.log.event("fetch.fail", level="warning",
                          error="no targets for dtpia card_digital")
            return

        with sync_playwright() as pw:
            browser, context = init_browser(pw, ctx)
            page = context.new_page()
            try:
                for i, t in enumerate(ctx.targets, 1):
                    ctx.log.event(
                        "fetch.start",
                        product=t.get("product_name"),
                        page_type=t.get("page_type"),
                        index=i,
                        total=len(ctx.targets),
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
        if t.get("page_type") != "mtrl_split":
            ctx.log.event("extract.warn", product=t["product_name"],
                          error=f"unsupported page_type: {t.get('page_type')}")
            return

        if not goto_with_wait(page, t["url"], timeouts, ctx, t["product_name"]):
            return

        # size 셋팅
        if t.get("size_value"):
            if not js_set(page, sel.get("size", "ppr_cut_tmp"), t["size_value"]):
                ctx.log.event("extract.warn", product=t["product_name"],
                              error=f"size 셋팅 실패: {t['size_value']}")
            page.wait_for_timeout(timeouts.get("after_select_ms", 500))

        # coating 셋팅 (디지털명함은 coating select 있는 페이지=소량명함만, 인디고는 coating 없음)
        coating_val = t.get("coating_select_value")
        if coating_val is not None and sel.get("coating_type"):
            if not js_set(page, sel["coating_type"], coating_val):
                ctx.log.event("extract.warn", product=t["product_name"],
                              error=f"coating 셋팅 실패: {coating_val}")
            page.wait_for_timeout(timeouts.get("after_select_ms", 500))

        # 가용 qty
        avail_qtys = page.evaluate(JS_AVAIL_OPTIONS, sel.get("qty", "prn_sht_cn"))
        target_qtys = [q for q in t.get("qtys", []) if str(q) in avail_qtys]
        if not target_qtys:
            ctx.log.event("extract.warn", product=t["product_name"],
                          error="no matching qty")
            return

        for paper in t.get("papers", []):
            if not set_paper_type_d(page, sel, paper, timeouts):
                ctx.log.event("extract.warn", product=t["product_name"],
                              error=f"paper 셋팅 실패: {paper.get('paper_name_out')}")
                continue
            yield from yield_items_for_paper(
                ctx, page, t, paper, "mtrl_split", sel, timeouts, guard, target_qtys,
            )
