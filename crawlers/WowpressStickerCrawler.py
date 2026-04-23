#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WowpressStickerCrawler.py
와우프레스 도무송 스티커 가격 크롤러

대상: https://wowpress.co.kr/ordr/prod/dets?ProdNo=40014
조건: 강접아트지(유광코팅) 80g / 단면칼라 / 1000매
사이즈: 40x40, 50x50, 60x60 (프리셋), 70x70, 80x80, 90x90 (비규격)

실행:
    python crawlers/WowpressStickerCrawler.py          # headless=True
    python crawlers/WowpressStickerCrawler.py --show   # headless=False (디버깅)
"""

import os
import json
import time
from pathlib import Path
import re
from datetime import datetime
from playwright.sync_api import sync_playwright, Page

# ─── 상수 ──────────────────────────────────────────────────────────────────────
COMPANY    = "wowpress"
CATEGORY   = "sticker"
URL        = "https://wowpress.co.kr/ordr/prod/dets?ProdNo=40617"
OUTPUT_DIR = "output"

# pdata_00_sizeno 기준 원형 프리셋 옵션값
# DOM 확인: 원형45=5517, 원형55=5519, 원형65=5521
PRESET_SIZES: dict[int, str] = {
    45: "5517",
    55: "5519",
    65: "5521",
}

# 비규격 size value
IRREGULAR_SIZE_VALUE = "6066"

# 비규격 작업 여백 (+5mm)
BLEED = 5

# 크롤링 대상 사이즈
TARGET_SIZES = [45, 55, 65, 75, 85, 95]

# ─── 공통 유틸 ─────────────────────────────────────────────────────────────────

def ensure_output_dir() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def set_select_and_trigger(page: Page, selector: str, value: str) -> None:
    """
    select[selector].value = value 후 onchange 속성 JS 실행.
    와우프레스의 모든 select는 onchange에 직접 JS 함수가 바인딩되어 있어
    value만 바꾸면 가격이 갱신되지 않으므로 반드시 eval이 필요함.
    """
    page.evaluate(
        """([sel, val]) => {
            const el = document.querySelector(sel);
            if (!el) return;
            el.value = val;
            const oc = el.getAttribute('onchange');
            if (oc) {
                // 'this' 바인딩을 위해 Function 생성 후 call
                try { (new Function('event', oc)).call(el, null); }
                catch(e) { console.warn('onchange eval error:', e); }
            }
        }""",
        [selector, value],
    )


def get_price(page: Page) -> int:
    """#od_00_totalcost 텍스트에서 숫자만 추출 (VAT 포함 총 결제금액)"""
    try:
        text = page.evaluate(
            "() => (document.getElementById('od_00_totalcost') || {}).textContent || ''"
        )
        cleaned = re.sub(r"[^0-9]", "", str(text))
        return int(cleaned) if cleaned else 0
    except Exception:
        return 0


def read_dom_state(page: Page) -> dict:
    """주문 페이지 현재 선택값의 DOM 실측 — 크롤러 raw 필드 채울 소스.

    반환 키:
      - paper_name: paperno3 텍스트 + paperno4 텍스트 (공백 연결)
                    예: "초강접아트지(유광코팅) 100g"
      - color_text: #pdata_00_colorno 선택 옵션 텍스트 (예: "단면 칼라")
      - qty:       #spdata_00_ordqty 값 (정수)
      - size_text: #pdata_00_sizeno 선택 옵션 텍스트 (예: "원형45", "비규격")
      - shape:     size_text 의 숫자 앞부분 (예: "원형")
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
                color_text:  optText(sel('pdata_00_colorno')),
                qty_val:     val(sel('spdata_00_ordqty')),
                size_text:   optText(sel('pdata_00_sizeno')),
            };
        }"""
    )
    p3 = (state.get("paper3") or "").strip()
    p4 = (state.get("paper4") or "").strip()
    paper_name = f"{p3} {p4}".strip() if p4 else p3
    try:
        qty = int(state.get("qty_val") or "")
    except (TypeError, ValueError):
        qty = 0
    size_text = (state.get("size_text") or "").strip()
    # "원형45" / "원형55" 등 — 숫자 이전 부분이 shape
    shape = re.sub(r"\d.*$", "", size_text).strip()
    return {
        "paper_name": paper_name,
        "color_text": (state.get("color_text") or "").strip(),
        "qty":        qty,
        "size_text":  size_text,
        "shape":      shape,
    }


def save_screenshot(page: Page, name: str) -> None:
    path = os.path.join(OUTPUT_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=False)
    print(f"  [screenshot] {path}")


# ─── 기본 옵션 설정 ─────────────────────────────────────────────────────────────

def setup_base_options(page: Page, paper_name: str | None = None) -> None:
    """
    용지(paper_name 지정 시 그 용지 유지, 없으면 첫 옵션) / 단면칼라 / 1000매 / 1건 고정.
    reqMdmDetail/getTemplate 호출 후 이 옵션들이 초기화될 수 있어 사이즈 변경 후에도 재호출해야 함.
    """
    # 용지 paperno3: paper_name 텍스트 매칭 (없으면 첫 옵션)
    page.evaluate(r"""([paperName]) => {
        const sel = document.getElementById('spdata_00_paperno3');
        if (!sel || sel.options.length === 0) return;
        let opt = null;
        if (paperName) {
            opt = [...sel.options].find(o => o.textContent.includes(paperName));
        }
        if (!opt) opt = sel.options[0];
        sel.value = opt.value;
        const oc = sel.getAttribute('onchange');
        if (oc) { try { (new Function('event', oc)).call(sel, null); } catch(e) {} }
    }""", [paper_name])
    time.sleep(1.5)

    # 용지 paperno4: 첫 번째 옵션 선택
    page.evaluate(r"""() => {
        const sel = document.getElementById('spdata_00_paperno4');
        if (sel && sel.options.length > 0) {
            sel.value = sel.options[0].value;
            const oc = sel.getAttribute('onchange');
            if (oc) { try { (new Function('event', oc)).call(sel, null); } catch(e) {} }
        }
    }""")
    time.sleep(1.5)

    # 색도 = 단면칼라 = 302
    set_select_and_trigger(page, "#pdata_00_colorno", "302")
    time.sleep(1.0)

    # 수량 = 1000매
    set_select_and_trigger(page, "#spdata_00_ordqty", "1000")
    time.sleep(1.0)

    # 건수 = 1건
    set_select_and_trigger(page, "#pdata_00_ordcnt", "1")
    time.sleep(1.0)

    # 칼선(sJob0) 선택 해제 — 사이트가 자동으로 '1개(36101)'를 설정해 추가 후가공비가 붙는 이슈 방지.
    # 스티커는 도무송 개수 = 칼선 개수이지만 매출 비교는 '칼선 없음(기본)' 기준.
    page.evaluate(r"""() => {
        const el = document.getElementById('sJob0') || document.querySelector('select[name="sJob0"]');
        if (!el) return;
        el.value = '';
        const oc = el.getAttribute('onchange');
        if (oc) { try { (new Function('event', oc)).call(el, null); } catch(e) {} }
    }""")
    time.sleep(0.8)


# ─── 프리셋 사이즈 크롤링 (40 / 50 / 60) ──────────────────────────────────────

def crawl_preset_size(page: Page, size_mm: int, paper_name: str | None = None) -> dict:
    """
    심플한 재설정 흐름 — 이전 setup_base_options 재호출이 가격 계산을 교란시키는 문제 회피.
    사이즈 변경 후 paperno3/4/color/qty/cnt를 인라인으로 바로 재설정 → fnOrdSummary.
    sJob0(칼선)은 사이트 자동 '1개' 상태를 유지 (해당 가격이 매출 기준과 일치).
    """
    size_value = PRESET_SIZES[size_mm]
    print(f"  [preset] 원형{size_mm}  sizeno={size_value}  paper={paper_name}")

    # 1) 사이즈 변경 (AJAX)
    page.evaluate(
        """([val]) => {
            const el = document.getElementById('pdata_00_sizeno');
            if (!el) return;
            el.value = val;
            if (typeof reqMdmDetail === 'function') {
                reqMdmDetail('Size', val, '0', 'hdata_00_sizeno');
            }
        }""",
        [size_value],
    )
    time.sleep(4)

    # 2) paperno3: paper loop에서 이미 설정했으므로 크롤_preset_size 안에서는 재설정 안 함.
    #    (재설정 시 onchange cascade로 가격이 꼬임)

    # 3) 색도 / 수량 / 건수 인라인 재설정
    set_select_and_trigger(page, "#pdata_00_colorno", "302")
    time.sleep(0.5)
    set_select_and_trigger(page, "#spdata_00_ordqty", "1000")
    time.sleep(0.5)
    set_select_and_trigger(page, "#pdata_00_ordcnt", "1")
    time.sleep(0.8)

    # 5) 가격 계산 트리거
    page.evaluate("() => { if (typeof fnOrdSummary === 'function') fnOrdSummary(); }")
    time.sleep(2)

    price = get_price(page)
    if price == 0:
        print(f"  [WARN] 가격 0원 - 스크린샷 저장")
        save_screenshot(page, f"error_preset_{size_mm}")

    dom = read_dom_state(page)
    print(f"    가격: {price:,}원  |  DOM: {dom['paper_name']!r} / {dom['color_text']!r} / qty={dom['qty']} / size={dom['size_text']!r}")
    return {"price": price, "dom": dom}


# ─── 비규격 사이즈 크롤링 ─────────────────────────────────────────────────────

def crawl_irregular_size(page: Page, size_mm: int, paper_name: str | None = None) -> dict:
    """
    비규격(6066) 선택 → hdata_00_sizeno_x/y/xx/yy 직접 입력 → fnOrdSummary() 호출.
    작업 사이즈 = 재단 + BLEED(5mm).
    """
    size_str  = f"{size_mm}x{size_mm}"
    work_size = size_mm + BLEED
    print(f"  [irregular] {size_str}  작업={work_size}x{work_size}")

    # 1) 비규격 선택 + reqMdmDetail 트리거
    page.evaluate(
        """() => {
            const el = document.getElementById('pdata_00_sizeno');
            if (!el) return;
            el.value = '6066';
            if (typeof reqMdmDetail === 'function') {
                reqMdmDetail('Size', '6066', '0', 'hdata_00_sizeno');
            }
        }"""
    )
    time.sleep(3)

    # 2) 재단/작업 사이즈 입력
    page.evaluate(
        """([cut, work]) => {
            const set = (id, v) => {
                const el = document.getElementById(id);
                if (el) el.value = String(v);
            };
            set('hdata_00_sizeno_x',  cut);   // 재단 가로
            set('hdata_00_sizeno_y',  cut);   // 재단 세로
            set('hdata_00_sizeno_xx', work);  // 작업 가로
            set('hdata_00_sizeno_yy', work);  // 작업 세로
        }""",
        [size_mm, work_size],
    )
    time.sleep(0.5)

    # 3) paperno3/paperno4: paper loop에서 이미 설정됨. 여기선 재설정하지 않음
    #    (재설정 시 onchange cascade로 가격이 꼬임)
    set_select_and_trigger(page, "#pdata_00_colorno", "302")
    time.sleep(0.5)
    set_select_and_trigger(page, "#spdata_00_ordqty", "1000")
    time.sleep(0.5)
    set_select_and_trigger(page, "#pdata_00_ordcnt", "1")
    time.sleep(0.8)

    # 4) 가격 계산 트리거
    page.evaluate("() => { if (typeof fnOrdSummary === 'function') fnOrdSummary(); }")
    time.sleep(3)

    price = get_price(page)
    if price == 0:
        print(f"  [WARN] 가격 0원 - 스크린샷 저장")
        save_screenshot(page, f"error_irregular_{size_mm}")

    dom = read_dom_state(page)
    print(f"    가격: {price:,}원  |  DOM: {dom['paper_name']!r} / {dom['color_text']!r} / qty={dom['qty']} / size={dom['size_text']!r}")
    return {"price": price, "dom": dom}


# ─── 아이템 빌더 ────────────────────────────────────────────────────────────────

def build_item(product_name: str, url: str, size_str: str, price: int, dom: dict | None) -> dict:
    """raw 아이템 빌더. 가격/사이즈/URL 외 모든 필드는 DOM 실측값을 사용.

    size_str 은 크롤 로직에서 결정된 재단 사이즈 표기("45x45" 등)를 사용 —
    DOM sizeno 의 "원형45" 라벨은 options.size_dom_label 로 보존.

    raw 필드 원칙: 사이트가 제시하지 않는 필드는 "" 또는 null.
      - wowpress 스티커는 별도 coating select 가 없어 paper_name 안에 "(유광코팅)"
        형태로 인코딩됨 → raw.coating = "" (normalize 에서 괄호 추출).
    """
    dom = dom or {}
    qty_dom = int(dom.get("qty") or 0)
    return {
        "product":    product_name,
        "category":   "스티커",
        "paper_name": dom.get("paper_name") or None,    # DOM, 없으면 null
        "coating":    None,                               # 별도 coating select 없음 → null
        "print_mode": dom.get("color_text") or None,    # DOM, 없으면 null
        "size":       size_str,
        "qty":        qty_dom or None,                   # DOM 값, 없으면 null
        "price":      price,
        "price_vat_included": True,
        "url":        url,
        "url_ok":     price > 0,
        "options": {
            "shape":          dom.get("shape") or "",
            "ea_per_sheet":   1,
            "size_dom_label": dom.get("size_text") or "",  # DOM sizeno 라벨 (추적용)
        },
    }


# ─── DOM 덤프 (디버깅) ──────────────────────────────────────────────────────────

def dump_dom(page: Page) -> None:
    """select / input 요소 전체를 output/wowpress_sticker_dom.json에 저장"""
    dom_data = page.evaluate(
        """() => {
            const result = { selects: [], inputs: [] };
            document.querySelectorAll('select').forEach(el => {
                result.selects.push({
                    id: el.id, name: el.name,
                    onchange: el.getAttribute('onchange'),
                    display: window.getComputedStyle(el).display,
                    value: el.value,
                    options: Array.from(el.options).map(o => ({
                        value: o.value, text: o.text.trim()
                    }))
                });
            });
            document.querySelectorAll('input').forEach(el => {
                result.inputs.push({
                    id: el.id, name: el.name, type: el.type,
                    value: el.value,
                    onchange: el.getAttribute('onchange'),
                    display: window.getComputedStyle(el).display
                });
            });
            return result;
        }"""
    )
    path = os.path.join(OUTPUT_DIR, "wowpress_sticker_dom.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dom_data, f, ensure_ascii=False, indent=2)
    print(f"[DOM dump] {path}")



_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "sticker_targets.json"


def _load_targets() -> list[dict]:
    if not _CONFIG_PATH.exists():
        return []
    cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    return cfg.get("wowpress", [])


# ─── 메인 크롤러 ────────────────────────────────────────────────────────────────

def crawl_all(headless: bool = True) -> list[dict]:
    """
    sticker_targets.json wowpress 섹션의 전체 제품 × 용지 × 사이즈 크롤링.
    여러 제품 페이지(ProdNo)를 순회.
    """
    ensure_output_dir()
    items: list[dict] = []
    targets = _load_targets()

    if not targets:
        print("크롤 타겟 없음")
        return items

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        for ti, t in enumerate(targets):
            product_url = t["url"]
            product_name = t["product_name"]
            print(f"\n[{ti+1}/{len(targets)}] {product_name}: {product_url}")

            page.goto(product_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)

            if ti == 0:
                dump_dom(page)

            for pi, paper in enumerate(t.get("papers", [{}])):
                paper_name = paper.get("name", product_name)
                print(f"\n  용지: {paper_name}")

                # 각 paper iteration마다 페이지 새로 로드 (초기 상태 = 페이지 기본 용지)
                if pi > 0:
                    page.goto(product_url, wait_until="domcontentloaded", timeout=30000)
                    time.sleep(3)

                # 페이지 현재 paperno3 값 확인
                current_text = page.evaluate(r"""() => {
                    const el = document.getElementById('spdata_00_paperno3');
                    return el ? (el.options[el.selectedIndex]?.textContent?.trim() || '') : '';
                }""") or ""

                # paper_name이 현재 선택된 기본 용지면 paperno3 건드리지 않음
                # (wowpress 내부 상태와 DOM value가 분리되어 있어서 변경해도 가격 반영 안 되는 문제 회피)
                if paper_name == current_text or paper_name in current_text or current_text in paper_name:
                    print(f"    paperno3 기본값 유지: {current_text}")
                else:
                    # 기본값 아닌 경우에만 paperno3 변경 시도
                    page.evaluate(r"""([paperText]) => {
                        const sel = document.getElementById('spdata_00_paperno3');
                        if (!sel) return;
                        const opt = [...sel.options].find(o => o.textContent.includes(paperText));
                        if (opt) {
                            sel.value = opt.value;
                            const oc = sel.getAttribute('onchange');
                            if (oc) { try { (new Function('event', oc)).call(sel, null); } catch(e) {} }
                        }
                    }""", [paper_name])
                    time.sleep(2.5)
                    actual = page.evaluate(r"""() => {
                        const el = document.getElementById('spdata_00_paperno3');
                        return el ? {val: el.value, text: el.options[el.selectedIndex]?.textContent?.trim()} : null;
                    }""") or {}
                    print(f"    paperno3 변경: {actual.get('text')} ({actual.get('val')})")

                # paperno4 첫 번째 옵션 자동 선택
                page.evaluate(r"""() => {
                    const sel = document.getElementById('spdata_00_paperno4');
                    if (sel && sel.options.length > 0) {
                        sel.value = sel.options[0].value;
                        const oc = sel.getAttribute('onchange');
                        if (oc) { try { (new Function('event', oc)).call(sel, null); } catch(e) {} }
                    }
                }""")
                time.sleep(1.0)

                # 색도 + 수량 + 건수
                set_select_and_trigger(page, "#pdata_00_colorno", "302")
                time.sleep(0.5)
                set_select_and_trigger(page, "#spdata_00_ordqty", "1000")
                time.sleep(0.5)
                set_select_and_trigger(page, "#pdata_00_ordcnt", "1")
                time.sleep(1.0)

                # 프리셋 사이즈
                for size_info in t.get("preset_sizes", []):
                    size_name = size_info["name"]
                    size_value = PRESET_SIZES.get(int(size_name.split("x")[0]))

                    if size_value:
                        result = crawl_preset_size(page, int(size_name.split("x")[0]), paper_name)
                        item = build_item(product_name, product_url, size_name,
                                          result.get("price", 0), result.get("dom"))
                    else:
                        item = build_item(product_name, product_url, size_name, 0, None)

                    if item["price"] > 0:
                        items.append(item)

                # 비규격 사이즈
                for size_info in t.get("custom_sizes", []):
                    size_mm = size_info["mm"]
                    size_name = size_info["name"]
                    result = crawl_irregular_size(page, size_mm, paper_name)
                    item = build_item(product_name, product_url, size_name,
                                      result.get("price", 0), result.get("dom"))
                    if item["price"] > 0:
                        items.append(item)

        browser.close()

    return items


# ─── 저장 ───────────────────────────────────────────────────────────────────────

def save(items: list[dict]) -> None:
    """output/wowpress_sticker_raw_now.json 에 저장"""
    ensure_output_dir()
    now    = datetime.now().strftime("%Y-%m-%d %H:%M")
    output = {
        "company":    COMPANY,
        "crawled_at": now,
        "items":      items,
    }
    path = os.path.join(OUTPUT_DIR, "wowpress_sticker_raw_now.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*50}")
    print(f"[저장 완료] {path}")
    print(f"  총 {len(items)}개 아이템  |  crawled_at: {now}")
    print(f"{'─'*50}")
    for it in items:
        mark = "OK" if it["url_ok"] else "!!"
        paper = it.get("paper_name", "")
        pm = it.get("print_mode", "")
        print(f"  {mark}  {it['size']:8s}  {paper[:30]:30s}  {pm[:10]:10s}  qty={it['qty']:>5}  {it['price']:>8,}원")
    print(f"{'='*50}")


# ─── 진입점 ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    # --show 옵션: 브라우저 창 표시 (디버깅용)
    headless = "--show" not in sys.argv
    print(f"=== WowpressStickerCrawler 시작 (headless={headless}) ===\n")
    items = crawl_all(headless=headless)
    save(items)