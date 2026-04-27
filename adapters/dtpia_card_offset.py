"""디티피아(dtpia.co.kr) 오프셋 명함 어댑터 (card_offset).

처리 페이지 (8):
  - 일반명함     (Color.aspx)              type=A page_fixed
  - 고급지명함   (Special.aspx?code=2)     type=B mtrl_cd_pair
  - 펄지명함     (Special.aspx?code=3)     type=B
  - 무늬지명함   (Special.aspx?code=4)     type=B
  - UV옵셋명함   (Uv.aspx)                 type=B
  - 두꺼운명함   (Extra.aspx)              type=B
  - PP카드명함   (Pp.aspx)                 type=C mtrl_cd_only (사이즈 86×54 고정)
  - 피아노블랙박 (PianoBlack.aspx)         type=D mtrl_split

target 의 page_type 에 따라 분기. 공통 로직은 _dtpia_card_common 사용.
"""
from typing import Iterator

from playwright.sync_api import sync_playwright

from adapters._dtpia_card_common import (
    JS_AVAIL_OPTIONS, init_browser, goto_with_wait, js_set,
    set_paper_type_a, set_paper_type_b, set_paper_type_c, set_paper_type_d,
    yield_items_for_paper,
)
from engine.adapter import SiteAdapter
from engine.context import RawItem, RunContext


_TYPE_HANDLERS = {
    "page_fixed":    set_paper_type_a,
    "mtrl_cd_pair":  set_paper_type_b,
    "mtrl_cd_only":  set_paper_type_c,
    "mtrl_split":    set_paper_type_d,
}


class Adapter(SiteAdapter):
    site = "dtpia"
    category = "card_offset"

    def fetch_and_extract(self, ctx: RunContext) -> Iterator[RawItem]:
        cat_cfg = ctx.site_config.get("card_offset", {})
        sel = cat_cfg.get("selectors", {})
        timeouts = cat_cfg.get("timeouts", {})
        guard = cat_cfg.get("low_price_guard", {})

        if not ctx.targets:
            ctx.log.event("fetch.fail", level="warning",
                          error="no targets for dtpia card_offset")
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
        page_type = t["page_type"]
        if page_type not in _TYPE_HANDLERS:
            ctx.log.event("extract.warn", product=t["product_name"],
                          error=f"unknown page_type: {page_type}")
            return

        if not goto_with_wait(page, t["url"], timeouts, ctx, t["product_name"]):
            return

        # size 셋팅 (PP카드 type-C 는 size select 가 없으므로 skip)
        if page_type != "mtrl_cd_only" and t.get("size_value"):
            if not js_set(page, sel.get("size", "ppr_cut_tmp"), t["size_value"]):
                ctx.log.event("extract.warn", product=t["product_name"],
                              error=f"size 셋팅 실패: {t['size_value']}")
            page.wait_for_timeout(timeouts.get("after_select_ms", 500))

        # 가용 qty 교집합
        avail_qtys = page.evaluate(JS_AVAIL_OPTIONS, sel.get("qty", "prn_sht_cn"))
        target_qtys = [q for q in t.get("qtys", []) if str(q) in avail_qtys]
        if not target_qtys:
            ctx.log.event("extract.warn", product=t["product_name"],
                          error="no matching qty")
            return

        handler = _TYPE_HANDLERS[page_type]
        for paper in t.get("papers", []):
            if not handler(page, sel, paper, timeouts):
                ctx.log.event("extract.warn", product=t["product_name"],
                              error=f"paper 셋팅 실패: {paper.get('paper_name_out')}")
                continue
            yield from yield_items_for_paper(
                ctx, page, t, paper, page_type, sel, timeouts, guard, target_qtys,
            )
