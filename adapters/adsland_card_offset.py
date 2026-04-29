"""애즈랜드 오프셋 명함 어댑터 (card_offset).

처리 페이지 (4):
  - 스페셜 특가 명함     (IC00175)
  - 일반지 명함(포카)    (IC00001)
  - 친환경수입지명함     (IC00002)
  - 567별색명함          (IC00091)

랜덤명함(IC00006) / 퓨어 컬러 박 명함(IC00197) 은 후가공/특수 카테고리로 별도 처리 (skip).

target schema:
  - product_name, category, url, page_type
  - size_value (페이지 가용 size_book value, 보통 '90×50')
  - qtys[]   (페이지 busu ∩ 표준 [100, 200, 500, 1000])
  - papers[ {paper_value, paper_name_out (선택)} ]   ← paper select value
  - dosu/spot_color 분기:
    - page_type='size_paper_dosu': dosus[ {value, name} ]   # dosu select 값
    - page_type='size_paper_spot': dosu_outs[ {value, name} ]  # dosu_cover_out 값. dosu_cover_in='0' 고정 (단면).
"""
from typing import Iterator

from playwright.sync_api import sync_playwright

from adapters._adsland_card_common import (
    JS_GET_SELECT_VALUE,
    goto_with_wait,
    init_browser,
    js_get_select_text,
    js_set_select,
    price_with_retry,
    trigger_smart,
)
from engine.adapter import SiteAdapter
from engine.context import RawItem, RunContext


def _read_dom_offset(page, sel: dict, page_type: str) -> dict:
    """DOM 표시값으로 raw 필드 추출."""
    paper_name = js_get_select_text(page, sel.get("paper")) or None
    size = js_get_select_text(page, sel.get("size")) or None
    if page_type == "size_paper_spot":
        out = js_get_select_text(page, sel.get("dosu_cover_out")) or ""
        inn = js_get_select_text(page, sel.get("dosu_cover_in")) or ""
        # 단면 (dosu_cover_in='인쇄없음') 이면 앞면 옵션만 표기, 아니면 합산
        if not inn or "인쇄없음" in inn:
            print_mode = f"단면 / {out}".strip(" /")
        else:
            print_mode = f"앞면 {out} / 뒷면 {inn}".strip()
    else:
        print_mode = js_get_select_text(page, sel.get("dosu")) or None
    qty_val = page.evaluate(JS_GET_SELECT_VALUE, sel.get("busu"))
    try:
        qty = int(qty_val) if qty_val else None
    except (TypeError, ValueError):
        qty = None
    return {
        "paper_name": paper_name,
        "paper_weight_text": None,  # adsland offset 은 paper select text 가 평량 통합
        "coating": None,
        "print_mode": print_mode,
        "size": size,
        "qty": qty,
    }


def _build_item_offset(page, t: dict, price: int, sel: dict, page_type: str) -> RawItem:
    dom = _read_dom_offset(page, sel, page_type)
    return RawItem(
        product=t["product_name"],
        category=t.get("category", t["product_name"]),
        paper_name=dom["paper_name"],
        paper_weight_text=dom["paper_weight_text"],
        coating=dom["coating"],
        print_mode=dom["print_mode"],
        size=dom["size"],
        qty=dom["qty"],
        price=price,
        price_vat_included=False,
        url=t["url"],
        url_ok=True,
        options={},
    )


class Adapter(SiteAdapter):
    site = "adsland"
    category = "card_offset"

    def fetch_and_extract(self, ctx: RunContext) -> Iterator[RawItem]:
        cat_cfg = ctx.site_config.get("card_offset", {})
        sel = cat_cfg.get("selectors", {})
        timeouts = cat_cfg.get("timeouts", {})
        guard = cat_cfg.get("low_price_guard", {})

        if not ctx.targets:
            ctx.log.event("fetch.fail", level="warning",
                          error="no targets for adsland card_offset")
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
        page_type = t.get("page_type", "size_paper_dosu")

        # size 셋팅
        if t.get("size_value") is not None:
            if not js_set_select(page, sel.get("size"), t["size_value"]):
                ctx.log.event("extract.warn", product=t["product_name"],
                              error=f"size 셋팅 실패: {t['size_value']}")
            page.wait_for_timeout(timeouts.get("after_select_ms", 500))

        # kind=1 고정
        if not js_set_select(page, sel.get("kind"), "1"):
            ctx.log.event("extract.warn", product=t["product_name"],
                          error="kind=1 셋팅 실패")
            return
        page.wait_for_timeout(timeouts.get("after_select_ms", 500))

        # 567별색: 단면 고정 (dosu_cover_in='0' = 인쇄없음)
        if page_type == "size_paper_spot":
            if not js_set_select(page, sel.get("dosu_cover_in"), "0"):
                ctx.log.event("extract.warn", product=t["product_name"],
                              error="dosu_cover_in='0' 셋팅 실패")
                return
            page.wait_for_timeout(timeouts.get("after_select_ms", 500))

        # paper sweep
        for paper in t.get("papers", []):
            if not paper.get("paper_value"):
                continue  # separator option (value="") skip
            if not js_set_select(page, sel.get("paper"), paper["paper_value"]):
                ctx.log.event("extract.warn", product=t["product_name"],
                              paper=paper.get("paper_value"),
                              error="paper 셋팅 실패")
                continue
            page.wait_for_timeout(timeouts.get("after_select_ms", 500))

            # 도수 sweep
            if page_type == "size_paper_spot":
                dosu_list = t.get("dosu_outs", [])
                dosu_sel  = sel.get("dosu_cover_out")
            else:
                dosu_list = t.get("dosus", [])
                dosu_sel  = sel.get("dosu")

            for ds in dosu_list:
                if not js_set_select(page, dosu_sel, ds["value"]):
                    continue
                page.wait_for_timeout(timeouts.get("after_select_ms", 500))

                # busu sweep
                for qty in t.get("qtys", []):
                    if not js_set_select(page, sel.get("busu"), str(qty)):
                        continue
                    trigger_smart(page)
                    price = price_with_retry(page, sel, qty, timeouts, guard)
                    if price is None:
                        ctx.log.event("extract.warn", product=t["product_name"],
                                      paper=paper.get("paper_value"),
                                      qty=qty, error="price read failed")
                        continue
                    yield _build_item_offset(page, t, price, sel, page_type)
