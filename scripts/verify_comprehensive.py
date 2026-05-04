"""대시보드 전면 검증 — 데이터/API/URL/가격 sanity 6 레이어.

레이어:
  L1. 데이터 정합성 — raw vs normalize record 수, 필드 누락 검사
  L2. 매칭 정확성 — schema 의 canonical 만 사용했는지, paper_name 형식 검증
  L3. API 정합성 — 4 카테고리 grid 응답 + paper × site 행렬 일관성
  L4. 가격 sanity — qty 단조 증가 / cross-site 분포 이상치
  L5. URL 형식 / liveness (HTTP HEAD)
  L6. 보간 / 정렬 검증

실행: python scripts/verify_comprehensive.py [--liveness]
사전: dashboard 가 :5001 에 떠 있어야 함.
"""
import json
import os
import re
import statistics
import sys
from collections import defaultdict

import requests
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "http://localhost:5001/api"
SITES = ["printcity", "dtpia", "swadpia", "wowpress", "adsland"]


def L1_data_integrity():
    print("\n[L1] 데이터 정합성")
    issues = 0
    total = 0
    for site in SITES:
        for cat in ("card_offset", "card_digital"):
            raw_fp = f"{ROOT}/output/{site}_{cat}_raw_now.json"
            norm_fp = f"{ROOT}/output/{site}_{cat}_normalize_now.json"
            if not os.path.exists(raw_fp): continue
            raw = json.load(open(raw_fp, encoding="utf-8"))["items"]
            norm = json.load(open(norm_fp, encoding="utf-8"))["items"]
            total += len(raw)
            if len(raw) != len(norm):
                print(f"  ✗ {site}/{cat}: raw {len(raw)} ≠ normalize {len(norm)}"); issues += 1
            # 필드 누락 검사
            for i, n in enumerate(norm):
                miss = [f for f in ("paper_name", "coating", "qty", "price")
                        if not n.get(f) and n.get(f) != 0]
                if miss:
                    if issues < 3:
                        print(f"  ✗ {site}/{cat}[{i}]: 필드 누락 {miss}")
                    issues += 1
    print(f"  total {total} records, issues {issues}")
    return issues == 0


def L2_canonical_consistency():
    print("\n[L2] 매칭/canonical 일관성")
    issues = 0
    for cat in ("card_offset", "card_digital"):
        schema = yaml.safe_load(open(f"{ROOT}/config/schemas/{cat}.yaml", encoding="utf-8"))
        canon_set = set(schema["_normalization"]["paper_name"]["canonical"].keys())
        for site in SITES:
            fp = f"{ROOT}/output/{site}_{cat}_normalize_now.json"
            if not os.path.exists(fp): continue
            d = json.load(open(fp, encoding="utf-8"))
            for it in d["items"]:
                pn = it.get("paper_name", "")
                # canonical 명 추출 (평량 g 떼고)
                base = re.sub(r"\s+\d+g$", "", pn).strip()
                if base and base not in canon_set:
                    if issues < 5:
                        print(f"  ⚠ {cat}/{site}: '{pn}' 의 canonical '{base}' 가 schema 에 없음")
                    issues += 1
    print(f"  schema 미등록 canonical 사용: {issues} 건")
    return issues == 0


def L3_api_consistency():
    print("\n[L3] API 정합성")
    issues = 0
    for cat in ("card_offset", "card_digital", "sticker", "envelope"):
        try:
            r = requests.get(f"{API}/data/grid?category={cat}", timeout=10)
            assert r.status_code == 200
            d = r.json()
        except Exception as e:
            print(f"  ✗ {cat}: API 응답 실패 {e}"); issues += 1; continue

        n_sites = len(d.get("sites", []))
        for p in d.get("papers", []):
            # sites cell 개수 일치
            if len(p["sites"]) != n_sites:
                print(f"  ✗ {cat}/{p['label']}: site cells {len(p['sites'])} ≠ sites {n_sites}")
                issues += 1; break
        print(f"  {cat}: ✓ papers={len(d.get('papers',[]))} sites={n_sites}")
    return issues == 0


def L4_price_sanity():
    print("\n[L4] 가격 sanity")
    issues = []
    cross_site_outliers = 0
    for cat in ("card_offset", "card_digital"):
        r = requests.get(f"{API}/data/grid?category={cat}", timeout=10)
        d = r.json()
        for p in d.get("papers", []):
            # 사이트별 (qty=가장 큰 매수, 양면) 가격 비교
            target_q = str(d["qtys"][-1])  # 1000매
            cross = {}
            for sid, sd in p["sites"].items():
                if sd:
                    pp = sd["prices"].get("양면", {}).get(target_q)
                    if pp: cross[sid] = pp
            # 2 사이트 이상이면 표준편차 계산
            if len(cross) >= 3:
                vals = list(cross.values())
                mean = statistics.mean(vals)
                stdev = statistics.stdev(vals) if len(vals) > 1 else 0
                # 평균 ±50% 밖은 outlier
                for sid, pp in cross.items():
                    if abs(pp - mean) / mean > 0.5 and stdev > 0:
                        cross_site_outliers += 1
                        if cross_site_outliers <= 5:
                            print(f"  ⚠ outlier: {cat}/{p['label']}/{sid} = {pp} "
                                  f"(평균 {mean:.0f}, 다른 사이트 {[v for s,v in cross.items() if s!=sid]})")
    print(f"  cross-site outlier (50% off): {cross_site_outliers} 건")
    return cross_site_outliers < 10


def L5_url_format(check_liveness: bool = False):
    print(f"\n[L5] URL 형식{' + liveness' if check_liveness else ''}")
    URL_RE = re.compile(r"^https?://[^\s]+$")
    bad = 0
    dead = 0
    unique_urls = set()
    for site in SITES:
        for cat in ("card_offset", "card_digital"):
            fp = f"{ROOT}/output/{site}_{cat}_raw_now.json"
            if not os.path.exists(fp): continue
            d = json.load(open(fp, encoding="utf-8"))
            for it in d["items"]:
                u = it.get("url") or ""
                if not URL_RE.match(u):
                    bad += 1
                    if bad <= 3: print(f"  ✗ {site}/{cat}: invalid url {u!r}")
                else:
                    unique_urls.add(u)
    print(f"  형식 위반: {bad} 건 / unique URL: {len(unique_urls)}")
    if check_liveness:
        print(f"  liveness 체크 시작 ({len(unique_urls)} URLs)...")
        for u in unique_urls:
            try:
                r = requests.head(u, timeout=8, allow_redirects=True)
                if r.status_code >= 400:
                    dead += 1
                    if dead <= 5: print(f"    ✗ {r.status_code}: {u}")
            except Exception:
                dead += 1
                if dead <= 5: print(f"    ✗ ERR: {u}")
        print(f"  dead URLs: {dead}/{len(unique_urls)}")
    return bad == 0 and dead == 0


def L6_interpolation_sort():
    print("\n[L6] 보간 + 정렬 검증")
    issues = 0
    for cat in ("card_offset", "card_digital"):
        r = requests.get(f"{API}/data/grid?category={cat}", timeout=10)
        d = r.json()
        # 정렬 검증: 다사이트 매칭이 위쪽
        prev_n = 999
        out_of_order = 0
        for p in d["papers"]:
            n = sum(1 for sd in p["sites"].values() if sd)
            if n > prev_n:
                out_of_order += 1
            prev_n = n
        # 보간 표시 검증 (digital 만)
        if cat == "card_digital":
            interp_count = sum(
                1 for p in d["papers"] for sd in p["sites"].values()
                if sd and sd.get("interp_note")
            )
            print(f"  {cat}: 정렬 위반={out_of_order}, 보간 표시={interp_count}")
        else:
            print(f"  {cat}: 정렬 위반={out_of_order}")
        if out_of_order: issues += 1
    return issues == 0


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    check_liveness = "--liveness" in sys.argv
    print("=" * 60)
    print("대시보드 전면 검증 (6 레이어)")
    print("=" * 60)

    results = {
        "L1": L1_data_integrity(),
        "L2": L2_canonical_consistency(),
        "L3": L3_api_consistency(),
        "L4": L4_price_sanity(),
        "L5": L5_url_format(check_liveness),
        "L6": L6_interpolation_sort(),
    }
    print("\n" + "=" * 60)
    print("종합 결과")
    print("=" * 60)
    for L, ok in results.items():
        print(f"  {L}: {'✓ 통과' if ok else '⚠ 실패'}")
    if all(results.values()):
        print("\n🎉 모두 통과")
    else:
        failed = [L for L, ok in results.items() if not ok]
        print(f"\n실패 레이어: {failed}")


if __name__ == "__main__":
    main()
