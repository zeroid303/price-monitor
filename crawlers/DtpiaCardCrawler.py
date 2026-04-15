"""
디티피아(dtpia.co.kr) 명함 크롤러.

흐름:
  1. config/card_targets.json dtpia 섹션 로드
  2. page_type(color/small_qty/special/uv)별로 옵션 셋팅 → DOM 가격 읽기
  3. raw 값 그대로 저장 (정규화는 common.normalize가 담당)

가격: #est_scroll_total_am (VAT 포함 합계)

페이지 타입:
  - color:     coating_type 단일 dropdown (paper × coating 합쳐진 옵션) — 일반명함
  - small_qty: mtrl_cd_01(용지) + mtrl_cd_02(평량) + coating_type — 소량명함
  - special:   mtrl_cd + mtrl_cdw — 고급지명함
  - uv:        mtrl_cd + mtrl_cdw — UV옵셋명함

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

    def _emit(self, t, paper, color_name, qty, price):
        out_paper = paper.get("match_as", paper.get("paper_name_out", ""))
        coating_out = paper.get("coating_out_runtime", paper.get("coating_out", ""))
        options = {}
        if "actual_weight_g" in paper:
            options["actual_weight_g"] = paper["actual_weight_g"]
        if "note" in paper:
            options["note"] = paper["note"]
        self.items.append({
            "product": t["product_name"],
            "category": t["category"],
            "paper_name": out_paper,
            "coating": coating_out,
            "print_mode": color_name,
            "size": SIZE_RAW,
            "qty": qty,
            "price": price,
            "price_vat_included": True,
            "url": t["url"],
            "url_ok": True,
            "options": options,
        })
        log.info(f"    {out_paper} | {coating_out or '-'} | {color_name} | {qty}매 → {price:,}원")

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
            elif page_type == "small_qty":
                if not self._set(page, "mtrl_cd_01", paper["mtrl_01"]):
                    log.warning(f"    mtrl_cd_01={paper['mtrl_01']} 실패"); continue
                page.wait_for_timeout(500)
                if not self._set(page, "mtrl_cd_02", paper["mtrl_02"]):
                    log.warning(f"    mtrl_cd_02={paper['mtrl_02']} 실패"); continue
                page.wait_for_timeout(500)
                # coating_type iterate
                paper_loops = []
                for c in paper["coatings"]:
                    pcopy = dict(paper)
                    pcopy["coating_select_value"] = c["value"]
                    pcopy["coating_out_runtime"] = c["name"]
                    paper_loops.append(pcopy)
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
                # small_qty의 경우 coating_type 셋팅
                if page_type == "small_qty":
                    if not self._set(page, "coating_type", p_inst["coating_select_value"]):
                        log.warning(f"    coating_type={p_inst['coating_select_value']} 실패"); continue
                    page.wait_for_timeout(400)
                # color iterate
                for color in t["color_modes"]:
                    if not self._set(page, "prn_clr_cn_gb", color["value"]):
                        log.warning(f"    color={color['name']} 실패"); continue
                    page.wait_for_timeout(400)
                    # 각 paper/color 변경 시 qty/size가 reset될 수 있어 재셋팅
                    self._set(page, "ppr_cut_tmp", t["size_value"])
                    for qty in target_qtys:
                        if not self._set(page, "prn_sht_cn", str(qty)):
                            continue
                        page.wait_for_timeout(300)
                        price = self._read_price(page)
                        if price is None:
                            log.warning(f"    가격 읽기 실패: {p_inst.get('paper_name_out')} | {color['name']} | {qty}매")
                            continue
                        self._emit(t, p_inst, color["name"], qty, price)

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
