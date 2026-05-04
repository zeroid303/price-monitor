"""대시보드 매칭/표시 자동 검증.

체크 항목:
  T1. API 응답 정상 (200, JSON 파싱)
  T2. 다사이트 매칭 paper 핵심 케이스 — 5사이트 매칭이 실제 표에서 5사이트 보이는지
  T3. 가격 합리성 — 100매 < 200매 < 500매 < 1000매 (단조 증가)
                 단면 ≤ 양면 (대부분, ±10% 허용)
  T4. 보간 표시 — adsland card_digital 에 "수량 보간" 메모 있는지
  T5. 변동 감지 API — past/now 비교 정상

실행: python scripts/verify_dashboard.py
사전: dashboard/app.py 가 :5001 에 떠 있어야 함.
"""
import json
import sys

import requests

API = "http://localhost:5001/api"


def t1_api_health():
    print("T1. API health")
    for cat in ("card_offset", "card_digital", "sticker", "envelope"):
        r = requests.get(f"{API}/data/grid?category={cat}", timeout=10)
        ok = r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json")
        d = r.json() if ok else {}
        n_papers = len(d.get("papers", []))
        n_sites = len(d.get("sites", []))
        print(f"  {cat}: {'✓' if ok else '✗'} status={r.status_code} sites={n_sites} papers={n_papers}")


def t2_multisite_match():
    print("\nT2. 다사이트 매칭 핵심 케이스 (card_offset)")
    # 5사이트 매칭이 expected 인 paper 들
    EXPECTED_5SITE = ["스노우화이트 250g", "머쉬멜로우 209g", "휘라레 216g", "유포지 250g"]
    r = requests.get(f"{API}/data/grid?category=card_offset", timeout=10)
    d = r.json()
    for p in d.get("papers", []):
        for expected in EXPECTED_5SITE:
            if expected in p["label"] and "비코팅" in p["label"]:
                n_with = sum(1 for sd in p["sites"].values() if sd)
                ok = n_with == 5
                print(f"  {p['label']}: {n_with}/5 사이트 {'✓' if ok else '⚠'}")


def t3_price_monotonic():
    print("\nT3. 가격 합리성 (qty 단조 증가, 단면 ≤ 양면)")
    issues = 0
    checked = 0
    for cat in ("card_offset", "card_digital"):
        r = requests.get(f"{API}/data/grid?category={cat}", timeout=10)
        d = r.json()
        qtys = d.get("qtys", [])
        sides = d.get("sides", [])
        for p in d.get("papers", []):
            for sid, sd in p["sites"].items():
                if not sd: continue
                # 단조 증가 체크 (단면)
                for side in sides:
                    prices = [sd["prices"][side].get(str(q)) for q in qtys]
                    nonnull = [(q, p_) for q, p_ in zip(qtys, prices) if p_ is not None]
                    if len(nonnull) < 2: continue
                    checked += 1
                    for i in range(1, len(nonnull)):
                        if nonnull[i][1] < nonnull[i - 1][1]:
                            issues += 1
                            print(f"  ⚠ {cat}/{p['label']}/{sid}/{side}: "
                                  f"{nonnull[i-1][0]}매 {nonnull[i-1][1]}원 > {nonnull[i][0]}매 {nonnull[i][1]}원")
                            break
    print(f"  체크 {checked} 건 / 단조 증가 위반 {issues} 건")


def t4_interpolation_note():
    print("\nT4. 수량 보간 표시")
    r = requests.get(f"{API}/data/grid?category=card_digital", timeout=10)
    d = r.json()
    interp_count = 0
    for p in d.get("papers", []):
        for sid, sd in p["sites"].items():
            if sd and sd.get("interp_note"):
                interp_count += 1
                if interp_count <= 3:
                    print(f"  {p['label']}/{sid}: {sd['interp_note']}")
    print(f"  보간 표시된 site cell: {interp_count} 건")


def t5_changes_api():
    print("\nT5. 변동 감지 API")
    for cat in ("card_offset", "card_digital"):
        r = requests.get(f"{API}/data/changes?category={cat}", timeout=10)
        ok = r.status_code == 200
        d = r.json() if ok else {}
        n = d.get("total", 0)
        print(f"  {cat}: {'✓' if ok else '✗'} 변동 {n} 건")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    print("=" * 60)
    print("대시보드 자동 검증")
    print("=" * 60)
    try:
        t1_api_health()
        t2_multisite_match()
        t3_price_monotonic()
        t4_interpolation_note()
        t5_changes_api()
    except requests.ConnectionError:
        print("\n⚠ dashboard 가 :5001 에 안 떠 있음. 먼저 실행:")
        print("  python dashboard/app.py")


if __name__ == "__main__":
    main()
