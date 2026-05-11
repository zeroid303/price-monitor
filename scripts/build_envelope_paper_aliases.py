"""봉투 schema 의 paper_name.canonical aliases 자동 갱신 스크립트 (일회성).

입력:
  - output/{site}_envelope_raw_now.json (5 사이트의 raw paper_name 표기)
  - config/schemas/envelope.yaml (현재 schema)

처리:
  1. 5 사이트 raw paper_name unique 수집
  2. 매핑 규칙(brand 패턴 → canonical+weight) 으로 분류
  3. canonical 별 weights[w].aliases.{site} 채워 schema 갱신

매핑 규칙 (raw paper_name 패턴 → (canonical, weight)):
  - "모조지 백색 NNNg" / "모조-NNNg" → (모조, NNN)
  - "미색모조지 미색 NNNg" / "모조지 미색 NNNg" → (미색모조, NNN)
  - "레자크-NNNg" / "줄레자크 ... NNNg" / "체크레자크 ... NNNg" / "레쟈크 ... NNNg"
    / "페스티발 ... NNNg" / "페스티발 (레쟈크) ... NNNg" → (레자크, NNN)
  - "랑데뷰 ... NNNg" / "랑데뷰-NNNg" → (랑데뷰, NNN)
  - "크라프트-NNNg" / "크라프트 크라프트... NNNg" → (크라프트, NNN)
  - "화일지-NNNg" / "화일지 ... NNNg" → (화일지, NNN)
  - "밍크-NNNg" / "매직칼라 ... NNNg" / "매직칼라 (밍크지) ... NNNg" → (밍크, NNN)
  - "탄트-NNNg" / "매직매칭 ... NNNg" / "매직매칭 (탄트지) ... NNNg" → (탄트, NNN)
  - "레이드-NNNg" / "레이드 ... NNNg" / "매직쉐도우 (레이드) ... NNNg" → (레이드, NNN)
  - "스타드림 ... NNNg" → (스타드림, NNN)
  - "뉴에코 ... NNNg" → (뉴에코, NNN)
  - "#91체크 레자크백색-NNNg" (printcity 변종) → (레자크, NNN)

매핑 안 되는 raw 는 "unmatched" 로 출력 — 사용자 검토.
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "config/schemas/envelope.yaml"
SITES = ["printcity", "dtpia", "swadpia", "wowpress", "adsland"]


# 매핑 규칙 — (canonical, prefix_pattern_regex) 우선순위 순서.
# 더 구체적인 패턴 먼저.
MAPPING_RULES = [
    # (canonical_name, raw_pattern_re)
    # 미색모조 — 명시적 "미색" 키워드. "모조지 미색" / "미색모조지" 둘 다.
    ("미색모조", re.compile(r"(미색모조지|모조지\s+미색)")),
    # 일반 모조 (백색/일반)
    ("모조", re.compile(r"(^모조\s*-|^모조지\b)")),
    # 레자크 계열 — 페스티발 (레쟈크 동의어), 줄레자크/체크레자크 (prefix), 레자크/레쟈크
    # printcity '레자크줄무늬백-110g'/'레자크체크백-110g' 같이 한글 즉시 따라오는 케이스도 포함.
    ("레자크", re.compile(r"(^레자크|^레쟈크|^줄레자크|^체크레자크|^페스티발|^#\d+체크\s+레자크)")),
    ("랑데뷰", re.compile(r"^랑데뷰")),
    ("크라프트", re.compile(r"^크라프트")),
    ("화일지", re.compile(r"^화일지")),
    # 밍크 — 매직칼라/매직칼라(밍크지)
    ("밍크", re.compile(r"(^밍크[\s\-]|^매직칼라)")),
    # 탄트 — 매직매칭/매직매칭(탄트지)
    ("탄트", re.compile(r"(^탄트[\s\-]|^매직매칭)")),
    # 레이드 — 매직쉐도우(레이드)
    ("레이드", re.compile(r"(^레이드[\s\-]|^매직쉐도우)")),
    ("스타드림", re.compile(r"^스타드림")),
    ("뉴에코", re.compile(r"^뉴에코")),
]


_WEIGHT_RE = re.compile(r"(\d{2,4})\s*g")


def classify(raw: str):
    """raw paper_name → (canonical, weight) 또는 None."""
    for canonical, pat in MAPPING_RULES:
        if pat.search(raw):
            m = _WEIGHT_RE.search(raw)
            if m:
                return (canonical, int(m.group(1)))
            return (canonical, 0)
    return None


def main():
    sys.stdout.reconfigure(encoding="utf-8")

    # 1. 5사이트 raw paper_name unique 수집
    site_papers = {}
    for site in SITES:
        path = ROOT / "output" / f"{site}_envelope_raw_now.json"
        if not path.exists():
            print(f"  ⚠ {path} 없음 — skip")
            site_papers[site] = set()
            continue
        items = json.loads(path.read_text(encoding="utf-8")).get("items", [])
        # printcity 는 raw paper_name 가 'paper' 필드와 다를 수 있음 — paper_name 으로 통일
        papers = set()
        for it in items:
            pn = it.get("paper_name")
            if pn:
                papers.add(pn.strip())
        site_papers[site] = papers
        print(f"  {site}: {len(papers)} unique papers")

    # 2. 매핑 + 분류
    # canonical_data[canonical][weight][site] = set of raw aliases
    canonical_data = defaultdict(lambda: defaultdict(lambda: defaultdict(set)))
    unmatched_by_site = defaultdict(list)

    for site, papers in site_papers.items():
        for raw in papers:
            cls = classify(raw)
            if cls is None:
                unmatched_by_site[site].append(raw)
                continue
            canonical, weight = cls
            if weight == 0:
                unmatched_by_site[site].append(f"{raw} (no weight)")
                continue
            canonical_data[canonical][weight][site].add(raw)

    # 3. unmatched 보고
    if any(unmatched_by_site.values()):
        print("\n=== UNMATCHED (사용자 검토 필요) ===")
        for site, lst in unmatched_by_site.items():
            if not lst: continue
            print(f"  {site}: {len(lst)}")
            for r in sorted(lst):
                print(f"    {r!r}")

    # 4. 매핑 결과 보고
    print("\n=== canonical 별 매핑 결과 ===")
    for canonical in sorted(canonical_data):
        weights = canonical_data[canonical]
        total = sum(len(s) for ws in weights.values() for s in ws.values())
        wlist = sorted(weights.keys())
        print(f"  {canonical}: weights={wlist}, total aliases={total}")

    # 5. schema 갱신 — paper_name.canonical 섹션 재구성
    schema = yaml.safe_load(SCHEMA_PATH.read_text(encoding="utf-8"))
    paper_canon = schema["_normalization"]["paper_name"]["canonical"]

    # 기존 canonical 보존 + 신규/추가 alias 머지.
    # 단, '스타펄' / '아르떼' / '모조창문' 처럼 봉투에 없는 dead entry 는 제거.
    USED_CANONICALS = set(canonical_data.keys())

    new_canon = {}
    for canonical in sorted(canonical_data.keys()):
        weights_data = canonical_data[canonical]
        new_canon[canonical] = {
            "weights": {
                int(w): {
                    "aliases": {
                        site: sorted(list(aliases))
                        for site, aliases in sorted(weights_data[w].items())
                    }
                }
                for w in sorted(weights_data.keys())
            }
        }

    # noise_suffix_regex 보존
    old_noise = schema["_normalization"]["paper_name"].get("noise_suffix_regex")
    old_desc = schema["_normalization"]["paper_name"].get("_description")

    new_paper_name = {}
    if old_desc:
        new_paper_name["_description"] = old_desc
    new_paper_name["canonical"] = new_canon
    if old_noise:
        new_paper_name["noise_suffix_regex"] = old_noise

    schema["_normalization"]["paper_name"] = new_paper_name

    # 6. print_mode aliases 보강 — wowpress "단면 칼라4도" 추가
    pm = schema["_normalization"]["print_mode"]
    pm_aliases = pm.setdefault("aliases", {})
    pm_aliases.setdefault("단면4도", [])
    extra_pm = ["단면4도", "단면 칼라4도", "단면칼라", "단면 칼라"]
    for x in extra_pm:
        if x not in pm_aliases["단면4도"]:
            pm_aliases["단면4도"].append(x)

    # 7. yaml 저장
    SCHEMA_PATH.write_text(
        yaml.safe_dump(schema, allow_unicode=True, sort_keys=False,
                       default_flow_style=False, width=140),
        encoding="utf-8",
    )
    print(f"\n✅ schema 갱신: {SCHEMA_PATH}")
    print(f"  canonical: {sorted(new_canon.keys())}")


if __name__ == "__main__":
    main()
