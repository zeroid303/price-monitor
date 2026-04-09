"""
경쟁사 가격 모니터링 대시보드
Flask + SSE (Server-Sent Events) 기반
"""
import os
import sys
import json
import threading
import time
from datetime import datetime
from flask import Flask, render_template, jsonify, Response, stream_with_context

# 크롤러 import를 위한 경로 설정
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

app = Flask(__name__)

# ── 상태 관리 ──
crawl_status = {
    "running": False,
    "progress": 0,
    "total": 0,
    "current": "",
    "results": {},
    "errors": [],
    "started_at": None,
    "finished_at": None,
    "elapsed_sec": 0,
    "estimated_remaining_sec": 0,
}
status_lock = threading.Lock()

# 이전 크롤링 소요 시간 (그룹별, 초)
# 업데이트할 때마다 갱신됨
_last_durations = {
    "group1": 480,  # 명함천국+비즈하우스 병렬 ~8분
    "group2": 180,  # 성원+네이플 병렬 ~3분
    "total": 660,   # 합계 ~11분
}
_crawl_start_time = None

# ── 경로 설정 ──
CONFIG_DIR = os.path.join(BASE_DIR, "config")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
WHITELIST_PATH = os.path.join(CONFIG_DIR, "crawl_whitelist.json")
REFERENCE_PATH = os.path.join(CONFIG_DIR, "card_reference.json")
ECARD_OUTPUT = os.path.join(OUTPUT_DIR, "ecard21_whitelist_prices.json")
TIMESTAMP_PATH = os.path.join(OUTPUT_DIR, "crawl_timestamp.json")


def load_json(path):
    """JSON 파일 로드"""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def update_status(**kwargs):
    with status_lock:
        crawl_status.update(kwargs)


# ── now/past 파일 관리 ──
COMPETITORS = ["bizhows", "swadpia", "naple", "printcity", "dtpia"]

def _now_path(company):
    return os.path.join(OUTPUT_DIR, f"{company}_card_now.json")

def _past_path(company):
    return os.path.join(OUTPUT_DIR, f"{company}_card_past.json")

def rotate_output(company):
    """크롤링 시작 전: now → past 로테이션"""
    now = _now_path(company)
    past = _past_path(company)
    if os.path.exists(now):
        import shutil
        shutil.copy2(now, past)

def save_competitor_output(company, data):
    """크롤링 완료 후: 결과를 now로 저장"""
    now = _now_path(company)
    with open(now, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_now(company):
    return load_json(_now_path(company))

def load_past(company):
    return load_json(_past_path(company))


# ── 개별 크롤러 실행 함수 ──
def _crawl_ecard21(results):
    """명함천국 크롤링"""
    try:
        os.chdir(BASE_DIR)
        from crawlers.Ecard21Crawler import Ecard21WhitelistCrawler
        crawler = Ecard21WhitelistCrawler(headless=True)
        try:
            crawler.crawl(WHITELIST_PATH)
            crawler.save_json(ECARD_OUTPUT)
            success = sum(1 for r in crawler.results if r.price is not None)
            results["ecard21"] = {"success": success, "total": len(crawler.results)}
        finally:
            crawler.close()
    except Exception as e:
        crawl_status["errors"].append(f"명함천국: {e}")
        results["ecard21"] = {"error": str(e)}


def _crawl_bizhows(results):
    """비즈하우스 크롤링 (크롤러 자체가 past/now 저장)"""
    try:
        os.chdir(BASE_DIR)
        from crawlers.BizhowsCardCrawler import BizhowsCrawler
        bz = BizhowsCrawler(headless=True)
        bz.run()  # 내부에서 now→past 로테이션 + now 저장
        success = sum(1 for r in bz.results if "error" not in r and r.get("price"))
        results["bizhows"] = {"success": success, "total": len(bz.results)}
    except Exception as e:
        crawl_status["errors"].append(f"비즈하우스: {e}")
        results["bizhows"] = {"error": str(e)}


def _crawl_swadpia(results):
    """성원애드피아 크롤링 (크롤러 자체가 past/now 저장)"""
    try:
        os.chdir(BASE_DIR)
        from crawlers.SwadpiaCardCrawler import main as swadpia_main
        swadpia_main()  # 내부에서 now→past 로테이션 + now 저장
        results["swadpia"] = {"success": True}
    except Exception as e:
        crawl_status["errors"].append(f"성원애드피아: {e}")
        results["swadpia"] = {"error": str(e)}


def _crawl_naple(results):
    """네이플 크롤링 (크롤러 자체가 past/now 저장)"""
    try:
        os.chdir(BASE_DIR)
        from crawlers.NapleCardCrawler import crawl_all as naple_crawl, save_results as naple_save
        naple_results = naple_crawl()
        naple_save(naple_results)  # 내부에서 now→past 로테이션 + now 저장
        results["naple"] = {"success": len(naple_results)}
    except Exception as e:
        crawl_status["errors"].append(f"네이플: {e}")
        results["naple"] = {"error": str(e)}


# ── 크롤링 실행 (백그라운드 스레드, 병렬) ──
def run_crawl_all():
    """명함천국 + 경쟁사 병렬 크롤링
    그룹1 (브라우저 무거운 것): 명함천국 + 비즈하우스 병렬
    그룹2 (브라우저 가벼운 것): 성원애드피아 + 네이플 병렬
    그룹1 완료 후 그룹2 실행 (메모리 절약)
    """
    global _crawl_start_time
    _crawl_start_time = time.time()

    # 타임스탬프 로테이션: now → past
    ts = load_json(TIMESTAMP_PATH) or {"past": None, "now": None}
    ts["past"] = ts.get("now")
    ts["now"] = None  # 크롤링 완료 후 기록
    with open(TIMESTAMP_PATH, "w", encoding="utf-8") as f:
        json.dump(ts, f, ensure_ascii=False, indent=2)

    update_status(running=True, progress=0, total=4, current="준비 중...",
                  errors=[], started_at=datetime.now().isoformat(),
                  elapsed_sec=0, estimated_remaining_sec=_last_durations["total"])

    results = {}

    # 그룹1: 명함천국 + 비즈하우스 병렬
    g1_start = time.time()
    update_status(progress=0, current="명함천국 + 비즈하우스 병렬 크롤링 중...")
    t1 = threading.Thread(target=_crawl_ecard21, args=(results,))
    t2 = threading.Thread(target=_crawl_bizhows, args=(results,))
    t1.start()
    t2.start()
    t1.join()
    elapsed = int(time.time() - _crawl_start_time)
    update_status(progress=1, current="명함천국 완료, 비즈하우스 진행 중...",
                  elapsed_sec=elapsed,
                  estimated_remaining_sec=max(0, _last_durations["total"] - elapsed))
    t2.join()
    g1_dur = int(time.time() - g1_start)
    elapsed = int(time.time() - _crawl_start_time)
    update_status(progress=2, current="비즈하우스 완료",
                  elapsed_sec=elapsed,
                  estimated_remaining_sec=max(0, _last_durations["group2"]))

    # 그룹2: 성원애드피아 + 네이플 병렬
    g2_start = time.time()
    update_status(progress=2, current="성원애드피아 + 네이플 병렬 크롤링 중...")
    t3 = threading.Thread(target=_crawl_swadpia, args=(results,))
    t4 = threading.Thread(target=_crawl_naple, args=(results,))
    t3.start()
    t4.start()
    t3.join()
    t4.join()
    g2_dur = int(time.time() - g2_start)
    total_dur = int(time.time() - _crawl_start_time)

    # 소요 시간 기록 (다음 실행 예상치 갱신)
    _last_durations["group1"] = g1_dur
    _last_durations["group2"] = g2_dur
    _last_durations["total"] = total_dur

    # 타임스탬프: now 기록
    ts = load_json(TIMESTAMP_PATH) or {"past": None, "now": None}
    ts["now"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(TIMESTAMP_PATH, "w", encoding="utf-8") as f:
        json.dump(ts, f, ensure_ascii=False, indent=2)

    update_status(running=False, progress=4, current="완료",
                  results=results, finished_at=datetime.now().isoformat(),
                  elapsed_sec=total_dur, estimated_remaining_sec=0)


# ── 라우트 ──
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    """크롤링 상태 조회"""
    with status_lock:
        return jsonify(crawl_status)


@app.route("/api/start", methods=["POST"])
def api_start():
    """크롤링 시작"""
    if crawl_status["running"]:
        return jsonify({"error": "이미 실행 중"}), 409
    thread = threading.Thread(target=run_crawl_all, daemon=True)
    thread.start()
    return jsonify({"status": "started"})


@app.route("/api/data/ecard21")
def api_ecard21():
    """명함천국 최신 크롤링 결과"""
    data = load_json(ECARD_OUTPUT)
    return jsonify(data) if data else jsonify({"error": "데이터 없음"})


@app.route("/api/data/bizhows")
def api_bizhows():
    """비즈하우스 최신 크롤링 결과"""
    data = get_latest_output("bizhows_card_")
    return jsonify(data) if data else jsonify({"error": "데이터 없음"})


@app.route("/api/data/swadpia")
def api_swadpia():
    """성원애드피아 최신 크롤링 결과"""
    data = get_latest_output("swadpia_card_")
    return jsonify(data) if data else jsonify({"error": "데이터 없음"})


@app.route("/api/data/naple")
def api_naple():
    """네이플 최신 크롤링 결과"""
    data = get_latest_output("naple_card_")
    return jsonify(data) if data else jsonify({"error": "데이터 없음"})


@app.route("/api/data/reference")
def api_reference():
    """card_reference 매칭 데이터"""
    data = load_json(REFERENCE_PATH)
    return jsonify(data) if data else jsonify({"error": "데이터 없음"})


@app.route("/api/data/comparison")
def api_comparison():
    """매칭된 가격 비교 데이터 (대시보드 테이블용)"""
    ref = load_json(REFERENCE_PATH)
    ecard = load_json(ECARD_OUTPUT)
    bizhows = load_now("bizhows")
    swadpia = load_now("swadpia")
    naple = load_now("naple")
    printcity = load_now("printcity")
    dtpia = load_now("dtpia")

    if not ref:
        return jsonify({"error": "reference 없음"})

    def build_price_map(data):
        """통일 구조에서 (paper_name, product) → {price, qty, vat} 맵 빌드.
        paper_name 단독 키도 유지 (product 매칭 실패 시 폴백)."""
        prices = {}
        if not data:
            return prices
        items = data.get("items", []) if isinstance(data, dict) else data
        for r in items:
            paper = r.get("paper_name", "")
            price = r.get("price")
            if not paper or not price:
                continue
            qty = r.get("qty", 200)
            # 200매 미만 제외 (비즈하우스 22매/100매 특가)
            if isinstance(qty, int) and qty < 200:
                continue
            entry = {"price": price, "qty": qty, "vat": r.get("price_vat_included", True)}
            product = r.get("product", "")
            # (paper_name, product) 복합키
            combo_key = (paper, product)
            if combo_key not in prices:
                prices[combo_key] = entry
            # paper_name 단독 키 (폴백용, 첫 번째 값만)
            if paper not in prices:
                prices[paper] = entry
        return prices

    # 각 사별 가격 맵
    bz_prices = build_price_map(bizhows)
    sw_prices = build_price_map(swadpia)
    np_prices = build_price_map(naple)
    pc_prices = build_price_map(printcity)

    # dtpia: 코팅 구분이 필요하므로 별도 리스트로 보관 후 필터링
    dt_items = []
    if dtpia:
        for r in dtpia.get("items", []):
            paper = r.get("paper_name", "")
            price = r.get("price")
            if not paper or not price:
                continue
            qty = r.get("qty", 200)
            dt_items.append({
                "paper": paper,
                "product": r.get("product", ""),
                "coating": r.get("coating", ""),
                "price": price,
                "qty": qty,
                "vat": r.get("price_vat_included", True),
            })

    def get_dtpia_price(ref_info):
        if not ref_info:
            return None
        key = (ref_info.get("crawl_key") or ref_info.get("paper_name", "")).lower()
        product = ref_info.get("product", "")
        ref_coating = ref_info.get("coating", "")
        if not key:
            return None
        for r in dt_items:
            r_paper = r["paper"].lower()
            if not (key in r_paper or r_paper in key):
                continue
            if product and r["product"] != product:
                continue
            if ref_coating and r["coating"] != ref_coating:
                continue
            return r
        return None

    # 명함천국 (아직 구형 구조)
    ec_prices = {}
    if ecard:
        for kind, records in ecard.get("products", {}).items():
            for r in records:
                if r.get("price"):
                    ec_prices[r["paper_name"]] = r["price"]

    # 비교 테이블: reference 매칭 키로 크롤링 output에서 가격 조회
    def get_price(ref_info, price_map):
        key = ref_info.get("crawl_key") or ref_info.get("paper_name", "")
        product = ref_info.get("product", "")
        # 1) (paper_name, product) 복합키로 정확 매칭
        data = price_map.get((key, product))
        # 2) product가 "A / B" 형태면 각각 시도
        if not data and "/" in product:
            for p in product.split("/"):
                data = price_map.get((key, p.strip()))
                if data:
                    break
        # 3) paper_name 단독 키로 폴백
        if not data:
            data = price_map.get(key)
        # 4) alt key 시도
        if not data and ref_info.get("crawl_key_alt"):
            alt = ref_info["crawl_key_alt"]
            data = price_map.get((alt, product)) or price_map.get(alt)
        return data

    rows = []
    for p in ref.get("papers", []):
        ec = p.get("ecard21") or {}
        bz = p.get("bizhows") or {}
        sw = p.get("swadpia") or {}
        np = p.get("naple") or {}
        pc = p.get("printcity") or {}

        # crawl_key로 크롤링 output 매칭
        ec_key = ec.get("crawl_key") or ec.get("paper_name", "")
        ec_price = ec_prices.get(ec_key)

        pc_data = get_price(pc, pc_prices)
        pc_price = pc_data["price"] if pc_data else None

        bz_data = get_price(bz, bz_prices)
        bz_raw = bz_data["price"] if bz_data else None
        bz_vat = bz_data.get("vat", False) if bz_data else False
        bz_price = int(bz_raw * 1.1) if bz_raw and not bz_vat else bz_raw

        sw_data = get_price(sw, sw_prices)
        sw_price = sw_data["price"] if sw_data else None
        sw_qty = sw_data.get("qty", 200) if sw_data else 200

        np_data = get_price(np, np_prices)
        np_price = np_data["price"] if np_data else None
        np_qty = np_data.get("qty", 200) if np_data else 200

        dt = p.get("dtpia") or {}
        dt_data = get_dtpia_price(dt)
        dt_raw = dt_data["price"] if dt_data else None
        dt_vat = dt_data.get("vat", True) if dt_data else True
        dt_price = dt_raw if dt_vat else (int(dt_raw * 1.1) if dt_raw else None)
        dt_qty = dt_data.get("qty", 200) if dt_data else 200

        # 가격 하나도 없으면 스킵
        if not any([ec_price, pc_price, bz_price, sw_price, np_price, dt_price]):
            continue

        rows.append({
            "paper": p["paper_name_ko"],
            "ecard21": {"paper": ec.get("paper_name", ""), "price": ec_price},
            "printcity": {"paper": pc.get("paper_name", ""), "price": pc_price},
            "bizhows": {"paper": bz.get("paper_name", ""), "price": bz_price},
            "swadpia": {"paper": sw.get("paper_name", ""), "price": sw_price, "qty": sw_qty},
            "naple": {"paper": np.get("paper_name", ""), "price": np_price, "qty": np_qty},
            "dtpia": {"paper": dt.get("paper_name", ""), "price": dt_price, "qty": dt_qty,
                      "coating": dt.get("coating", "")},
        })

    return jsonify({
        "updated_at": ref.get("updated_at"),
        "rows": rows,
    })


@app.route("/api/data/changes")
def api_changes():
    """가격 변동 감지: past vs now 비교"""
    changes = []

    for company in COMPETITORS:
        now_data = load_now(company)
        past_data = load_past(company)
        if not now_data or not past_data:
            continue

        # 통일 구조에서 가격 맵 빌드
        def build_change_map(data):
            prices = {}
            items = data.get("items", []) if isinstance(data, dict) else data
            for r in items:
                paper = r.get("paper_name", "")
                price = r.get("price")
                if paper and price:
                    qty = r.get("qty", 200)
                    if isinstance(qty, int) and qty < 200:
                        continue
                    prices[paper] = price
            return prices

        now_prices = build_change_map(now_data)
        past_prices = build_change_map(past_data)

        for paper, now_price in now_prices.items():
            past_price = past_prices.get(paper)
            if past_price and now_price != past_price:
                diff = now_price - past_price
                pct = round(diff / past_price * 100, 1)
                changes.append({
                    "company": company,
                    "paper": paper,
                    "past_price": past_price,
                    "now_price": now_price,
                    "diff": diff,
                    "pct": pct,
                    "direction": "up" if diff > 0 else "down",
                })

    # 변동률 절대값 큰 순 정렬
    changes.sort(key=lambda x: abs(x["pct"]), reverse=True)

    ts = load_json(TIMESTAMP_PATH) or {}
    return jsonify({
        "changes": changes,
        "total": len(changes),
        "past_time": ts.get("past"),
        "now_time": ts.get("now"),
    })


@app.route("/stream")
def stream():
    """SSE 스트림 - 크롤링 진행 상황 실시간 전송"""
    def generate():
        last_progress = -1
        while True:
            with status_lock:
                progress = crawl_status["progress"]
                total = crawl_status["total"]
                current = crawl_status["current"]
                running = crawl_status["running"]
                elapsed = crawl_status.get("elapsed_sec", 0)
                remaining = crawl_status.get("estimated_remaining_sec", 0)

            # 실시간 경과 시간 계산
            if running and _crawl_start_time:
                elapsed = int(time.time() - _crawl_start_time)

            if progress != last_progress or running:
                data = json.dumps({
                    "progress": progress,
                    "total": total,
                    "current": current,
                    "running": running,
                    "elapsed_sec": elapsed,
                    "remaining_sec": remaining,
                })
                yield f"data: {data}\n\n"
                last_progress = progress

            if not running and progress > 0:
                yield f"data: {json.dumps({'done': True})}\n\n"
                break

            time.sleep(0.5)

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


if __name__ == "__main__":
    os.chdir(BASE_DIR)
    app.run(host="::", port=5000, threaded=True)
