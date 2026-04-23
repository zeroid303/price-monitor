"""
디티피아(dtpia.co.kr) 명함 크롤러.

흐름:
  1. config/card_targets.json dtpia 섹션 로드
  2. page_type(color/special/uv)별로 옵션 셋팅 → DOM 가격 읽기
  3. raw 값 그대로 저장 (정규화는 common.normalize가 담당)

가격: #est_scroll_total_am (VAT 포함 합계)

페이지 타입 (오프셋 명함만):
  - color:   coating_type 단일 dropdown (paper × coating 합쳐진 옵션) — 일반명함
  - special: mtrl_cd + mtrl_cdw — 고급지명함
  - uv:      mtrl_cd + mtrl_cdw — UV옵셋명함
  ※ 디지털명함(SmallQuantity.aspx, page_type=small_qty)은 본 크롤러 범위 제외.

출력: output/dtpia_card_raw_now.json — output_template 포맷
"""
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "card_targets.json"


def _load_targets() -> list[dict]:
    if not _CONFIG_PATH.exists():
        return []
    cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    return cfg.get("dtpia", [])


TARGETS = _load_targets()

COMPANY = "dtpia"
CATEGORY = "card"
SIZE_RAW = "90x50"

BLOCK_PATTERNS = [
    "**/google-analytics.com/**", "**/googletagmanager.com/**",
    "**/facebook.net/**", "**/facebook.com/tr/**", "**/doubleclick.net/**",
    "**/criteo.net/**", "**/criteo.com/**", "**/analytics.tiktok.com/**",
]


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

JS_GET_PRICE = """() => {
    const el = document.getElementById('est_scroll_total_am');
    return el ? el.textContent.trim() : null;
}"""


JS_READ_DOM_STATE = """() => {
    const selText = (id) => {
        const el = document.getElementById(id);
        if (!el || el.selectedIndex < 0) return '';
        return (el.options[el.selectedIndex]?.textContent || '').trim();
    };
    const selVal = (id) => {
        const el = document.getElementById(id);
        return el ? (el.value || '') : '';
    };
    return {
        // 일반명함(color): coating_type select 에 paper×coating 합쳐짐
        coating_text:   selText('coating_type'),
        // 특수지/UV: mtrl_cd (paper) + mtrl_cdw (평량)
        mtrl_text:      selText('mtrl_cd'),
        mtrl_cdw_text:  selText('mtrl_cdw'),
        color_text:     selText('prn_clr_cn_gb'),
        size_text:      selText('ppr_cut_tmp'),
        qty_val:        selVal('prn_sht_cn'),
    };
}"""


def read_dom_state(page) -> dict:
    raw = page.evaluate(JS_READ_DOM_STATE) or {}
    try:
        qty = int(raw.get("qty_val") or "")
    except (TypeError, ValueError):
        qty = 0
    # paper_name: mtrl_cd 있으면 우선, 아니면 coating_type (color page는 paper×coating 결합형)
    mtrl = (raw.get("mtrl_text") or "").strip()
    mtrl_cdw = (raw.get("mtrl_cdw_text") or "").strip()
    coat = (raw.get("coating_text") or "").strip()
    # 특수지/UV: mtrl + mtrl_cdw 결합 ("럭셔리 반누보 화이트 230g"형태면 mtrl_cdw 비어도 됨)
    if mtrl:
        paper = (f"{mtrl} {mtrl_cdw}".strip() if mtrl_cdw else mtrl)
    else:
        paper = coat   # color page: coating_type text 에 "스노우지 250g(무광코팅)" 식으로 올 수 있음
    return {
        "paper_name": paper,
        "coating":    coat,      # color page에선 paper=coating text로 같음(덮어쓰는 모양새). normalize가 paper_name 괄호에서 coating 추출.
        "print_mode": (raw.get("color_text") or "").strip(),
        "size":       (raw.get("size_text") or "").strip(),
        "qty":        qty,
    }


def parse_price(txt: str) -> int | None:
    if not txt:
        return None
    m = re.search(r"[\d,]+", txt.replace(" ", ""))
    if not m:
        return None
    try:
        return int(m.group().replace(",", ""))
    except ValueError:
        return None


class DtpiaCrawler:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.items: list[dict] = []

    def _init_browser(self, pw):
        browser = pw.chromium.launch(headless=self.headless)
        context = browser.new_context(viewport={"width": 1280, "height": 900}, locale="ko-KR")
        for pat in BLOCK_PATTERNS:
            context.route(pat, lambda r: r.abort())
        context.on("dialog", lambda d: d.dismiss())
        return browser, context

    def _set(self, page, sel_id: str, value) -> bool:
        try:
            res = page.evaluate(JS_SET_SELECT, {"sel_id": sel_id, "value": value})
            return res is True
        except Exception as e:
            log.warning(f"  select {sel_id}={value} 실패: {e}")
            return False

    def _read_price(self, page) -> int | None:
        page.evaluate(JS_TRIGGER_PRICE)
        page.wait_for_timeout(900)
        txt = page.evaluate(JS_GET_PRICE)
        return parse_price(txt or "")

    def _emit(self, page, t, paper, color_name, qty, price):
        # raw 원칙: DOM 실측. 없으면 null. config 는 options 에 추적용으로만.
        dom = read_dom_state(page)
        options = {
            "config_paper_name_out": paper.get("paper_name_out"),
            "config_coating_out":    paper.get("coating_out_runtime", paper.get("coating_out")),
            "config_color_name":     color_name,
        }
        if "actual_weight_g" in paper:
            options["actual_weight_g"] = paper["actual_weight_g"]
        if "note" in paper:
            options["note"] = paper["note"]
        item = {
            "product":    t["product_name"],
            "category":   t["category"],
            "paper_name": dom["paper_name"] or None,
            "coating":    dom["coating"]    or None,
            "print_mode": dom["print_mode"] or None,
            "size":       dom["size"]       or None,
            "qty":        dom["qty"]        or None,
            "price":      price,
            "price_vat_included": True,
            "url":        t["url"],
            "url_ok":     True,
            "options":    options,
        }
        if paper.get("match_as"):
            item["match_as"] = paper["match_as"]
        self.items.append(item)
        log.info(f"    DOM: {dom['paper_name']} | {dom['coating'] or '-'} | {dom['print_mode']} | {dom['qty']}매 → {price:,}원")

    def _crawl_target(self, page, t: dict):
        log.info(f"▶ {t['category']} / {t['product_name']} ({t['page_type']})")
        try:
            page.goto(t["url"], wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
        except PwTimeout:
            log.error("  ✖ 페이지 타임아웃")
            return

        # size 셋팅 (size_select_id는 페이지마다 다를 수 있지만 ppr_cut_tmp가 표준)
        if not self._set(page, "ppr_cut_tmp", t["size_value"]):
            log.warning(f"  size 셋팅 실패 (계속 진행): {t['size_value']}")
        page.wait_for_timeout(500)

        avail_qtys = page.evaluate(JS_AVAIL_OPTIONS, "prn_sht_cn")
        target_qtys = [q for q in t["qtys"] if str(q) in avail_qtys]
        log.info(f"  qty 매칭: {target_qtys} (사이트: {avail_qtys[:6]})")
        if not target_qtys:
            log.warning("  매칭 qty 없음, skip")
            return

        page_type = t["page_type"]
        for paper in t["papers"]:
            # 1. paper 옵션 셋팅 (page_type별)
            if page_type == "color":
                # 코팅타입 옵션이 paper×coating
                if not self._set(page, "coating_type", paper["coating_select_value"]):
                    log.warning(f"    coating_type={paper['coating_select_value']!r} 셋팅 실패")
                    continue
                paper_loops = [paper]  # 단일 조합
            elif page_type in ("special", "uv"):
                if not self._set(page, "mtrl_cd", paper["mtrl_cd"]):
                    log.warning(f"    mtrl_cd={paper['mtrl_cd']} 실패"); continue
                page.wait_for_timeout(500)
                if "mtrl_cdw" in paper:
                    self._set(page, "mtrl_cdw", paper["mtrl_cdw"])
                    page.wait_for_timeout(500)
                pcopy = dict(paper)
                pcopy["coating_out_runtime"] = "코팅없음"
                paper_loops = [pcopy]
            else:
                log.warning(f"    unknown page_type: {page_type}"); continue

            for p_inst in paper_loops:
                # color iterate
                for color in t["color_modes"]:
                    if not self._set(page, "prn_clr_cn_gb", color["value"]):
                        log.warning(f"    color={color['name']} 실패"); continue
                    page.wait_for_timeout(400)
                    # 각 paper/color 변경 시 qty/size가 reset될 수 있어 재셋팅 + 검증
                    self._set(page, "ppr_cut_tmp", t["size_value"])
                    page.wait_for_timeout(200)
                    actual_size = page.evaluate(
                        "() => (document.getElementById('ppr_cut_tmp') || {}).value || ''"
                    )
                    if actual_size != t["size_value"]:
                        # 1회 재시도
                        self._set(page, "ppr_cut_tmp", t["size_value"])
                        page.wait_for_timeout(400)
                        actual_size = page.evaluate(
                            "() => (document.getElementById('ppr_cut_tmp') || {}).value || ''"
                        )
                        if actual_size != t["size_value"]:
                            log.warning(f"    size 셋팅 실패 (expected={t['size_value']}, actual={actual_size!r}) → skip")
                            continue
                    for qty in target_qtys:
                        if not self._set(page, "prn_sht_cn", str(qty)):
                            continue
                        page.wait_for_timeout(300)
                        price = self._read_price(page)
                        if price is None:
                            log.warning(f"    가격 읽기 실패: {p_inst.get('paper_name_out')} | {color['name']} | {qty}매")
                            continue
                        # 비정상 저가 방어: 1매 단가 수준(qty×최소단가 이하) 차단 + 재시도
                        # dtpia 오프셋 명함의 이론적 최저는 1000매 기준 약 5,000원 이상 (공급가)
                        min_reasonable = max(500, qty * 3)  # qty=1000이면 3000, qty=200이면 600, qty=100이면 500
                        if price < min_reasonable:
                            log.warning(f"    비정상 저가 {price}원 (qty={qty}, 기대 ≥{min_reasonable}) → callPrice 재시도")
                            page.evaluate("() => { if (typeof callPrice === 'function') callPrice(); }")
                            page.wait_for_timeout(1500)
                            price = self._read_price(page)
                            if price is None or price < min_reasonable:
                                log.warning(f"    재시도 후에도 비정상({price}) → skip")
                                continue
                        self._emit(page, t, p_inst, color["name"], qty, price)

    def run(self):
        log.info(f"=== 디티피아 명함 크롤링 시작 ({len(TARGETS)}종 제품) ===")
        if not TARGETS:
            log.error("크롤 타겟 없음 — config/card_targets.json dtpia 섹션 확인")
            return
        start = time.time()
        with sync_playwright() as pw:
            browser, context = self._init_browser(pw)
            page = context.new_page()
            for i, t in enumerate(TARGETS, 1):
                log.info(f"[{i}/{len(TARGETS)}]")
                try:
                    self._crawl_target(page, t)
                except Exception as e:
                    log.error(f"  ✖ {t['product_name']}: {e}")
            browser.close()
        elapsed = time.time() - start
        log.info(f"=== 완료: {len(self.items)}건, {elapsed:.1f}초 ===")


def crawl_all() -> list[dict]:
    c = DtpiaCrawler(headless=True)
    c.run()
    return c.items


def save(items: list[dict]):
    base = Path(__file__).resolve().parent.parent
    outdir = base / "output"
    outdir.mkdir(exist_ok=True)
    raw_now = outdir / f"{COMPANY}_{CATEGORY}_raw_now.json"
    output = {
        "company": COMPANY,
        "crawled_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "items": items,
    }
    with open(raw_now, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    log.info(f"저장: {raw_now} ({len(items)}건)")


if __name__ == "__main__":
    items = crawl_all()
    save(items)
