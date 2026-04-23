"""
와우프레스 명함 크롤러.

흐름:
  1. config/card_targets.json wowpress 섹션 로드
  2. 각 (제품, 용지, 색도, 수량) 조합마다 페이지 옵션 셋팅 → DOM에서 가격 읽기
  3. raw 값 그대로 저장 (정규화는 common.normalize가 담당)

가격: #od_00_totalcost (VAT 포함 총 결제금액)
용지 코팅: paper_name에 (무광코팅)/(유광코팅)/(무코팅) 박힌 형태 — 정규화 단계가 분리

출력: output/wowpress_card_raw_now.json — output_template 포맷
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
    return cfg.get("wowpress", [])


TARGETS = _load_targets()

TARGET_QTYS = [100, 200, 500, 1000]
COMPANY = "wowpress"
CATEGORY = "card"
SIZE_RAW = "90x50"
COLOR_MODES = [
    {"value": "256", "name": "양면 칼라8도"},
    {"value": "255", "name": "단면 칼라4도"},
]
PAGE_BASE = "https://wowpress.co.kr/ordr/prod/dets?ProdNo={prod_no}"

BLOCK_PATTERNS = [
    "**/google-analytics.com/**", "**/googletagmanager.com/**",
    "**/facebook.net/**", "**/facebook.com/tr/**", "**/doubleclick.net/**",
    "**/criteo.net/**", "**/criteo.com/**", "**/analytics.tiktok.com/**",
]


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


JS_SET_SELECT = """({sel_id, value}) => {
    const el = document.getElementById(sel_id);
    if (!el) return false;
    const opt = [...el.options].find(o => o.value === String(value));
    if (!opt) return 'NO_OPT';
    el.value = String(value);
    // onchange 트리거 (와우프레스 가격 갱신)
    const oc = el.getAttribute('onchange');
    if (oc) { try { eval(oc); } catch(e) {} }
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return true;
}"""

JS_GET_PRICE = """() => {
    const e = document.getElementById('od_00_totalcost');
    return e ? e.textContent.trim() : null;
}"""

JS_AVAIL_OPTIONS = """(sel_id) => {
    const el = document.getElementById(sel_id);
    if (!el) return [];
    return [...el.options].map(o => o.value).filter(v => v);
}"""


JS_READ_DOM_STATE = """() => {
    const sel = id => document.getElementById(id);
    const optText = el => el && el.selectedIndex >= 0
        ? (el.options[el.selectedIndex]?.textContent?.trim() || '')
        : '';
    const val = el => el ? (el.value || '') : '';
    return {
        paper3:     optText(sel('spdata_00_paperno3')),
        paper4:     optText(sel('spdata_00_paperno4')),
        paper5:     optText(sel('spdata_00_paperno5')),
        color_text: optText(sel('pdata_00_colorno')),
        qty_val:    val(sel('spdata_00_ordqty')),
        size_text:  optText(sel('pdata_00_sizeno')),
    };
}"""


def read_dom_state(page) -> dict:
    state = page.evaluate(JS_READ_DOM_STATE) or {}
    parts = [
        (state.get("paper3") or "").strip(),
        (state.get("paper4") or "").strip(),
        (state.get("paper5") or "").strip(),
    ]
    paper_name = " ".join(p for p in parts if p)
    try:
        qty = int(state.get("qty_val") or "")
    except (TypeError, ValueError):
        qty = 0
    return {
        "paper_name": paper_name,
        "color_text": (state.get("color_text") or "").strip(),
        "qty":        qty,
        "size_text":  (state.get("size_text") or "").strip(),
    }

# 와우프레스의 paper select는 paperno3(종류) → paperno4(평량/색상) 2단계.
# 트리 구조가 paper마다 달라(어떤 건 leaf→parent, 어떤 건 leaf→middle→grand)
# paperList JSON에서 leaf로부터 부모 체인을 모두 모아서 paperno3 dropdown에 매칭되는 조상을 찾음.
JS_PAPER_PARENT_CHAIN = """(paperNo) => {
    const pl = document.getElementById('paperList');
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

JS_PAPERNO3_OPTIONS = """() => {
    const el = document.getElementById('spdata_00_paperno3');
    if (!el) return [];
    return [...el.options].map(o => String(o.value)).filter(v => v);
}"""

JS_PAPERNO4_OPTIONS = """() => {
    const el = document.getElementById('spdata_00_paperno4');
    if (!el) return [];
    return [...el.options].map(o => String(o.value)).filter(v => v);
}"""


class WowpressCrawler:
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

    def _set_select(self, page, sel_id: str, value) -> bool:
        try:
            res = page.evaluate(JS_SET_SELECT, {"sel_id": sel_id, "value": value})
            return res is True
        except Exception as e:
            log.warning(f"  select 실패 {sel_id}={value}: {e}")
            return False

    def _select_paper(self, page, paper_no: int) -> bool:
        # leaf paper_no로부터 부모 체인 추출 + paperno3 dropdown 옵션 조회
        chain = page.evaluate(JS_PAPER_PARENT_CHAIN, paper_no) or []
        p3_opts = set(page.evaluate(JS_PAPERNO3_OPTIONS) or [])
        if not chain or not p3_opts:
            log.warning(f"  paper 트리 정보 없음: paper_no={paper_no}")
            return False
        # paperno3 = chain 안에서 dropdown에 있는 첫 ancestor (leaf→root)
        p3_idx = next((i for i, n in enumerate(chain) if str(n) in p3_opts), None)
        if p3_idx is None:
            log.warning(f"  paperno3 매칭 실패: paper_no={paper_no} chain={chain} options={list(p3_opts)[:8]}")
            return False
        p3_value = str(chain[p3_idx])
        if not self._set_select(page, "spdata_00_paperno3", p3_value):
            return False
        page.wait_for_timeout(800)
        # paperno4 후보 = paperno3보다 아래 노드 (leaf, mid). 실제 dropdown에 들어있는 것 우선.
        p4_opts = set(page.evaluate(JS_PAPERNO4_OPTIONS) or [])
        p4_candidates = [str(n) for n in chain[:p3_idx]]
        p4_value = next((c for c in p4_candidates if c in p4_opts), None)
        if p4_value is None:
            # dropdown 비어있거나 매칭 안되면 leaf 그대로 시도 (마지막 fallback)
            p4_value = str(paper_no)
        if not self._set_select(page, "spdata_00_paperno4", p4_value):
            log.warning(f"  paperno4 셋팅 실패: paper_no={paper_no} (paperno3={p3_value}, p4_value={p4_value}, candidates={p4_candidates}, opts={list(p4_opts)[:8]})")
            return False
        page.wait_for_timeout(800)
        return True

    def _crawl_product(self, page, t: dict):
        url = PAGE_BASE.format(prod_no=t["prod_no"])
        log.info(f"▶ {t['category']} / {t['product_name']} (ProdNo={t['prod_no']})")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
        except PwTimeout:
            log.error("  ✖ 페이지 타임아웃")
            return

        # size 셋팅
        if not self._set_select(page, "pdata_00_sizeno", t["size_value"]):
            log.error(f"  ✖ size {t['size_value']} 셋팅 실패")
            return
        page.wait_for_timeout(1000)

        for color in COLOR_MODES:
            if not self._set_select(page, "pdata_00_colorno", color["value"]):
                log.warning(f"  color {color['name']} 셋팅 실패, skip")
                continue
            page.wait_for_timeout(1500)

            # qty 옵션 확인
            avail_qtys = page.evaluate(JS_AVAIL_OPTIONS, "spdata_00_ordqty")
            target_qtys = [str(q) for q in TARGET_QTYS if str(q) in avail_qtys]
            if not target_qtys:
                log.warning(f"  매칭 qty 없음 (사이트: {avail_qtys[:5]})")
                continue
            log.info(f"  color={color['name']} | qty 매칭: {target_qtys}")

            for paper in t["papers"]:
                if not self._select_paper(page, paper["paper_no"]):
                    log.warning(f"    paper 셋팅 실패: {paper['name']}")
                    continue

                for qty_str in target_qtys:
                    if not self._set_select(page, "spdata_00_ordqty", qty_str):
                        continue
                    page.wait_for_timeout(1200)
                    txt = page.evaluate(JS_GET_PRICE)
                    price = parse_price(txt or "")
                    if price is None:
                        log.warning(f"    가격 읽기 실패: {paper['name']} | {color['name']} | {qty_str}매")
                        continue

                    dom = read_dom_state(page)
                    options = {
                        "config_paper_name": paper["name"],
                        "config_color":      color["name"],
                    }
                    if "actual_weight_g" in paper:
                        options["actual_weight_g"] = paper["actual_weight_g"]
                    if "note" in paper:
                        options["note"] = paper["note"]

                    item = {
                        "product":    t["product_name"],
                        "category":   t["category"],
                        "paper_name": dom["paper_name"] or None,
                        # wowpress 명함은 별도 coating select 없음 → null (paper_name 괄호 속 coating 은 normalize 추출)
                        "coating":    None,
                        "print_mode": dom["color_text"] or None,
                        "size":       dom["size_text"]  or None,
                        "qty":        dom["qty"]        or None,
                        "price":      price,
                        "price_vat_included": True,
                        "url":        url,
                        "url_ok":     True,
                        "options":    options,
                    }
                    if paper.get("match_as"):
                        item["match_as"] = paper["match_as"]
                    self.items.append(item)
                    log.info(f"    DOM: {dom['paper_name']} | {dom['color_text']} | {dom['size_text']} | {dom['qty']}매 → {price:,}원")

    def run(self):
        log.info(f"=== 와우프레스 명함 크롤링 시작 ({len(TARGETS)}종 제품) ===")
        if not TARGETS:
            log.error("크롤 타겟 없음 — config/card_targets.json wowpress 섹션 확인")
            return
        start = time.time()
        with sync_playwright() as pw:
            browser, context = self._init_browser(pw)
            page = context.new_page()
            for i, t in enumerate(TARGETS, 1):
                log.info(f"[{i}/{len(TARGETS)}]")
                try:
                    self._crawl_product(page, t)
                except Exception as e:
                    log.error(f"  ✖ {t['product_name']}: {e}")
            browser.close()
        elapsed = time.time() - start
        log.info(f"=== 완료: {len(self.items)}건, {elapsed:.1f}초 ===")


def crawl_all() -> list[dict]:
    c = WowpressCrawler(headless=True)
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
