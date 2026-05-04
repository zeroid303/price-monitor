"""와우프레스(wowpress) 명함 어댑터 공통 헬퍼.

페이지 메커니즘:
- size/color_mode select (pdata_00_*) 는 단일 dropdown
- paper 는 paperno3 → paperno4 (간혹 5) 단계 dropdown.
  paperList JSON 으로 leaf paper_no 의 부모 체인을 추출 →
  paperno3 dropdown 에 매칭되는 ancestor 를 셋팅 → paperno4 셋팅.
- 가격은 od_00_totalcost (VAT 포함) - od_00_taxcost (부가세) = 공급가

raw 원칙:
- paper_name: paperno3 + paperno4 (+ paperno5) selected text 결합 (DOM 표시값)
- size:       pdata_00_sizeno selected text
- print_mode: pdata_00_colorno selected text
- coating:    별도 select 없음 — paper_name 괄호 속 (무광코팅) 등은 schema 정규화에서 추출
- qty:        spdata_00_ordqty value (숫자)
- price:      totalcost - taxcost (공급가)
"""
import re
from typing import Optional

from playwright.sync_api import TimeoutError as PwTimeout

from engine.context import RawItem, RunContext


# ── JS 헬퍼 ──

JS_SET_SELECT = """({sel_id, value}) => {
    const el = document.getElementById(sel_id);
    if (!el) return 'NO_EL';
    const wantedVal = String(value);
    const opt = [...el.options].find(o => o.value === wantedVal);
    if (!opt) return 'NO_OPT';
    el.value = wantedVal;
    // wowpress 는 onchange attribute 안에서 가격 갱신 함수 호출
    const oc = el.getAttribute('onchange');
    if (oc) { try { eval(oc); } catch(e) {} }
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return true;
}"""

JS_AVAIL_OPTIONS = """(sel_id) => {
    const el = document.getElementById(sel_id);
    if (!el) return [];
    return [...el.options].map(o => String(o.value)).filter(v => v);
}"""

JS_GET_SELECT_TEXT = """(sel_id) => {
    const el = document.getElementById(sel_id);
    if (!el || el.selectedIndex < 0) return '';
    return (el.options[el.selectedIndex]?.textContent || '').trim();
}"""

JS_GET_SELECT_VALUE = """(sel_id) => {
    const el = document.getElementById(sel_id);
    return el ? (el.value || '') : '';
}"""

# leaf paper_no 의 부모 체인 (자기 자신 → root). PaperPNo === 0 또는 매핑 끊기면 종료.
JS_PAPER_PARENT_CHAIN = """({list_id, paperNo}) => {
    const pl = document.getElementById(list_id);
    if (!pl) return null;
    let list;
    try { list = JSON.parse(pl.value); } catch(e) { return null; }
    const map = {};
    for (const p of list) map[p.PaperNo] = p;
    const chain = [];
    let cur = map[paperNo];
    while (cur && cur.PaperNo && cur.PaperNo !== 0) {
        chain.push(cur.PaperNo);
        if (!cur.PaperPNo || cur.PaperPNo === 0) break;
        cur = map[cur.PaperPNo];
    }
    return chain;
}"""

JS_GET_PRICE_PAIR = """({total_id, tax_id}) => {
    const t = document.getElementById(total_id);
    const x = document.getElementById(tax_id);
    return {
        total: t ? t.textContent.trim() : null,
        tax:   x ? x.textContent.trim() : null,
    };
}"""


# ── 가격 파싱 ──

def parse_int_price(txt: Optional[str]) -> Optional[int]:
    if not txt:
        return None
    m = re.search(r"[\d,]+", txt)
    if not m:
        return None
    try:
        return int(m.group().replace(",", ""))
    except ValueError:
        return None


# ── 셋 유틸 ──

def js_set_select(page, sel_id: str, value) -> bool:
    try:
        return page.evaluate(JS_SET_SELECT, {"sel_id": sel_id, "value": value}) is True
    except Exception:
        return False


def js_get_select_text(page, sel_id: str) -> str:
    try:
        return page.evaluate(JS_GET_SELECT_TEXT, sel_id) or ""
    except Exception:
        return ""


def select_paper(page, sel: dict, paper_no: int) -> bool:
    """paper leaf no 로부터 paperno3 / paperno4 (필요시 paperno5) 까지 셋팅."""
    chain = page.evaluate(
        JS_PAPER_PARENT_CHAIN,
        {"list_id": sel.get("paper_list", "paperList"), "paperNo": paper_no},
    ) or []
    p3_id = sel.get("paper_no3", "spdata_00_paperno3")
    p3_opts = set(page.evaluate(JS_AVAIL_OPTIONS, p3_id) or [])
    if not chain or not p3_opts:
        return False
    # paperno3 = chain 안에서 dropdown 에 있는 첫 ancestor (leaf → root 순)
    p3_idx = next((i for i, n in enumerate(chain) if str(n) in p3_opts), None)
    if p3_idx is None:
        return False
    if not js_set_select(page, p3_id, str(chain[p3_idx])):
        return False
    page.wait_for_timeout(800)
    # paperno4 후보 = paperno3 보다 leaf 쪽 노드들. dropdown 에 있는 첫 매칭 사용.
    p4_id = sel.get("paper_no4", "spdata_00_paperno4")
    p4_opts = set(page.evaluate(JS_AVAIL_OPTIONS, p4_id) or [])
    p4_candidates = [str(n) for n in chain[:p3_idx]]
    p4_value = next((c for c in p4_candidates if c in p4_opts), None)
    if p4_value is None:
        # dropdown 비어있거나 매칭 안되면 leaf 그대로 (마지막 fallback)
        p4_value = str(paper_no)
    if not js_set_select(page, p4_id, p4_value):
        return False
    page.wait_for_timeout(800)
    return True


def read_supply_price(page, sel: dict) -> Optional[int]:
    """공급가 = totalcost - taxcost. 두 값 다 있어야 정상 가격."""
    pair = page.evaluate(
        JS_GET_PRICE_PAIR,
        {"total_id": sel.get("price_total", "od_00_totalcost"),
         "tax_id":   sel.get("price_tax",   "od_00_taxcost")},
    ) or {}
    total = parse_int_price(pair.get("total"))
    tax = parse_int_price(pair.get("tax"))
    if total is None:
        return None
    if tax is None:
        return None
    return total - tax


def read_dom_state(page, sel: dict) -> dict:
    p3 = js_get_select_text(page, sel.get("paper_no3", "spdata_00_paperno3"))
    p4 = js_get_select_text(page, sel.get("paper_no4", "spdata_00_paperno4"))
    p5 = js_get_select_text(page, sel.get("paper_no5", "spdata_00_paperno5"))
    paper_name = " ".join(x.strip() for x in (p3, p4, p5) if x and x.strip()) or None
    size = js_get_select_text(page, sel.get("size", "pdata_00_sizeno")) or None
    color = js_get_select_text(page, sel.get("color_mode", "pdata_00_colorno")) or None
    qty_val = page.evaluate(JS_GET_SELECT_VALUE, sel.get("qty", "spdata_00_ordqty"))
    try:
        qty = int(qty_val) if qty_val else None
    except (TypeError, ValueError):
        qty = None
    return {
        "paper_name": paper_name,
        "paper_weight_text": p4.strip() if p4 else None,  # paperno4 가 평량 leaf 인 경우 (예: '250g')
        "size": size,
        "print_mode": color,
        "qty": qty,
    }


# ── 브라우저 셋업 ──

def init_browser(pw, ctx: RunContext):
    browser_cfg = ctx.site_config.get("browser", {})
    browser = pw.chromium.launch(headless=browser_cfg.get("headless", True))
    vp = browser_cfg.get("viewport", {"width": 1280, "height": 900})
    context = browser.new_context(
        viewport={"width": vp["width"], "height": vp["height"]},
        locale=ctx.site_config.get("locale", "ko-KR"),
    )
    for pat in ctx.site_config.get("block_patterns", []):
        context.route(pat, lambda r: r.abort())
    context.on("dialog", lambda d: d.dismiss())
    return browser, context


def goto_with_wait(page, url: str, timeouts: dict, ctx: RunContext, product: str) -> bool:
    try:
        page.goto(url, wait_until="domcontentloaded",
                  timeout=timeouts.get("page_goto_ms", 30000))
        page.wait_for_timeout(timeouts.get("after_goto_ms", 2500))
        return True
    except PwTimeout:
        ctx.log.event("fetch.fail", level="error",
                      product=product, error="page goto timeout")
        return False


def price_with_retry(page, sel: dict, qty: Optional[int], timeouts: dict, guard: dict) -> Optional[int]:
    page.wait_for_timeout(timeouts.get("after_qty_ms", 1200))
    price = read_supply_price(page, sel)
    if price is None:
        page.wait_for_timeout(timeouts.get("retry_price_ms", 1500))
        price = read_supply_price(page, sel)
        if price is None:
            return None
    floor = guard.get("floor_abs", 500)
    if qty:
        floor = max(floor, qty * guard.get("per_qty_multiplier", 3))
    if price < floor:
        page.wait_for_timeout(timeouts.get("retry_price_ms", 1500))
        price = read_supply_price(page, sel)
        if price is None or price < floor:
            return None
    return price


def build_item(page, t: dict, price: int, sel: dict) -> RawItem:
    dom = read_dom_state(page, sel)
    return RawItem(
        product=t["product_name"],
        category=t.get("category", t["product_name"]),
        paper_name=dom["paper_name"],
        paper_weight_text=dom["paper_weight_text"],
        coating=None,  # wowpress 명함 페이지에는 별도 coating select 없음
        print_mode=dom["print_mode"],
        size=dom["size"],
        qty=dom["qty"],
        price=price,
        price_vat_included=False,
        url=t["url"],
        url_ok=True,
        options={},
    )
