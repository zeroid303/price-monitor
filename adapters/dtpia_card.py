"""디티피아(dtpia.co.kr) 명함 어댑터.

기존 crawlers/DtpiaCardCrawler.py 로직을 엔진 계약(SiteAdapter)으로 이관.
DOM/타임아웃 설정은 config/sites/dtpia.yaml 의 `card` 섹션을 읽어 사용.

타겟 스키마 (config/card_targets.json dtpia 섹션):
  { category, product_name, url, page_type(color|special|uv),
    size_value, qtys[], color_modes[],
    papers[ page_type별 키 + match_as?, actual_weight_g?, note? ] }

page_type:
  - color   : coating_type 단일 dropdown (paper × coating 결합)
  - special : mtrl_cd + mtrl_cdw
  - uv      : mtrl_cd + mtrl_cdw
※ 디지털명함(small_qty)은 본 어댑터 범위 제외 — 추후 오프셋/디지털 분기 때 별도 어댑터.
"""
import re
import time
from typing import Iterator

from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

from engine.adapter import SiteAdapter
from engine.context import RawItem, RunContext


JS_SET_SELECT = """({sel_id, value}) => {
    const el = document.getElementById(sel_id);
    if (!el) return 'NO_EL';
    const wantedVal = String(value);
    const opt = [...el.options].find(o => o.value === wantedVal);
    if (!opt && wantedVal !== '') return 'NO_OPT';
    el.value = wantedVal;
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return true;
}"""

JS_AVAIL_OPTIONS = """(sel_id) => {
    const el = document.getElementById(sel_id);
    if (!el) return [];
    return [...el.options].map(o => String(o.value));
}"""

JS_TRIGGER_PRICE = """() => {
    if (typeof callPrice === 'function') {
        try { callPrice(); } catch(e) {}
    }
}"""

JS_GET_PRICE = """(sel_id) => {
    const el = document.getElementById(sel_id);
    return el ? el.textContent.trim() : null;
}"""

JS_READ_DOM_STATE = """(ids) => {
    // dtpia Color.aspx 에서 일부 id(mtrl_cd 등)는 INPUT 으로 존재 — SELECT가 아니면 조기 리턴.
    const selText = (id) => {
        const el = document.getElementById(id);
        if (!el || el.tagName !== 'SELECT' || el.selectedIndex < 0) return '';
        return (el.options[el.selectedIndex]?.textContent || '').trim();
    };
    const selVal = (id) => {
        const el = document.getElementById(id);
        return el ? (el.value || '') : '';
    };
    return {
        coating_text:  selText(ids.coating_type),
        mtrl_text:     selText(ids.mtrl_cd),
        mtrl_cdw_text: selText(ids.mtrl_cdw),
        color_text:    selText(ids.color_mode),
        size_text:     selText(ids.size),
        qty_val:       selVal(ids.qty),
    };
}"""


def _parse_price(txt: str | None) -> int | None:
    if not txt:
        return None
    m = re.search(r"[\d,]+", txt.replace(" ", ""))
    if not m:
        return None
    try:
        return int(m.group().replace(",", ""))
    except ValueError:
        return None


def _read_dom_state(page, sel_ids: dict) -> dict:
    raw = page.evaluate(JS_READ_DOM_STATE, sel_ids) or {}
    try:
        qty = int(raw.get("qty_val") or "")
    except (TypeError, ValueError):
        qty = 0
    mtrl = (raw.get("mtrl_text") or "").strip()
    mtrl_cdw = (raw.get("mtrl_cdw_text") or "").strip()
    coat = (raw.get("coating_text") or "").strip()
    if mtrl:
        paper = f"{mtrl} {mtrl_cdw}".strip() if mtrl_cdw else mtrl
    else:
        paper = coat
    return {
        "paper_name": paper,
        "coating":    coat,
        "print_mode": (raw.get("color_text") or "").strip(),
        "size":       (raw.get("size_text") or "").strip(),
        "qty":        qty,
    }


class Adapter(SiteAdapter):
    site = "dtpia"
    category = "card"

    def fetch_and_extract(self, ctx: RunContext) -> Iterator[RawItem]:
        cat_cfg = ctx.site_config.get("card", {})
        sel = cat_cfg.get("selectors", {})
        timeouts = cat_cfg.get("timeouts", {})
        guard = cat_cfg.get("low_price_guard", {})
        browser_cfg = ctx.site_config.get("browser", {})

        if not ctx.targets:
            ctx.log.event("fetch.fail", level="warning", error="no targets for dtpia card")
            return

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=browser_cfg.get("headless", True))
            vp = browser_cfg.get("viewport", {"width": 1280, "height": 900})
            context = browser.new_context(
                viewport={"width": vp["width"], "height": vp["height"]},
                locale=ctx.site_config.get("locale", "ko-KR"),
            )
            for pat in ctx.site_config.get("block_patterns", []):
                context.route(pat, lambda r: r.abort())
            context.on("dialog", lambda d: d.dismiss())
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
                        ctx.log.event(
                            "fetch.fail",
                            level="error",
                            product=t.get("product_name"),
                            error=str(e),
                        )
            finally:
                browser.close()

    # ── 내부 ─────────────────────────────────────────────────────────

    def _set(self, page, sel_id: str, value) -> bool:
        try:
            return page.evaluate(JS_SET_SELECT, {"sel_id": sel_id, "value": value}) is True
        except Exception:
            return False

    def _read_price(self, page, price_sel_id: str, after_trigger_ms: int) -> int | None:
        page.evaluate(JS_TRIGGER_PRICE)
        page.wait_for_timeout(after_trigger_ms)
        txt = page.evaluate(JS_GET_PRICE, price_sel_id)
        return _parse_price(txt or "")

    def _crawl_target(
        self, ctx: RunContext, page, t: dict,
        sel: dict, timeouts: dict, guard: dict,
    ) -> Iterator[RawItem]:
        try:
            page.goto(t["url"], wait_until="domcontentloaded",
                      timeout=timeouts.get("page_goto_ms", 30000))
            page.wait_for_timeout(timeouts.get("after_goto_ms", 2500))
        except PwTimeout:
            ctx.log.event("fetch.fail", level="error",
                          product=t["product_name"], error="page goto timeout")
            return

        # size 셋팅 (ppr_cut_tmp 표준)
        if not self._set(page, sel["size"], t["size_value"]):
            ctx.log.event("extract.warn", product=t["product_name"],
                          error=f"size 셋팅 실패: {t['size_value']}")
        page.wait_for_timeout(timeouts.get("after_select_ms", 500))

        avail_qtys = page.evaluate(JS_AVAIL_OPTIONS, sel["qty"])
        target_qtys = [q for q in t["qtys"] if str(q) in avail_qtys]
        if not target_qtys:
            ctx.log.event("extract.warn", product=t["product_name"],
                          error="no matching qty")
            return

        page_type = t["page_type"]
        for paper in t["papers"]:
            # 용지 옵션 셋팅
            if page_type == "color":
                if not self._set(page, sel["coating_type"], paper["coating_select_value"]):
                    ctx.log.event("extract.warn", product=t["product_name"],
                                  error=f"coating_type={paper.get('coating_select_value')} 실패")
                    continue
                paper_loops = [paper]
            elif page_type in ("special", "uv"):
                if not self._set(page, sel["mtrl_cd"], paper["mtrl_cd"]):
                    ctx.log.event("extract.warn", product=t["product_name"],
                                  error=f"mtrl_cd={paper.get('mtrl_cd')} 실패")
                    continue
                page.wait_for_timeout(timeouts.get("after_select_ms", 500))
                if "mtrl_cdw" in paper:
                    self._set(page, sel["mtrl_cdw"], paper["mtrl_cdw"])
                    page.wait_for_timeout(timeouts.get("after_select_ms", 500))
                pcopy = dict(paper)
                pcopy["coating_out_runtime"] = "코팅없음"
                paper_loops = [pcopy]
            else:
                ctx.log.event("extract.warn", product=t["product_name"],
                              error=f"unknown page_type: {page_type}")
                continue

            for p_inst in paper_loops:
                for color in t["color_modes"]:
                    if not self._set(page, sel["color_mode"], color["value"]):
                        continue
                    page.wait_for_timeout(timeouts.get("after_color_ms", 400))

                    # paper/color 변경 시 size/qty reset 가능성 → 재셋팅·검증
                    self._set(page, sel["size"], t["size_value"])
                    page.wait_for_timeout(200)
                    actual_size = page.evaluate(
                        f"() => (document.getElementById('{sel['size']}') || {{}}).value || ''"
                    )
                    if actual_size != t["size_value"]:
                        self._set(page, sel["size"], t["size_value"])
                        page.wait_for_timeout(400)
                        actual_size = page.evaluate(
                            f"() => (document.getElementById('{sel['size']}') || {{}}).value || ''"
                        )
                        if actual_size != t["size_value"]:
                            ctx.log.event("extract.warn", product=t["product_name"],
                                          error=f"size 셋팅 실패 actual={actual_size}")
                            continue

                    for qty in target_qtys:
                        if not self._set(page, sel["qty"], str(qty)):
                            continue
                        page.wait_for_timeout(timeouts.get("after_qty_ms", 300))
                        price = self._read_price(page, sel["price"],
                                                 timeouts.get("after_price_trigger_ms", 900))
                        if price is None:
                            ctx.log.event("extract.warn", product=t["product_name"],
                                          qty=qty, error="price read failed")
                            continue
                        # 비정상 저가 방어
                        floor = max(guard.get("floor_abs", 500),
                                    qty * guard.get("per_qty_multiplier", 3))
                        if price < floor:
                            page.evaluate(JS_TRIGGER_PRICE)
                            page.wait_for_timeout(timeouts.get("retry_price_ms", 1500))
                            price = self._read_price(page, sel["price"],
                                                     timeouts.get("after_price_trigger_ms", 900))
                            if price is None or price < floor:
                                ctx.log.event("extract.warn", product=t["product_name"],
                                              qty=qty, price=price,
                                              error=f"저가 방어 실패 floor={floor}")
                                continue

                        yield self._build_item(page, t, p_inst, color["name"], price, sel)

    def _build_item(
        self, page, t: dict, paper: dict, color_name: str,
        price: int, sel: dict,
    ) -> RawItem:
        dom = _read_dom_state(page, sel)
        options = {
            "config_paper_name_out": paper.get("paper_name_out"),
            "config_coating_out":    paper.get("coating_out_runtime", paper.get("coating_out")),
            "config_color_name":     color_name,
        }
        if "actual_weight_g" in paper:
            options["actual_weight_g"] = paper["actual_weight_g"]
        if "note" in paper:
            options["note"] = paper["note"]

        return RawItem(
            product=t["product_name"],
            category=t["category"],
            paper_name=dom["paper_name"] or None,
            coating=dom["coating"] or None,
            print_mode=dom["print_mode"] or None,
            size=dom["size"] or None,
            qty=dom["qty"] or None,
            price=price,
            price_vat_included=True,
            url=t["url"],
            url_ok=True,
            options=options,
            match_as=paper.get("match_as"),
        )
