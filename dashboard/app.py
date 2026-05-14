"""
경쟁사 가격 모니터링 대시보드.
- 스케줄러가 생성한 normalize_now.json을 그대로 읽어 표시.
- 값 변환 로직 없음 (정규화는 스케줄러가 담당).
- 카테고리별 가격 비교 + 변동 감지.

카테고리:
  card_offset / card_digital / flyer / envelope / sticker — 신규 schema (config/schemas/*.yaml + config/sites/*.yaml)
"""
import json
import os
import sys
import threading
import time
from datetime import datetime

import yaml
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

CONFIG_DIR = os.path.join(BASE_DIR, "config")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

# 모든 카테고리가 신규 schema (config/schemas/*.yaml) 사용. legacy 분기 더 이상 없음.
LEGACY_RULE_PATHS = {}

# 신규 카드 카테고리의 사이트 list (config/sites/*.yaml 에서 읽음)
CARD_SITES = ["printcity", "dtpia", "swadpia", "wowpress", "adsland"]

CATEGORIES = [
    {"id": "card_offset", "name": "명함 (오프셋)"},
    {"id": "card_digital", "name": "명함 (디지털)"},
    {"id": "flyer", "name": "합판 전단"},
    {"id": "envelope", "name": "봉투"},
    {"id": "sticker_offset", "name": "스티커 (오프셋)"},
    {"id": "sticker_digital", "name": "스티커 (디지털)"},
]

app = Flask(__name__)


# ── 상태 ──
crawl_status = {"running": False, "current": "", "elapsed_sec": 0, "errors": [], "category": ""}
status_lock = threading.Lock()
_crawl_start_time = None


def load_json(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def update_status(**kwargs):
    with status_lock:
        crawl_status.update(kwargs)


# ── 사이트 목록 ──
def _load_site_yaml(site_id: str) -> dict:
    path = os.path.join(CONFIG_DIR, "sites", f"{site_id}.yaml")
    if not os.path.exists(path): return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_active_sites(category: str) -> list[dict]:
    """카테고리별 활성 사이트 list — 모두 config/sites/*.yaml 의 5사이트."""
    if category in ("card_offset", "card_digital", "flyer", "envelope", "sticker_offset", "sticker_digital"):
        out = []
        for sid in CARD_SITES:
            site_cfg = _load_site_yaml(sid)
            if not site_cfg: continue
            out.append({
                "id": sid,
                "name": site_cfg.get("name", sid),
                "base_url": site_cfg.get("base_url", ""),
                "ownership": site_cfg.get("ownership", "competitor"),
                "vat_adjusted": False,
            })
        return out
    return []


def _side(print_mode: str) -> str | None:
    if print_mode.startswith("양면"):
        return "양면"
    if print_mode.startswith("단면"):
        return "단면"
    return None


# 명함 grid 표시 순서는 동적 — 다사이트 매칭 paper 우선, 그 다음 record 많은 순.


# ── 공통: 사이트별 데이터 로드 ──
def _load_site_data(category: str, site_ids: list[str]):
    items_by_site = {}
    raw_items_by_site = {}
    latest_crawled = None
    for sid in site_ids:
        data = load_json(os.path.join(OUTPUT_DIR, f"{sid}_{category}_normalize_now.json"))
        items_by_site[sid] = data.get("items", []) if data else []
        if data and data.get("crawled_at"):
            if latest_crawled is None or data["crawled_at"] > latest_crawled:
                latest_crawled = data["crawled_at"]
        raw_data = load_json(os.path.join(OUTPUT_DIR, f"{sid}_{category}_raw_now.json"))
        raw_items_by_site[sid] = raw_data.get("items", []) if raw_data else []
    return items_by_site, raw_items_by_site, latest_crawled


def _find_raw_paper_name(matching, items_by_site_sid, raw_items_by_site_sid):
    """normalize item index → raw item의 같은 index에서 원래 용지명."""
    if not matching:
        return ""
    for norm_idx, nit in enumerate(items_by_site_sid):
        if nit is matching[0]:
            if norm_idx < len(raw_items_by_site_sid):
                return raw_items_by_site_sid[norm_idx].get("paper_name", "")
            break
    return ""


# ── 명함 grid ──
def _build_card_grid(sites, site_ids, items_by_site, raw_items_by_site, latest_crawled,
                    qtys=None):
    """카드 그리드 빌드 (offset / digital 공통).

    그룹핑 키 = (paper_name, coating). raw 매칭은 paper_name 만 (match_as 미사용).
    표시 순서: 다사이트 매칭 paper 우선 + record 많은 순.
    """
    qtys = qtys or [100, 200, 500, 1000]
    sides = ["단면", "양면"]

    # 1) 모든 (paper_name, coating) 키 + 사이트 set / record count 수집
    key_sites = {}    # (paper, coating) → set(sid)
    key_records = {}  # (paper, coating) → 총 record 수
    for sid in site_ids:
        for it in items_by_site[sid]:
            pn = it.get("paper_name", "")
            coat = it.get("coating", "")
            k = (pn, coat)
            key_sites.setdefault(k, set()).add(sid)
            key_records[k] = key_records.get(k, 0) + 1
    # 2) 정렬 — 다사이트 매칭 우선, 동수면 record 많은 순, 그 다음 paper 이름
    def _sort_key(k):
        n_sites = len(key_sites[k])
        return (-n_sites, -key_records[k], k[0], k[1])
    sorted_keys = sorted(key_sites.keys(), key=_sort_key)

    # 표시용 qty = 표준 [100,200,500,1000]. 사이트별 매수가 다르면 (예: adsland 96/192/504/1008)
    # 표준 qty 의 ±tolerance 안 가장 가까운 매수의 가격으로 보간 (raw 매수 표시는 메타).
    qtys_to_show = qtys
    QTY_TOLERANCE = 0.10  # ±10% 안 가장 가까운 매수로 보간

    def _closest_qty(target: int, available: set[int]) -> int | None:
        if target in available: return target
        # ±10% 범위 안의 가장 가까운 매수
        candidates = [q for q in available if abs(q - target) / target <= QTY_TOLERANCE]
        if not candidates: return None
        return min(candidates, key=lambda q: abs(q - target))

    papers = []
    for paper_name, coating in sorted_keys:
        label = f"{paper_name} · {coating}" if coating else paper_name
        entry = {"label": label, "paper_name": paper_name, "coating": coating, "sites": {}}
        for site in sites:
            sid = site["id"]
            matching = [
                it for it in items_by_site[sid]
                if it.get("paper_name", "") == paper_name and it.get("coating", "") == coating
            ]
            if not matching:
                entry["sites"][sid] = None
                continue
            url = matching[0].get("url") or site["base_url"]
            url_ok = matching[0].get("url_ok", True)
            prices = {s: {str(q): None for q in qtys_to_show} for s in sides}
            # 보간 정보: target → actual (보간된 매수). target == actual 이면 보간 X
            interp_map = {s: {} for s in sides}
            products_seen = []
            # 사이트의 (side, actual_qty) → price 맵 구성
            by_side_qty = {s: {} for s in sides}
            for it in matching:
                p = it.get("product", "")
                if p and p not in products_seen:
                    products_seen.append(p)
                side = _side(it.get("print_mode", ""))
                if side not in by_side_qty: continue
                q = it.get("qty")
                if q is not None:
                    by_side_qty[side][q] = it.get("price")
            # 각 표준 qty 에 대해 사이트의 매수에서 ±10% 안 가장 가까운 매수의 가격 사용
            site_has_interpolation = False
            interp_pairs = set()  # (target, actual) 보간된 매수 페어
            for side in sides:
                avail = set(by_side_qty[side].keys())
                if not avail: continue
                for target_q in qtys_to_show:
                    cq = _closest_qty(target_q, avail)
                    if cq is not None:
                        prices[side][str(target_q)] = by_side_qty[side][cq]
                        interp_map[side][str(target_q)] = cq
                        if cq != target_q:
                            site_has_interpolation = True
                            interp_pairs.add((target_q, cq))
            raw_paper_name = _find_raw_paper_name(matching, items_by_site[sid], raw_items_by_site.get(sid, []))
            entry["sites"][sid] = {
                "product": " + ".join(products_seen),
                "raw_paper_name": raw_paper_name,
                "url": url, "url_ok": url_ok, "prices": prices,
                "actual_qty": interp_map,  # side → {target_q: actual_q}
            }
        papers.append(entry)

    return {
        "type": "card", "sites": sites, "sides": sides, "qtys": qtys_to_show,
        "papers": papers, "updated_at": latest_crawled,
    }


# ── 합판전단 grid ──
# qty=2000 고정. rows=size (A2/A3/A4/B3/B4), site cell sub-cols=print_mode (단면/양면).

FLYER_SIZES = ["A2", "A3", "A4", "B3", "B4"]
FLYER_PRINT_MODES = ["단면4도", "양면8도"]


def _flyer_print_mode(raw: str) -> str | None:
    """raw print_mode → canonical (단면4도/양면8도)."""
    if not raw: return None
    if "단면" in raw: return "단면4도"
    if "양면" in raw: return "양면8도"
    return None


def _build_flyer_grid(sites, site_ids, items_by_site, raw_items_by_site, latest_crawled):
    """합판전단 그리드. paper 별 카드, 각 카드 안에서 size × print_mode × site 매트릭스."""
    sizes = FLYER_SIZES
    modes = FLYER_PRINT_MODES

    # paper canonical 수집 + 사이트 수 정렬
    key_sites = {}    # paper → set(sid)
    key_records = {}
    for sid in site_ids:
        for it in items_by_site[sid]:
            pn = it.get("paper_name", "")
            key_sites.setdefault(pn, set()).add(sid)
            key_records[pn] = key_records.get(pn, 0) + 1

    def _sort_key(k):
        return (-len(key_sites[k]), -key_records[k], k)
    sorted_papers = sorted(key_sites.keys(), key=_sort_key)

    papers = []
    for paper_name in sorted_papers:
        entry = {"label": paper_name, "paper_name": paper_name, "sites": {}}
        for site in sites:
            sid = site["id"]
            matching = [it for it in items_by_site[sid] if it.get("paper_name", "") == paper_name]
            if not matching:
                entry["sites"][sid] = None
                continue
            url = matching[0].get("url") or site["base_url"]
            url_ok = matching[0].get("url_ok", True)
            prices = {m: {s: None for s in sizes} for m in modes}
            qtys = {m: {s: None for s in sizes} for m in modes}      # 보간 후 표준 qty
            raw_qtys = {m: {s: None for s in sizes} for m in modes}  # 실제 사이트 매수 (보간 전)
            products_seen = []
            for it in matching:
                p = it.get("product", "")
                if p and p not in products_seen:
                    products_seen.append(p)
                m = _flyer_print_mode(it.get("print_mode", ""))
                if m not in prices: continue
                sz = it.get("size", "")
                if sz in sizes:
                    prices[m][sz] = it.get("price")
                    qtys[m][sz] = it.get("qty")
                    raw_qtys[m][sz] = (it.get("options") or {}).get("raw_qty") or it.get("qty")
            raw_paper = _find_raw_paper_name(matching, items_by_site[sid], raw_items_by_site.get(sid, []))
            entry["sites"][sid] = {
                "product": " + ".join(products_seen),
                "raw_paper_name": raw_paper,
                "url": url, "url_ok": url_ok,
                "prices": prices,
                "qtys": qtys,
                "raw_qtys": raw_qtys,
            }
        papers.append(entry)

    return {
        "type": "flyer", "sites": sites,
        "sizes": sizes, "print_modes": modes, "qty": 2000,
        "papers": papers, "updated_at": latest_crawled,
    }


# ── 스티커 grid ──
# 사각형 스티커 표준 8 사이즈 (2026-05~ 정책. 옛 원형 6 사이즈 정책 폐기)
STICKER_SIZES = ["60x40", "80x50", "90x55", "90x60", "90x70", "90x80", "90x100", "90x120"]

def _build_sticker_grid(sites, site_ids, items_by_site, raw_items_by_site, latest_crawled):
    # 용지별 그룹: (paper_name, coating) 키. 카드/전단과 동일 정렬:
    # 다사이트 매칭 paper 우선 + record 많은 순.
    key_sites = {}    # (paper, coating) → set(sid)
    key_records = {}  # (paper, coating) → 총 record 수
    for sid in site_ids:
        for it in items_by_site[sid]:
            key = (it.get("paper_name", ""), it.get("coating", ""))
            key_sites.setdefault(key, set()).add(sid)
            key_records[key] = key_records.get(key, 0) + 1
    def _sort_key(k):
        return (-len(key_sites[k]), -key_records[k], k[0], k[1])
    seen_keys = sorted(key_sites.keys(), key=_sort_key)

    papers = []
    for paper_name, coating in seen_keys:
        label = f"{paper_name} {coating}".strip()
        entry = {"label": label, "paper_name": paper_name, "coating": coating, "sites": {}}
        for site in sites:
            sid = site["id"]
            matching = [
                it for it in items_by_site[sid]
                if it.get("paper_name") == paper_name and it.get("coating") == coating
            ]
            if not matching:
                entry["sites"][sid] = None
                continue
            url = matching[0].get("url") or site["base_url"]
            url_ok = matching[0].get("url_ok", True)
            products_seen = []
            # 사이즈별 가격 + ea_per_sheet 보정
            prices = {}
            for it in matching:
                p = it.get("product", "")
                if p and p not in products_seen:
                    products_seen.append(p)
                size = it.get("size", "")
                ea = it.get("options", {}).get("ea_per_sheet", 1)
                price = it.get("price")
                if price is not None and ea > 1:
                    price = price // ea
                prices[size] = {"price": price, "ea": ea}
            raw_paper_name = _find_raw_paper_name(matching, items_by_site[sid], raw_items_by_site.get(sid, []))
            entry["sites"][sid] = {
                "product": " + ".join(products_seen),
                "raw_paper_name": raw_paper_name,
                "url": url, "url_ok": url_ok, "prices": prices,
            }
        papers.append(entry)

    return {"type": "sticker", "sites": sites, "sizes": STICKER_SIZES, "papers": papers, "updated_at": latest_crawled}


# ── 봉투 grid ──
# 사이즈는 canonical 3종 고정(매출 TOP 순). print_mode는 단면칼라/단면흑백 2종.
ENVELOPE_SIZES = ["대봉투", "9절봉투", "소봉투"]
# 봉투는 단면4도(칼라) 만 수집. 흑백/무지/색지/양면 봉투는 정책상 제외.
ENVELOPE_PRINT_MODES = ["단면4도"]

# 용지 표시 순서 — 매출 TOP 우선. canonical name + 평량 (normalize 결과 형식).
ENVELOPE_PAPER_ORDER = [
    "모조 100g", "모조 120g", "모조 150g", "모조 180g",
    "미색모조 100g",
    "크라프트 98g", "크라프트 100g", "크라프트 120g",
    "레자크 110g", "레자크 120g", "레자크 140g", "레자크 150g", "레자크 200g",
    "랑데뷰 105g", "랑데뷰 130g", "랑데뷰 160g",
    "밍크 120g", "탄트 120g", "레이드 110g", "레이드 120g",
    "스타드림 120g", "화일지 120g", "뉴에코 120g", "뉴에코 150g",
]


def _build_envelope_grid(sites, site_ids, items_by_site, raw_items_by_site, latest_crawled):
    # 키 = (paper_name, print_mode). 카드/전단/스티커와 동일 정렬:
    # 다사이트 매칭 paper 우선 + record 많은 순.
    key_sites = {}    # (paper, pm) → set(sid)
    key_records = {}  # (paper, pm) → 총 record 수
    for sid in site_ids:
        for it in items_by_site[sid]:
            key = (it.get("paper_name", ""), it.get("print_mode", ""))
            key_sites.setdefault(key, set()).add(sid)
            key_records[key] = key_records.get(key, 0) + 1
    def _sort_key(k):
        return (-len(key_sites[k]), -key_records[k], k[0], k[1])
    seen_keys = sorted(key_sites.keys(), key=_sort_key)

    papers = []
    for paper_name, print_mode in seen_keys:
        label = f"{paper_name} · {print_mode}" if print_mode else paper_name
        entry = {"label": label, "paper_name": paper_name, "print_mode": print_mode, "sites": {}}
        for site in sites:
            sid = site["id"]
            matching = [
                it for it in items_by_site[sid]
                if it.get("paper_name") == paper_name and it.get("print_mode") == print_mode
            ]
            if not matching:
                entry["sites"][sid] = None
                continue
            url = matching[0].get("url") or site["base_url"]
            url_ok = matching[0].get("url_ok", True)
            products_seen = []
            # 사이즈별 가격
            prices = {}
            for it in matching:
                p = it.get("product", "")
                if p and p not in products_seen:
                    products_seen.append(p)
                size = it.get("size", "")
                prices[size] = {"price": it.get("price")}
            raw_paper_name = _find_raw_paper_name(matching, items_by_site[sid], raw_items_by_site.get(sid, []))
            entry["sites"][sid] = {
                "product": " + ".join(products_seen),
                "raw_paper_name": raw_paper_name,
                "url": url, "url_ok": url_ok, "prices": prices,
            }
        papers.append(entry)

    return {"type": "envelope", "sites": sites, "sizes": ENVELOPE_SIZES, "papers": papers, "updated_at": latest_crawled}


# ── grid API ──
@app.route("/api/data/grid")
def api_grid():
    category = request.args.get("category", "card_offset")
    sites = get_active_sites(category)
    site_ids = [s["id"] for s in sites]
    items_by_site, raw_items_by_site, latest_crawled = _load_site_data(category, site_ids)

    if category in ("sticker_offset", "sticker_digital"):
        return jsonify(_build_sticker_grid(sites, site_ids, items_by_site, raw_items_by_site, latest_crawled))
    if category == "envelope":
        return jsonify(_build_envelope_grid(sites, site_ids, items_by_site, raw_items_by_site, latest_crawled))
    if category == "flyer":
        return jsonify(_build_flyer_grid(sites, site_ids, items_by_site, raw_items_by_site, latest_crawled))
    # card_offset / card_digital (둘 다 카드 그리드)
    return jsonify(_build_card_grid(sites, site_ids, items_by_site, raw_items_by_site, latest_crawled))


# ── 변동 감지 API ──
def _match_key(item: dict) -> tuple:
    return (
        item.get("paper_name", ""),
        item.get("coating", ""),
        item.get("print_mode", ""),
        item.get("size", ""),
        item.get("qty", 0),
    )


@app.route("/api/data/changes")
def api_changes():
    category = request.args.get("category", "card_offset")
    sites = get_active_sites(category)
    site_names = {s["id"]: s["name"] for s in sites}

    changes = []
    past_time = now_time = None
    for site in sites:
        sid = site["id"]
        past_data = load_json(os.path.join(OUTPUT_DIR, f"{sid}_{category}_normalize_past.json"))
        now_data = load_json(os.path.join(OUTPUT_DIR, f"{sid}_{category}_normalize_now.json"))
        if not past_data or not now_data:
            continue
        if now_data.get("crawled_at") and (now_time is None or now_data["crawled_at"] > now_time):
            now_time = now_data["crawled_at"]
        if past_data.get("crawled_at") and (past_time is None or past_data["crawled_at"] > past_time):
            past_time = past_data["crawled_at"]
        past = {_match_key(it): it for it in past_data.get("items", [])}
        now = {_match_key(it): it for it in now_data.get("items", [])}
        for k, now_it in now.items():
            past_it = past.get(k)
            if not past_it:
                continue
            pp, np_ = past_it.get("price"), now_it.get("price")
            if not (pp and np_) or pp == np_:
                continue
            changes.append({
                "company": sid, "company_name": site_names.get(sid, sid),
                "paper_name": k[0], "coating": k[1], "print_mode": k[2],
                "size": k[3], "qty": k[4],
                "past_price": pp, "now_price": np_, "diff": np_ - pp,
                "pct": round((np_ - pp) / pp * 100, 2),
                "direction": "up" if np_ > pp else "down",
            })
    changes.sort(key=lambda c: abs(c["pct"]), reverse=True)
    return jsonify({"past_time": past_time, "now_time": now_time, "total": len(changes), "changes": changes})


# ── 크롤 트리거 ──
def run_crawl(category: str):
    global _crawl_start_time
    _crawl_start_time = time.time()
    update_status(running=True, current=f"{category} 파이프라인 실행 중...", elapsed_sec=0, errors=[], category=category)
    try:
        from scheduler import run_category
        run_category(category)
    except Exception as e:
        with status_lock:
            crawl_status["errors"].append(str(e))
    total = int(time.time() - _crawl_start_time)
    update_status(running=False, current="완료", elapsed_sec=total)


@app.route("/")
def index():
    return render_template("index.html", categories=CATEGORIES)


@app.route("/api/categories")
def api_categories():
    return jsonify(CATEGORIES)


@app.route("/api/status")
def api_status():
    with status_lock:
        return jsonify(crawl_status)


@app.route("/api/start", methods=["POST"])
def api_start():
    if crawl_status["running"]:
        return jsonify({"error": "이미 실행 중"}), 409
    data = request.get_json(silent=True) or {}
    category = data.get("category", "card")
    threading.Thread(target=run_crawl, args=(category,), daemon=True).start()
    return jsonify({"status": "started", "category": category})


@app.route("/stream")
def stream():
    def generate():
        while True:
            with status_lock:
                running = crawl_status["running"]
                current = crawl_status["current"]
                elapsed = crawl_status.get("elapsed_sec", 0)
                cat = crawl_status.get("category", "")
            if running and _crawl_start_time:
                elapsed = int(time.time() - _crawl_start_time)
            yield f"data: {json.dumps({'running': running, 'current': current, 'elapsed_sec': elapsed, 'category': cat}, ensure_ascii=False)}\n\n"
            if not running and current == "완료":
                yield f"data: {json.dumps({'done': True, 'elapsed_sec': elapsed, 'category': cat})}\n\n"
                break
            time.sleep(0.5)
    return Response(stream_with_context(generate()), mimetype="text/event-stream")


if __name__ == "__main__":
    os.chdir(BASE_DIR)
    app.run(host="0.0.0.0", port=5001, threaded=True)
