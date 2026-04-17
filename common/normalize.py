"""
raw 크롤링 결과 → 정규화된 결과로 변환하는 순수 함수 모듈.

사용:
    from normalize import load_rule, normalize_items
    rule = load_rule("config/card_mapping_rule.json")
    normalized = normalize_items(raw_items, rule)

파일 I/O 없음. 스케줄러/배치/테스트 어디서든 재사용.
"""
import json
import re
from copy import deepcopy


def load_rule(path: str) -> dict:
    """매핑 규칙 JSON 로드. _normalization 섹션만 반환."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("_normalization", {})


def _build_alias_lookup(rule_section: dict) -> dict[str, str]:
    """aliases dict({canonical: [raw1, raw2...]})를 역인덱스({raw: canonical}).
    canonical 자기 자신도 매핑에 포함."""
    lookup = {}
    for canonical, raws in rule_section.get("aliases", {}).items():
        lookup[canonical] = canonical
        for r in raws:
            lookup[r] = canonical
    return lookup


def _normalize_coating(raw: str, rule: dict) -> tuple[str, dict]:
    """raw coating → (정규화 coating, options 추가분)."""
    raw = (raw or "").strip()
    # 1) to_options 먼저 확인 (부분코팅/홀로그램 등 특수)
    to_opts = rule.get("to_options", {})
    if raw in to_opts:
        entry = to_opts[raw]
        if isinstance(entry, dict) and "coating_base" in entry:
            return (entry["coating_base"], dict(entry.get("options", {})))
    # 2) aliases 역인덱스
    lookup = _build_alias_lookup(rule)
    if raw in lookup:
        return (lookup[raw], {})
    # 3) fallback: default
    return (rule.get("default", "비코팅"), {"coating_raw": raw} if raw else {})


def _normalize_print_mode(raw: str, rule: dict) -> tuple[str, dict]:
    raw = (raw or "").strip()
    to_opts = rule.get("to_options", {})
    if raw in to_opts:
        entry = to_opts[raw]
        if isinstance(entry, dict) and "_base" in entry:
            opts = {k: v for k, v in entry.items() if k != "_base"}
            return (entry["_base"], opts)
    lookup = _build_alias_lookup(rule)
    if raw in lookup:
        return (lookup[raw], {})
    return (rule.get("default", "양면칼라"), {"print_mode_raw": raw} if raw else {})


def _normalize_size(raw: str, rule: dict) -> str:
    """raw size → 'WxH' mm 정수."""
    raw = (raw or "").strip()
    if not raw:
        return rule.get("default", "90x50")
    pattern = rule.get("regex", r"(\d+)\s*[x×*X]\s*(\d+)\s*(cm)?")
    m = re.search(pattern, raw)
    if not m:
        return rule.get("default", "90x50")
    w, h = int(m.group(1)), int(m.group(2))
    unit = m.group(3) if m.lastindex and m.lastindex >= 3 else None
    if unit == "cm":
        w *= 10
        h *= 10
    return f"{w}x{h}"


def _normalize_qty(raw, rule: dict) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return rule.get("default", 200)


def _normalize_paper_name(raw: str, rule: dict) -> tuple[str, str | None]:
    """용지명 공통 파서. 카드/스티커 모두 동일 로직.

    처리 순서:
      1. 전체 문자열 alias 매칭 (히트하면 즉시 반환)
      2. prefix 코팅 추출 ("무코팅아트지" → 비코팅 + "아트지")
         2-1. prefix 제거 후 전체 문자열 재매칭
      3. 괄호 코팅 추출 ("(무광코팅)" → 무광코팅)
      4. weight 분리 ("아트지 250g" → base="아트지", weight=250)
      5. base alias 매칭 (longest match)
      6. '{canonical} {weight}g' 재조립

    예:
      "초강접스티커(아트지 90g)"  → 1번에서 매칭 → ("초강접아트지 90g", None)
      "무코팅아트지90g"          → 2번 prefix 추출 → ("강접아트지 90g", "비코팅")
      "스노우지(무광코팅) 250g"  → 3번 괄호 추출 + 4~6번 → ("스노우화이트 250g", "무광코팅")
      "무코팅스노우 400g"        → 2번 prefix + 4~6번 → ("스노우화이트 400g", "비코팅")
    """
    raw = (raw or "").strip()
    if not raw:
        return "", None

    lookup = _build_alias_lookup(rule)

    # ── 1. 전체 문자열 alias 매칭 ──
    if raw in lookup:
        return lookup[raw], None

    # ── 2. prefix 코팅 추출 ("무코팅아트지90g", "코팅스노우 300g") ──
    paper_coating = None
    working = raw
    for prefix, coat_val in (("무코팅", "비코팅"), ("코팅", "유광코팅")):
        if working.startswith(prefix) and len(working) > len(prefix) and working[len(prefix)] not in (" ", "("):
            paper_coating = coat_val
            working = working[len(prefix):]
            break

    # 2-1. prefix 제거 후 전체 문자열 재매칭
    if paper_coating and working in lookup:
        return lookup[working], paper_coating

    # ── 3. 괄호 코팅 추출 ("(무광코팅)", "(무코팅)") ──
    m = re.search(r"\((무광코팅|유광코팅|벨벳코팅|무코팅)\)", working)
    if m:
        tok = m.group(1)
        paper_coating = "비코팅" if tok == "무코팅" else tok
        working = (working[:m.start()] + working[m.end():]).strip()

    # ── 4. weight 분리 ──
    noise_re = rule.get("noise_suffix_regex", r"(\d+)\s*g.*$")
    wm = re.search(noise_re, working)
    weight = None
    base = working
    if wm:
        weight = wm.group(1)
        base = working[:wm.start()].strip()

    base = re.sub(r"\s+", " ", base).strip()

    # ── 5. base alias 매칭 (longest match) ──
    canonical_name = None
    best_len = 0
    for alias, canonical in lookup.items():
        if alias in base and len(alias) > best_len:
            canonical_name = canonical
            best_len = len(alias)
    if not canonical_name:
        canonical_name = base

    # ── 6. 재조립 ──
    result = f"{canonical_name} {weight}g" if weight else canonical_name
    return result, paper_coating


def apply(item: dict, norm_rule: dict) -> dict:
    """단일 item을 정규화된 dict로 변환. 원본 훼손 안 함.

    입력 item 예 (raw):
        paper_name: "스노우화이트-250g"
        coating: "부분UV코팅-앞면"
        print_mode: "양면8도"
        size: "명함 90×50"
        qty: 200
        price: 7920
        options: {}
    출력 (normalized):
        paper_name: "스노우화이트-250g"  (paper_name은 alias 없어서 그대로)
        coating: "비코팅"
        print_mode: "양면칼라"
        size: "90x50"
        qty: 200
        price: 7920
        options: { partial_coating: true }   ← to_options로부터 머지
    """
    out = deepcopy(item)
    options = dict(out.get("options") or {})

    # paper_name (+ 내부에서 발견한 coating 힌트)
    paper_rule = norm_rule.get("paper_name", {})
    paper_val, paper_coating = _normalize_paper_name(out.get("paper_name", ""), paper_rule)
    out["paper_name"] = paper_val

    # coating — paper_name에서 추출된 coating이 있으면 우선, 없으면 원본 coating 필드 정규화
    coating_rule = norm_rule.get("coating", {})
    if paper_coating:
        c_val, c_opts = paper_coating, {}
    else:
        c_val, c_opts = _normalize_coating(out.get("coating", ""), coating_rule)
    out["coating"] = c_val
    options.update(c_opts)

    # print_mode
    pm_rule = norm_rule.get("print_mode", {})
    pm_val, pm_opts = _normalize_print_mode(out.get("print_mode", ""), pm_rule)
    out["print_mode"] = pm_val
    options.update(pm_opts)

    # size
    out["size"] = _normalize_size(out.get("size", ""), norm_rule.get("size", {}))

    # qty
    out["qty"] = _normalize_qty(out.get("qty"), norm_rule.get("qty", {}))

    # price: VAT 포함 가격으로 통일 (raw가 별도면 ×1.1 보정)
    raw_price = out.get("price")
    if isinstance(raw_price, (int, float)) and out.get("price_vat_included") is False:
        out["price"] = round(raw_price * 1.1)
        out["price_vat_included"] = True
        options["price_vat_adjusted"] = True  # 보정 흔적 (참고용)

    out["options"] = options
    return out


def normalize_items(items: list[dict], norm_rule: dict) -> list[dict]:
    return [apply(it, norm_rule) for it in items]


def normalize_output(raw_output: dict, norm_rule: dict) -> dict:
    """전체 output 파일 구조 정규화 (company/crawled_at 유지 + items 변환)."""
    out = {k: v for k, v in raw_output.items() if k != "items"}
    out["items"] = normalize_items(raw_output.get("items", []), norm_rule)
    return out
