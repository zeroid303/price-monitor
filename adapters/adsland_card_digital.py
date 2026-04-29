"""애즈랜드 디지털 명함 어댑터 (card_digital).

처리 페이지 (13): 디지털 일반/고급/백색인쇄 색지/한지/펄지/두꺼운/모양커팅 5종/소량/에코.

Vue.js 2.x SPA: paperSort[] → paper[] → pweight[] 3단계 *런타임 동적 enumerate*.
paper 변경 시 pweight 옵션이 동적 갱신되므로 yaml 카테시안이 아닌 런타임에 옵션 읽기.

target schema (단순화):
  - product_name, category, url
  - size_value (보통 '90x50')
  - paper_sorts[ value ]   # paperSort 옵션 list (sweep 대상)
  - back_dosus[ {value, name} ]
  - busu_targets[]
  - coats[ {value, name} ]
  - sc_blade ({"sweep": [...]} 모양커팅, {"fix": ""} 소량, 또는 null)
"""
from typing import Iterator

from playwright.sync_api import sync_playwright

from adapters._adsland_card_common import (
    JS_GET_DIGITAL_DOSU_TEXT,
    JS_GET_SELECT_VALUE,
    JS_SET_DIGITAL_BACK_DOSU,
    goto_with_wait,
    init_browser,
    js_get_select_options,
    js_get_select_text,
    js_set_select,
    price_with_retry,
    trigger_smart,
)
from engine.adapter import SiteAdapter
from engine.context import RawItem, RunContext


def _read_dom_digital(page, sel: dict) -> dict:
    """DOM 표시값 그대로 추출. paper_name = paperSort + paper + pweight 결합 표시값."""
    sort = js_get_select_text(page, sel.get("paperSort"))
    pname = js_get_select_text(page, sel.get("paper"))
    pw = js_get_select_text(page, sel.get("pweight"))
    paper_name = " ".join(x.strip() for x in (sort, pname, pw) if x and x.strip()) or None

    size = js_get_select_text(page, sel.get("size")) or None
    coat = js_get_select_text(page, sel.get("coat")) or None

    # 익명 도수 select 2개 (앞면/뒷면) DOM 표시값
    try:
        dosu = page.evaluate(JS_GET_DIGITAL_DOSU_TEXT) or {}
    except Exception:
        dosu = {}
    front, back = (dosu.get("front") or "").strip(), (dosu.get("back") or "").strip()
    if back and "인쇄없음" in back:
        print_mode = f"단면 / {front}".strip(" /") if front else "단면"
    elif front or back:
        parts = [x for x in (front, back) if x]
        print_mode = " / ".join(parts) if parts else None
    else:
        print_mode = None

    qty_val = page.evaluate(JS_GET_SELECT_VALUE, sel.get("busuSelect"))
    try:
        qty = int(qty_val) if qty_val else None
    except (TypeError, ValueError):
        qty = None

    return {
        "paper_name": paper_name,
        "paper_weight_text": pw.strip() if pw else None,  # 평량 leaf (예: '300 g/㎡')
        "coating": coat,
        "print_mode": print_mode,
        "size": size,
        "qty": qty,
    }


def _build_item_digital(page, t: dict, price: int, sel: dict) -> RawItem:
    dom = _read_dom_digital(page, sel)
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
    category = "card_digital"

    def fetch_and_extract(self, ctx: RunContext) -> Iterator[RawItem]:
        cat_cfg = ctx.site_config.get("card_digital", {})
        sel = cat_cfg.get("selectors", {})
        timeouts = cat_cfg.get("timeouts", {})
        guard = cat_cfg.get("low_price_guard", {})

        if not ctx.targets:
            ctx.log.event("fetch.fail", level="warning",
                          error="no targets for adsland card_digital")
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
        if t.get("size_value") is not None:
            if not js_set_select(page, sel.get("size"), t["size_value"]):
                ctx.log.event("extract.warn", product=t["product_name"],
                              error=f"size 셋팅 실패: {t['size_value']}")
                return
            page.wait_for_timeout(timeouts.get("after_select_ms", 600))

        # kind=1 고정
        if not js_set_select(page, sel.get("kind"), "1"):
            ctx.log.event("extract.warn", product=t["product_name"],
                          error="kind=1 셋팅 실패")
            return
        page.wait_for_timeout(timeouts.get("after_select_ms", 600))

        # scBlade 처리: 모양커팅 페이지는 sweep, 그 외는 '' 고정 또는 skip
        sc = t.get("sc_blade")  # dict or None
        sc_iter = []
        if sc and sc.get("sweep"):
            sc_iter = sc["sweep"]
        elif sc and "fix" in sc:
            js_set_select(page, sel.get("scBlade"), sc["fix"])
            page.wait_for_timeout(timeouts.get("after_select_ms", 600))
            sc_iter = [None]
        else:
            sc_iter = [None]

        # paperSort → paper → pweight 런타임 동적 enumerate
        for ps_value in t.get("paper_sorts", []):
            if not js_set_select(page, sel.get("paperSort"), ps_value):
                ctx.log.event("extract.warn", product=t["product_name"],
                              paperSort=ps_value, error="paperSort 셋팅 실패")
                continue
            page.wait_for_timeout(timeouts.get("after_paper_chain_ms", 800))

            paper_opts = js_get_select_options(page, sel.get("paper"))
            for p_opt in paper_opts:
                p_val = p_opt.get("value")
                if not p_val:
                    continue
                if not js_set_select(page, sel.get("paper"), p_val):
                    continue
                page.wait_for_timeout(timeouts.get("after_paper_chain_ms", 800))

                pw_opts = js_get_select_options(page, sel.get("pweight"))
                for pw_opt in pw_opts:
                    pw_val = pw_opt.get("value")
                    if not pw_val:
                        continue
                    if not js_set_select(page, sel.get("pweight"), pw_val):
                        continue
                    page.wait_for_timeout(timeouts.get("after_paper_chain_ms", 800))

                    yield from self._sweep_remaining(
                        ctx, page, t, sel, timeouts, guard, sc_iter,
                    )

    def _sweep_remaining(
        self, ctx: RunContext, page, t: dict,
        sel: dict, timeouts: dict, guard: dict, sc_iter: list,
    ) -> Iterator[RawItem]:
        for sc_choice in sc_iter:
            if sc_choice is not None:
                if not js_set_select(page, sel.get("scBlade"), sc_choice["value"]):
                    continue
                page.wait_for_timeout(timeouts.get("after_select_ms", 600))

            for back in t.get("back_dosus", []):
                try:
                    ok = page.evaluate(JS_SET_DIGITAL_BACK_DOSU, back["value"])
                    if ok is not True:
                        continue
                except Exception:
                    continue
                page.wait_for_timeout(timeouts.get("after_select_ms", 600))

                for coat in t.get("coats", []):
                    if not js_set_select(page, sel.get("coat"), coat["value"]):
                        continue
                    page.wait_for_timeout(timeouts.get("after_select_ms", 600))

                    for qty in t.get("busu_targets", []):
                        if not js_set_select(page, sel.get("busuSelect"), str(qty)):
                            continue
                        trigger_smart(page)
                        price = price_with_retry(page, sel, qty, timeouts, guard)
                        if price is None:
                            ctx.log.event("extract.warn", product=t["product_name"],
                                          qty=qty, error="price read failed")
                            continue
                        yield _build_item_digital(page, t, price, sel)
