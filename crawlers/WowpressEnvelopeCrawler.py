"""와우프레스 봉투 크롤러.
config/envelope_targets.json wowpress 섹션 참조.

WowpressCardCrawler 패턴 이식:
  - 사이즈 변경 후 reqMdmDetail AJAX → 용지 cascade 옵션 갱신 (최소 5초 대기 필요)
  - paperList JSON 트리에서 leaf paper_no의 parent chain 추출
  - paperno3(용지 종류) → paperno4(색상) → paperno5/sPaper0(평량) 3단 주입
  - ColorNo 고정: 40034 칼라4도(255) / 40035 먹1도(빈값=기본 선택)
  - 수량=1000, 건수=1

가격: #od_00_totalcost (VAT 포함)

매출 TOP 매칭 (40034 칼라봉투):
  - 모조 100g (소봉투만), 모조 120g (대/9절/소)
  - 레자크체크백 110g, 레자크줄백 110g
매출 TOP 매칭 (40035 마스타봉투·흑백):
  - 모조 100g/120g, 레자크줄백 110g (사이즈별 공급 제한)

결측:
  - 크라프트 98g · 랑데뷰 130/160g: 와우프레스 봉투에선 canonical 대응 제품 미공급(paperList 옵션 있으나 1000매 가격 0원)
  - 모조 150g · 180g: 와우프레스 봉투 미취급
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

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "envelope_targets.json"
COMPANY = "wowpress"
CATEGORY = "envelope"


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


def _load_targets() -> list[dict]:
    if not _CONFIG_PATH.exists():
        return []
    cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    return [t for t in cfg.get("wowpress", []) if isinstance(t, dict) and "prod_no" in t]


TARGETS = _load_targets()


def set_select_by_id(page, sel_id: str, value: str) -> bool:
    """id로 select 값 설정 + onchange 트리거."""
    try:
        res = page.evaluate(f"""() => {{
            const el = document.getElementById('{sel_id}');
            if (!el) return 'NO_EL';
            el.value = '{value}';
            const oc = el.getAttribute('onchange');
            if (oc) {{ try {{ (new Function('event', oc)).call(el, null); }} catch(e) {{}} }}
            return true;
        }}""")
        return res is True
    except Exception as e:
        log.warning(f"  select 실패 {sel_id}={value}: {e}")
        return False


def set_size_req(page, size_value: str):
    """사이즈 변경 — reqMdmDetail 트리거 (AJAX 로드)."""
    page.evaluate(f"""() => {{
        const el = document.getElementById('pdata_00_sizeno') || document.querySelector('select[name="SizeNo"]');
        if (!el) return;
        el.value = '{size_value}';
        if (typeof reqMdmDetail === 'function') {{
            reqMdmDetail('Size', '{size_value}', '0', 'hdata_00_sizeno');
        }}
        const oc = el.getAttribute('onchange');
        if (oc) {{ try {{ (new Function('event', oc)).call(el, null); }} catch(e) {{}} }}
    }}""")


def get_price(page) -> int:
    try:
        txt = page.evaluate(
            "() => (document.getElementById('od_00_totalcost') || {}).textContent || ''"
        )
        cleaned = re.sub(r"[^0-9]", "", str(txt))
        return int(cleaned) if cleaned else 0
    except Exception:
        return 0


def get_select_options(page, sel_id: str) -> set[str]:
    return set(page.evaluate(f"""() => {{
        const el = document.getElementById('{sel_id}');
        if (!el) return [];
        return [...el.options].map(o => String(o.value)).filter(v => v);
    }}""") or [])


def read_dom_state(page) -> dict:
    """주문 페이지 현재 선택값의 DOM 실측 — raw 필드 소스.

    반환 키:
      - paper_name: paperno3 + paperno4 + paperno5 선택 옵션 텍스트 (공백 연결)
                    (예: "모조지 백색 120g")
      - color_text: #pdata_00_colorno 선택 옵션 텍스트 (예: "칼라 4도")
      - qty:       #spdata_00_ordqty 값 (정수)
      - size_text: #pdata_00_sizeno 선택 옵션 텍스트 (예: "대봉투-규격")
    """
    state = page.evaluate(
        """() => {
            const sel = id => document.getElementById(id);
            const optText = el => el && el.selectedIndex >= 0
                ? (el.options[el.selectedIndex]?.textContent?.trim() || '')
                : '';
            const val = el => el ? (el.value || '') : '';
            return {
                paper3:      optText(sel('spdata_00_paperno3')),
                paper4:      optText(sel('spdata_00_paperno4')),
                paper5:      optText(sel('spdata_00_paperno5')),
                color_text:  optText(sel('pdata_00_colorno')),
                qty_val:     val(sel('spdata_00_ordqty')),
                size_text:   optText(sel('pdata_00_sizeno')),
            };
        }"""
    ) or {}
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


class WowpressEnvelopeCrawler:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.items: list[dict] = []

    def _apply_common_options(self, page, t: dict, include_color: bool = True):
        """ColorNo(선택적, 용지 변경 후엔 건드리지 않음) + 수량 + 건수."""
        if include_color:
            color_no = t.get("color_no", "")
            if color_no:
                set_select_by_id(page, "pdata_00_colorno", color_no)
                time.sleep(0.6)
        set_select_by_id(page, "spdata_00_ordqty", t.get("qty_target", "1000"))
        time.sleep(0.5)
        set_select_by_id(page, "pdata_00_ordcnt", "1")
        time.sleep(0.4)

    def _select_paper(self, page, paper_no: int) -> bool:
        """leaf paper_no → parent chain 추출 → paperno3/paperno4/paperno5 순차 설정.
        각 단계마다 AJAX 로드 충분히 대기 + 실제 value 검증.
        """
        chain = page.evaluate(JS_PAPER_PARENT_CHAIN, paper_no) or []
        if not chain:
            log.warning(f"    paper_no={paper_no} chain 추출 실패")
            return False
        # chain: [leaf, ..., root(W2.0)]. paperno3 options에 존재하는 첫 ancestor 찾기.
        p3_opts = get_select_options(page, "spdata_00_paperno3")
        p3_idx = next((i for i, n in enumerate(chain) if str(n) in p3_opts), None)
        if p3_idx is None:
            log.warning(f"    paperno3 매칭 실패: paper_no={paper_no} chain={chain}")
            return False
        p3_value = str(chain[p3_idx])
        if not set_select_by_id(page, "spdata_00_paperno3", p3_value):
            return False
        time.sleep(2.5)
        # paperno4: chain 내 paperno3 이전(leaf 방향) ancestor 중 dropdown에 있는 것
        p4_opts = get_select_options(page, "spdata_00_paperno4")
        p4_value = next((str(n) for n in chain[:p3_idx] if str(n) in p4_opts), None)
        if p4_value is None and p4_opts:
            p4_value = next(iter(p4_opts))
        if not p4_value:
            log.warning(f"    paperno4 옵션 없음 (paper_no={paper_no})")
            return False
        set_select_by_id(page, "spdata_00_paperno4", p4_value)
        time.sleep(2.5)
        # paperno5 (sPaper0 평량): leaf paper_no가 dropdown에 있어야 함
        p5_opts = get_select_options(page, "spdata_00_paperno5")
        if str(paper_no) in p5_opts:
            p5_value = str(paper_no)
        elif p5_opts:
            # paperList chain의 다른 leaf도 시도
            alt = next((str(n) for n in chain if str(n) in p5_opts), None)
            if alt is None:
                alt = next(iter(p5_opts))
            log.info(f"      paperno5에 {paper_no} 없음, fallback={alt} (opts={list(p5_opts)[:5]})")
            p5_value = alt
        else:
            log.warning(f"    paperno5 옵션 비어있음 (paper_no={paper_no})")
            return False
        set_select_by_id(page, "spdata_00_paperno5", p5_value)
        time.sleep(1.8)
        # 최종 검증
        current = page.evaluate(r"""() => ({
            p3: document.getElementById('spdata_00_paperno3')?.value,
            p4: document.getElementById('spdata_00_paperno4')?.value,
            p5: document.getElementById('spdata_00_paperno5')?.value,
        })""")
        log.debug(f"    paper set: p3={current.get('p3')} p4={current.get('p4')} p5={current.get('p5')} (target leaf={paper_no})")
        if str(current.get("p5")) != str(paper_no):
            log.warning(f"    p5 최종값 불일치: got {current.get('p5')} expected {paper_no}")
        return True

    def _crawl_product(self, page, t: dict):
        url = t["url"]
        prod_no = t["prod_no"]
        print_mode = t["print_mode"]
        log.info(f"  [{t['product_name']}] ProdNo={prod_no}")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(6000)
        except PwTimeout:
            log.warning("    page timeout")
            return

        for sz in t["sizes"]:
            log.info(f"    [size] {sz['canonical']} = {sz['label']} (value={sz['size_value']})")
            set_size_req(page, sz["size_value"])
            time.sleep(6)  # AJAX paperno3 갱신 대기
            self._apply_common_options(page, t)
            time.sleep(0.5)

            for paper in t["papers"]:
                paper_no = paper["paper_no"]
                paper_name = paper["name"]
                if not self._select_paper(page, paper_no):
                    log.info(f"      - {paper_name}: 해당 사이즈에 옵션 없음")
                    continue
                # 용지 변경 후 수량/건수만 재적용 (ColorNo 건드리면 용지 선택 리셋됨)
                self._apply_common_options(page, t, include_color=False)
                time.sleep(0.5)
                # 가격 계산 트리거
                page.evaluate("() => { if (typeof fnOrdSummary === 'function') fnOrdSummary(); }")
                time.sleep(2.0)
                price = get_price(page)
                dom = read_dom_state(page)
                if price > 0:
                    # raw 원칙: DOM 실측. 없으면 null. config 폴백 금지.
                    # 봉투는 coating select 없음 → coating = None.
                    self.items.append({
                        "product":    t["product_name"],
                        "category":   "봉투",
                        "paper_name": dom["paper_name"] or None,
                        "coating":    None,
                        "print_mode": dom["color_text"] or None,
                        "size":       dom["size_text"] or None,
                        "size_raw":   sz.get("label", ""),
                        "qty":        dom["qty"] or None,
                        "price":      price,
                        "price_vat_included": True,
                        "url":        url,
                        "url_ok":     True,
                        "options": {
                            "paper_no":          paper_no,
                            "size_value":        sz["size_value"],
                            "size_canonical":    sz["canonical"],
                            "config_paper_name": paper_name,
                            "config_print_mode": print_mode,
                            "config_qty":        int(t.get("qty_target", "1000")),
                        },
                    })
                    log.info(f"      ✓ DOM='{dom['paper_name']}' / {dom['color_text']} / {dom['size_text']} → {price:,}원")
                else:
                    log.info(f"      - {paper_name}: 1000매 가격 없음 (미공급)")

    def run(self):
        log.info(f"=== 와우프레스 봉투 크롤링 시작 ({len(TARGETS)}종) ===")
        if not TARGETS:
            log.error("크롤 타겟 없음")
            return
        start = time.time()
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless)
            context = browser.new_context(viewport={"width": 1400, "height": 900}, locale="ko-KR")
            context.on("dialog", lambda d: d.dismiss())
            page = context.new_page()
            for i, t in enumerate(TARGETS, 1):
                log.info(f"[{i}/{len(TARGETS)}]")
                try:
                    self._crawl_product(page, t)
                except Exception as e:
                    log.error(f"  error: {e}", exc_info=True)
            browser.close()
        elapsed = time.time() - start
        log.info(f"=== 완료: {len(self.items)}건, {elapsed:.1f}초 ===")


def crawl_all() -> list[dict]:
    c = WowpressEnvelopeCrawler(headless=True)
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
    log.info(f"saved: {raw_now} ({len(items)} items)")


if __name__ == "__main__":
    items = crawl_all()
    save(items)
