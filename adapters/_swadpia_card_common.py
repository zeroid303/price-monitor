"""성원애드피아(swadpia) 명함 어댑터 공통 헬퍼.

페이지 메커니즘:
- select 식별이 `name` 속성 기반 (id 가 아닌 이유로 dtpia 와 다름)
- coating 이 라디오 (paper_gloss / paper_gloss2 두 그룹, 한 번에 하나만 체크)
- 가격 = `tr.estimate_supply_amt td.price` 텍스트 (공급가 ₩X,XXX 형식)

raw 원칙:
- paper_name = paper_code select 의 selected text 그대로
- size = paper_size select text
- coating = checked 된 paper_gloss* 라디오의 label text (또는 value 매핑)
- print_mode = print_color_type select text
- price = price_supply element 텍스트에서 정수 추출 (공급가)
"""
import re
from typing import Iterator, Optional

from playwright.sync_api import TimeoutError as PwTimeout

from engine.context import RawItem, RunContext


# ── JS 헬퍼 ──

JS_GET_SELECT_TEXT = """(selector) => {
    const el = document.querySelector(selector);
    if (!el || el.tagName !== 'SELECT' || el.selectedIndex < 0) return '';
    return (el.options[el.selectedIndex]?.textContent || '').trim();
}"""

JS_GET_SELECT_VALUE = """(selector) => {
    const el = document.querySelector(selector);
    return el ? (el.value || '') : '';
}"""

JS_SET_SELECT = """({selector, value}) => {
    const el = document.querySelector(selector);
    if (!el) return 'NO_EL';
    const wantedVal = String(value);
    const opt = [...el.options].find(o => o.value === wantedVal);
    if (!opt) return 'NO_OPT';   // 옵션에 없으면 셋팅 X — paper 변경 후 동적으로 사라진 옵션 케이스
    el.value = wantedVal;
    el.dispatchEvent(new Event('change', {bubbles: true}));
    if (window.jQuery) { try { window.jQuery(el).trigger('change'); } catch(e) {} }
    return true;
}"""

JS_CLICK_RADIO = """({name, value}) => {
    // 클릭 전에 다른 그룹의 checked 모두 해제 — paper_gloss 와 paper_gloss2 가
    // 다른 name 이라 자동 mutual exclusion 안 됨. 한쪽 그룹 클릭해도 다른 쪽 잔존.
    const otherName = (name === 'paper_gloss') ? 'paper_gloss2' : 'paper_gloss';
    document.querySelectorAll(`input[type=radio][name=${otherName}]`).forEach(r => { r.checked = false; });
    const r = document.querySelector(`input[type=radio][name=${name}][value=${value}]`);
    if (!r) return 'NO_RADIO';
    r.click();
    if (window.jQuery) {
        try { window.jQuery(r).trigger('click').trigger('change'); } catch(e) {}
    }
    return true;
}"""

JS_GET_AVAILABLE_QTY = """(selector) => {
    const el = document.querySelector(selector);
    if (!el) return [];
    return [...el.options].map(o => o.value).filter(v => v && /^\\d+$/.test(v)).map(v => parseInt(v, 10));
}"""

JS_GET_PRICE_SUPPLY = """(selector) => {
    const el = document.querySelector(selector);
    return el ? el.textContent.trim() : null;
}"""

# coating 라디오 — 두 그룹 (paper_gloss/paper_gloss2) 중 checked 된 것의 label
JS_GET_COATING = """() => {
    for (const r of document.querySelectorAll('input[type=radio][name^=paper_gloss]:checked')) {
        // label text 우선
        if (r.id) {
            const lab = document.querySelector(`label[for="${r.id}"]`);
            if (lab && lab.textContent.trim()) return lab.textContent.trim();
        }
        const wrap = r.closest('label');
        if (wrap && wrap.textContent.trim()) return wrap.textContent.trim();
        // sibling text node
        const nxt = r.nextSibling;
        if (nxt && nxt.nodeType === 3) {
            const t = nxt.textContent.trim();
            if (t) return t;
        }
        // fallback: value 매핑
        const map = {'PAG99': '코팅없음', 'PAG10': '무광코팅', 'PAG20': '유광코팅'};
        return map[r.value] || r.value || '';
    }
    return '';
}"""


# ── 가격 파싱 ──

def parse_price(txt: Optional[str]) -> Optional[int]:
    """₩4,200 또는 \\4,200 같은 텍스트에서 정수 추출."""
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

def js_set_select(page, selector: str, value) -> bool:
    try:
        return page.evaluate(JS_SET_SELECT, {"selector": selector, "value": value}) is True
    except Exception:
        return False


def js_get_select_text(page, selector: str) -> str:
    try:
        return page.evaluate(JS_GET_SELECT_TEXT, selector) or ""
    except Exception:
        return ""


def js_click_radio(page, name: str, value: str) -> bool:
    try:
        return page.evaluate(JS_CLICK_RADIO, {"name": name, "value": value}) is True
    except Exception:
        return False


def read_supply_price(page, selector: str, after_trigger_ms: int) -> Optional[int]:
    page.wait_for_timeout(after_trigger_ms)
    txt = page.evaluate(JS_GET_PRICE_SUPPLY, selector)
    return parse_price(txt or "")


# ── DOM 실측 ──

def read_dom_state(page, sel: dict, has_coating: bool = True) -> dict:
    """DOM 표시값 추출. paper_name = paper_code select selected text.

    digital 페이지(paper_type 별도 select 존재)의 경우 paper_code 가 색깔만 표기되는
    paper 도 있어 paper_type text 도 합쳐서 paper_name 생성.
    """
    paper_name = js_get_select_text(page, sel.get("paper_code", "select[name=paper_code]")) or None
    paper_type_sel = sel.get("paper_type")
    if paper_type_sel:
        paper_type_text = js_get_select_text(page, paper_type_sel) or ""
        # paper_type text 가 의미있고 paper_name 안에 이미 포함 안 되어 있으면 prefix
        if paper_type_text and paper_name and paper_type_text not in paper_name:
            paper_name = f"{paper_type_text} {paper_name}".strip()
    size = js_get_select_text(page, sel.get("paper_size", "select[name=paper_size]")) or None
    print_mode = js_get_select_text(page, sel.get("color_mode", "select[name=print_color_type]")) or None
    qty_val = page.evaluate(JS_GET_SELECT_VALUE, sel.get("qty", "select[name=paper_qty_select]"))
    try:
        qty = int(qty_val) if qty_val else None
    except (TypeError, ValueError):
        qty = None
    coating = None
    if has_coating:
        try:
            coating = page.evaluate(JS_GET_COATING) or None
        except Exception:
            coating = None
    return {
        "paper_name": paper_name,
        "paper_weight_text": None,  # swadpia 는 paper_code text 가 paper+weight 결합 (예: '스노우지 백색 250g')
        "coating": coating,
        "print_mode": print_mode,
        "size": size,
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


def price_with_retry(
    page, price_selector: str, qty: int,
    timeouts: dict, guard: dict,
) -> Optional[int]:
    price = read_supply_price(page, price_selector, timeouts.get("after_price_trigger_ms", 700))
    if price is None:
        return None
    floor = max(guard.get("floor_abs", 500), qty * guard.get("per_qty_multiplier", 3))
    if price < floor:
        page.wait_for_timeout(timeouts.get("retry_price_ms", 1500))
        price = read_supply_price(page, price_selector, timeouts.get("after_price_trigger_ms", 700))
        if price is None or price < floor:
            return None
    return price


def build_item(
    page, t: dict, paper: dict, price: int, sel: dict,
    has_coating: bool = True,
) -> RawItem:
    dom = read_dom_state(page, sel, has_coating=has_coating)
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
        price_vat_included=False,  # tr.estimate_supply_amt 는 공급가 (VAT 제외)
        url=t["url"],
        url_ok=True,
        options={},
    )
