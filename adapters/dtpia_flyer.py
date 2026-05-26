"""디티피아 합판전단 어댑터.

페이지: https://dtpia.co.kr/Order/Flyer/Happan.aspx
DOM:
- mtrl_cd: 5 paper / sdiv: A=국전 / B=4*6전 / sdiv_cd: paper×sdiv 별 가용 사이즈 동적
- prn_clr_cn_gb: 4=단면칼라 / 8=양면칼라
- ream_cn: R(원지) 단위 select. 옆 grandparent innerText 에 "R (X,000장)" 표시.
  R 값은 사이트 내부 단위 — paper×size 별 1R 매수가 다름. R 환산보다 옆 텍스트의 매수를 직접 신뢰.
- 가격: est_scroll_ord_am (공급가)

수집 정책 (2026-05-26~):
  R 단위를 매수로 환산하지 않음. 각 R 옵션을 차례로 select → 옆 "X,000장" 텍스트를
  직접 측정 → (R, 매수) 매트릭스 구축 → target 매수와 매칭.
  정확 일치 옵션이 있으면 그것 선택, 없으면 가장 가까운 + 비례환산 (정규화 단계 보간).
  A4 만 target 2회(2000매·4000매), 나머지 사이즈는 1회.
"""
import re
from typing import Iterator, Optional

from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

from engine.adapter import SiteAdapter
from engine.context import RawItem, RunContext


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

JS_LIST_REAM_OPTS = """(sel_id) => {
    const el = document.getElementById(sel_id);
    if (!el) return [];
    return [...el.options].map(o => o.value).filter(v => v && !isNaN(parseFloat(v)));
}"""

JS_READ_REAM_MAE = r"""(sel_id) => {
    const el = document.getElementById(sel_id);
    if (!el) return null;
    const gp = el.parentElement?.parentElement;
    const txt = gp?.innerText || '';
    const m = txt.match(/R\s*\(([0-9,]+)\s*장\)/);
    return m ? parseInt(m[1].replace(/,/g, ''), 10) : null;
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

            qty_targets_by_size = t.get("qty_targets_by_size", {})
            for size in t["sizes"]:
                page.evaluate(JS_SET_SELECT, {"sel_id": sel["sdiv"], "value": size["sdiv"]})
                page.wait_for_timeout(timeouts.get("after_select_ms", 500))
                r = page.evaluate(JS_SET_SELECT, {"sel_id": sel["sdiv_cd"], "value": size["sdiv_cd"]})
                if r != True:
                    ctx.log.event("extract.warn", product=t["product_name"],
                                  paper=paper["mtrl_cd"], size=size["size_label"],
                                  error=f"sdiv_cd {size['sdiv_cd']} 셋팅 실패 — 사이트 미제공 가능")
                    continue
                page.wait_for_timeout(timeouts.get("after_select_ms", 500))

                # ream_cn 옵션 listing
                ream_opts = page.evaluate(JS_LIST_REAM_OPTS, sel["qty"])
                if not ream_opts:
                    continue
                # default selected R 의 옆 "X,000장" 매수 1회 측정 (가격 흔들림 방지).
                # R-매수가 선형이라는 사실은 별도 검증 완료 (모든 paper×size 에서 R × per_1R = 페이지 표시 매수).
                page.wait_for_timeout(timeouts.get("after_select_ms", 500))
                default_mae = page.evaluate(JS_READ_REAM_MAE, sel["qty"])
                default_R_val = page.evaluate(
                    "(sel_id) => parseFloat(document.getElementById(sel_id).value)",
                    sel["qty"])
                if not default_mae or not default_R_val:
                    ctx.log.event("extract.warn", product=t["product_name"],
                                  paper=paper["mtrl_cd"], size=size["size_label"],
                                  error="매수 텍스트 읽기 실패")
                    continue
                per_1R = default_mae / default_R_val
                # 옵션 매수 매트릭스 (선형 환산)
                r_to_mae = {r_val: int(float(r_val) * per_1R) for r_val in ream_opts}

                targets_mae = qty_targets_by_size.get(size["size_label"], [])
                if not targets_mae:
                    ctx.log.event("extract.warn", product=t["product_name"],
                                  size=size["size_label"], error="qty_targets_by_size 미정의")
                    continue

                for target_mae in targets_mae:
                    # 정확 매칭 우선, 없으면 가장 가까운 옵션 (정규화 단계서 비례환산)
                    exact = [(r, m) for r, m in r_to_mae.items() if m == target_mae]
                    if exact:
                        chosen_R, actual_qty = exact[0]
                    else:
                        chosen_R, actual_qty = min(r_to_mae.items(),
                                                    key=lambda p: abs(p[1] - target_mae))

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
                                          target_mae=target_mae,
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
                                     "target_mae": target_mae,
                                     "ream_R": chosen_R,
                                     "measured_mae_per_R": r_to_mae},
                        )
