"""디티피아 합판전단 어댑터.

페이지: https://dtpia.co.kr/Order/Flyer/Happan.aspx
DOM:
- mtrl_cd: 5 paper / sdiv: A=국전 / B=4*6전 / sdiv_cd: paper×sdiv 별 가용 사이즈 동적
- prn_clr_cn_gb: 4=단면칼라 / 8=양면칼라
- ream_cn: R(원지) 단위 — paper×size 별 1R 매수 다름. ream_cn select grandparent
  innerText 에 "R (X,000장)" 표기 → 1R 매수 추출.
- 가격: est_scroll_ord_am (공급가)

수집 정책: 표준 매수(2000)에 가장 가까운 R 옵션 동적 선택. raw 에 실측 매수+가격.
"""
import re
from typing import Iterator, Optional

from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

from engine.adapter import SiteAdapter
from engine.context import RawItem, RunContext


TARGET_QTY_MAE = 2000  # 표준 매수


JS_SET_SELECT = """({sel_id, value}) => {
    const el = document.getElementById(sel_id);
    if (!el) return 'NO_EL';
    const opt = [...el.options].find(o => o.value === String(value));
    if (!opt) return 'NO_OPT';
    el.value = String(value);
    const oc = el.getAttribute('onchange');
    if (oc) { try { eval(oc); } catch(e) {} }
    el.dispatchEvent(new Event('change', {bubbles: true}));
    if (window.jQuery) { try { window.jQuery(el).trigger('change'); } catch(e) {} }
    return true;
}"""

JS_GET_REAM_INFO = r"""(sel_id) => {
    const el = document.getElementById(sel_id);
    if (!el) return null;
    const opts = [...el.options].map(o => parseFloat(o.value)).filter(v => !isNaN(v));
    const gp = el.parentElement?.parentElement;
    let per_ream = null;
    if (gp) {
        const m = (gp.innerText || '').match(/R\s*\(([0-9,]+)\s*장\)/);
        if (m) per_ream = parseInt(m[1].replace(/,/g, ''), 10);
    }
    return {opts: opts, per_ream: per_ream};
}"""

JS_GET_PRICE = """(sel_id) => {
    const el = document.getElementById(sel_id);
    return el ? el.textContent.trim() : null;
}"""


def _parse_price(txt) -> Optional[int]:
    if not txt: return None
    m = re.search(r"[\d,]+", txt)
    if not m: return None
    try:
        return int(m.group().replace(",", ""))
    except ValueError:
        return None


class Adapter(SiteAdapter):
    site = "dtpia"
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
            browser_cfg = ctx.site_config.get("browser", {})
            browser = pw.chromium.launch(headless=browser_cfg.get("headless", True))
            context = browser.new_context(
                viewport=browser_cfg.get("viewport", {"width": 1280, "height": 900}),
                locale=ctx.site_config.get("locale", "ko-KR"),
            )
            for pat in ctx.site_config.get("block_patterns", []):
                context.route(pat, lambda r: r.abort())
            page = context.new_page()
            try:
                for t in ctx.targets:
                    ctx.log.event("fetch.start", product=t.get("product_name"))
                    yield from self._crawl(ctx, page, t, sel, timeouts, guard)
            finally:
                browser.close()

    def _crawl(self, ctx, page, t, sel, timeouts, guard) -> Iterator[RawItem]:
        try:
            page.goto(t["url"], wait_until="domcontentloaded",
                      timeout=timeouts.get("page_goto_ms", 30000))
            page.wait_for_timeout(timeouts.get("after_goto_ms", 2500))
        except PwTimeout:
            ctx.log.event("fetch.fail", level="error", product=t["product_name"], error="goto timeout")
            return

        for paper in t["papers"]:
            r = page.evaluate(JS_SET_SELECT, {"sel_id": sel["mtrl_cd"], "value": paper["mtrl_cd"]})
            if r is not True:
                ctx.log.event("extract.warn", product=t["product_name"],
                              error=f"paper {paper['mtrl_cd']} 셋팅 실패")
                continue
            page.wait_for_timeout(timeouts.get("after_select_ms", 500))

            for size in t["sizes"]:
                page.evaluate(JS_SET_SELECT, {"sel_id": sel["sdiv"], "value": size["sdiv"]})
                page.wait_for_timeout(timeouts.get("after_select_ms", 500))
                r = page.evaluate(JS_SET_SELECT, {"sel_id": sel["sdiv_cd"], "value": size["sdiv_cd"]})
                if r != True:
                    continue
                page.wait_for_timeout(timeouts.get("after_select_ms", 500))

                # ream_cn 옵션 + 1R 매수 (페이지표기) 추출
                info = page.evaluate(JS_GET_REAM_INFO, sel["qty"])
                if not info or not info.get("opts"):
                    continue
                per_ream = info.get("per_ream")
                opts = info["opts"]
                # 표준 매수에 가장 가까운 R 선택. per_ream 모르면 첫 옵션.
                if per_ream:
                    chosen_R = min(opts, key=lambda o: abs(o * per_ream - TARGET_QTY_MAE))
                    actual_qty = int(chosen_R * per_ream)
                else:
                    chosen_R = opts[0]
                    actual_qty = None

                # ream_cn 셋팅
                page.evaluate(JS_SET_SELECT, {"sel_id": sel["qty"], "value": str(chosen_R)})
                page.wait_for_timeout(timeouts.get("after_qty_ms", 400))

                for cm in t["color_modes"]:
                    page.evaluate(JS_SET_SELECT, {"sel_id": sel["color_mode"], "value": cm["value"]})
                    page.wait_for_timeout(timeouts.get("after_price_trigger_ms", 900))

                    price_txt = page.evaluate(JS_GET_PRICE, sel["price"])
                    price = _parse_price(price_txt)
                    if price is None:
                        page.wait_for_timeout(timeouts.get("retry_price_ms", 1500))
                        price_txt = page.evaluate(JS_GET_PRICE, sel["price"])
                        price = _parse_price(price_txt)
                    if price is None:
                        ctx.log.event("extract.warn", product=t["product_name"],
                                      paper=paper["mtrl_cd"], size=size["size_label"],
                                      color=cm["name"], error="price read failed")
                        continue

                    yield RawItem(
                        product=t["product_name"],
                        category=t["category"],
                        paper_name=paper["paper_name_out"],
                        coating=None,
                        print_mode=cm["name"],
                        size=size["size_label"],
                        qty=actual_qty,
                        price=price,
                        price_vat_included=False,
                        url=t["url"],
                        url_ok=True,
                        options={"mtrl_cd": paper["mtrl_cd"],
                                 "sdiv": size["sdiv"], "sdiv_cd": size["sdiv_cd"],
                                 "color_value": cm["value"],
                                 "ream_R": chosen_R,
                                 "per_ream_mae": per_ream},
                    )
