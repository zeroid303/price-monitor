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
            if r is not None:
                lookup[r] = canonical
    return lookup


def _build_paper_lookup(canonical_section: dict) -> dict[str, tuple]:
    """new schema (canonical[name].weights[w].aliases) → flat alias → (canonical, weight) map.

    weight 0 = (평량없음). 평량 strip 변형도 등록 (fallback).
    """
    out = {}
    for name, info in canonical_section.items():
        weights = info.get("weights", {}) or {}
        for w_key, w_info in weights.items():
            try:
                weight = int(w_key) if w_key not in ("(평량없음)", None) else 0
            except (ValueError, TypeError):
                weight = 0
            aliases = (w_info or {}).get("aliases", {}) or {}
            for site_aliases in aliases.values():
                for a in site_aliases or []:
                    if not a: continue
                    a = a.strip()
                    if a not in out:
                        out[a] = (name, weight)
                    base = re.sub(r"\s*\d{2,4}\s*[gμu]\S*\s*$", "", a).strip()
                    base = re.sub(r"\s*\d{2,4}\s*g\s*/\s*㎡\s*$", "", base).strip()
                    if base and base != a and base not in out:
                        out[base] = (name, weight)
    return out


# 부가 표기 strip 패턴 (매칭 fallback)
_AUX_STRIP_RES = [
    re.compile(r"\s*\((FSC|D|신상품(-?[가-힣A-Za-z]*)?)\)\s*"),
    re.compile(r"\s*-?\s*당일판가능\s*"),
]


def _strip_aux(s: str) -> str:
    out = s
    for pat in _AUX_STRIP_RES:
        out = pat.sub(" ", out)
    return re.sub(r"\s+", " ", out).strip()


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
    """raw size → canonical.

    카드/스티커는 'WxH' mm 정수 포맷. 봉투는 '대봉투'/'9절봉투' 같은 카테고리 canonical.
    처리 순서:
      1) aliases 직접 매칭 ("대봉투-규격" → "대봉투")
      2) WxH 숫자 파싱 ("510x387" → "510x387")
      3) 파싱 결과 재-alias 매칭 ("330x245" → "대봉투")
    """
    raw = (raw or "").strip()
    if not raw:
        return rule.get("default", "")
    lookup = _build_alias_lookup(rule) if "aliases" in rule else {}
    # 1) raw 그대로 alias 매칭
    if lookup and raw in lookup:
        return lookup[raw]
    # 2) WxH 숫자 파싱
    pattern = rule.get("regex", r"(\d+)\s*[x×*X]\s*(\d+)\s*(cm)?")
    m = re.search(pattern, raw)
    if not m:
        return rule.get("default", "")
    w, h = int(m.group(1)), int(m.group(2))
    unit = m.group(3) if m.lastindex and m.lastindex >= 3 else None
    if unit == "cm":
        w *= 10
        h *= 10
    result = f"{w}x{h}"
    # 3) 파싱 결과 재-alias 매칭 (봉투용)
    if lookup and result in lookup:
        return lookup[result]
    return result


def _normalize_qty(raw, rule: dict) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return rule.get("default", 200)


def _normalize_paper_name_new(raw: str, paper_weight_text: str | None,
                               canonical_section: dict, rule: dict) -> tuple[str, str | None]:
    """new schema 매칭 — flat alias → (canonical, weight) 역인덱스 활용."""
    lookup = _build_paper_lookup(canonical_section)

    # 코팅 hint
    coat_hint = None
    m = re.search(r"\((무광코팅|유광코팅|벨벳코팅|무코팅|비코팅|UV유광코팅|UV코팅)\)", raw)
    if m:
        tok = m.group(1)
        if "무코팅" in tok or "비코팅" in tok:
            coat_hint = "비코팅"
        elif "무광" in tok:
            coat_hint = "무광코팅"
        elif "유광" in tok:
            coat_hint = "유광코팅"
        elif "벨벳" in tok:
            coat_hint = "벨벳코팅"

    # 후보 변형
    cands = [raw]
    # adsland paperSort prefix strip
    m_ps = re.match(r"^(일반지|고급지|펄지|친환경 재생지|색지|한지)\s+(.+?)\s+(\d{2,4})\s*g\s*/\s*㎡\s*$", raw)
    if m_ps:
        cands.append(f"{m_ps.group(2)} {m_ps.group(3)}g")
    # g/㎡ → g
    cand_g = re.sub(r"(\d+)\s*g\s*/\s*㎡", r"\1g", raw)
    if cand_g != raw:
        cands.append(cand_g)
    # printcity dash 형식: "스노우화이트-300g" → "스노우화이트 300g"
    cand_dash_w = re.sub(r"-(\d{2,4}g)", r" \1", raw)
    if cand_dash_w != raw:
        cands.append(cand_dash_w)
    # printcity dash 코팅 strip
    cand_dash = re.sub(r"-\s*(양면|단면)?(무광|유광|벨벳|무|비)코팅?[, ]?.*$", "", raw).strip()
    if cand_dash and cand_dash != raw:
        cands.append(cand_dash)
    # 부가 표기 strip
    cand_aux = _strip_aux(raw)
    if cand_aux != raw:
        cands.append(cand_aux)
    # 평량 suffix strip
    base = re.sub(r"\s*\d{2,4}\s*[gμu]\S*\s*$", "", raw).strip()
    base = re.sub(r"\s*\d{2,4}\s*g\s*/\s*㎡\s*$", "", base).strip()
    if base and base != raw:
        cands.append(base)

    # 매칭
    matched = None
    for c in cands:
        if c in lookup:
            matched = lookup[c]
            break

    if matched:
        canonical_name, weight = matched
        # weight 0 (평량 추출 실패) 이면 raw 또는 paper_weight_text 에서 보강
        if weight == 0:
            mw = re.search(r"(\d{2,4})\s*g", raw)
            if mw:
                weight = int(mw.group(1))
            elif paper_weight_text:
                mw = re.search(r"(\d{2,4})", paper_weight_text)
                if mw: weight = int(mw.group(1))
        if weight > 0:
            return (f"{canonical_name} {weight}g", coat_hint)
        return (canonical_name, coat_hint)

    # 미매칭 — raw 그대로
    return (raw, coat_hint)


def _normalize_paper_name(raw: str, rule: dict, paper_weight_text: str | None = None) -> tuple[str, str | None]:
    """용지명 정규화 — new schema (paper_name.canonical[name].weights[w].aliases).

    반환: (canonical_name + ' Wg', coating_hint).
    coating_hint 는 raw paper_name 의 괄호 코팅 표기 ("(무광코팅)" 등) 추출.

    매칭 순서:
      1. raw 그대로 alias 매칭
      2. 변형 매칭 (paperSort strip / g/㎡→g / dash 코팅 strip / 부가 표기 strip / 평량 suffix strip)
      3. 매칭 시 weight 가 0(평량없음) 이면 raw paper_name 또는 paper_weight_text 에서 평량 추출 보강
      4. 매칭 실패 시 raw 그대로 반환
    """
    raw = (raw or "").strip()
    if not raw:
        return "", None

    canonical_section = rule.get("canonical", {}) or {}
    if canonical_section and isinstance(next(iter(canonical_section.values()), {}), dict) \
            and "weights" in next(iter(canonical_section.values()), {}):
        # new schema
        return _normalize_paper_name_new(raw, paper_weight_text, canonical_section, rule)

    # legacy fallback (구 schema)
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
    # base 끝에 남은 구분자 제거 (raw가 "레자크-110g" 형태일 때 "레자크-" → "레자크")
    base = base.rstrip("-_ ").strip()

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
    # canonical에 이미 평량이 박혀있는 경우(예: 스티커 "초강접아트지 90g") 실제 raw weight와
    # 섞여 "초강접아트지 90g 100g"이 되는 것을 방지 — canonical의 trailing " Xg" 제거 후 재조립.
    # 카드 규칙처럼 canonical이 base-only ("스노우화이트")이면 sub 는 no-op.
    canonical_base = re.sub(r"\s*\d+\s*[gG]$", "", canonical_name).strip() or canonical_name
    result = f"{canonical_base} {weight}g" if weight else canonical_name
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

    # paper_name (+ 내부에서 발견한 coating 힌트). paper_weight_text 도 함께 전달.
    paper_rule = norm_rule.get("paper_name", {})
    paper_val, paper_coating = _normalize_paper_name(
        out.get("paper_name", ""), paper_rule,
        paper_weight_text=out.get("paper_weight_text"),
    )
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

    # price: 공급가(VAT 제외) 기준으로 통일
    # raw가 VAT 포함이면 ÷1.1 하여 공급가로 변환. raw가 이미 공급가면 그대로.
    raw_price = out.get("price")
    if isinstance(raw_price, (int, float)):
        if out.get("price_vat_included") is True:
            out["price"] = round(raw_price / 1.1)
            out["price_vat_included"] = False
            options["price_vat_stripped"] = True  # 변환 흔적
        else:
            # 공급가 raw → 그대로 유지
            out["price_vat_included"] = False

    out["options"] = options
    return out


def normalize_items(items: list[dict], norm_rule: dict) -> list[dict]:
    return [apply(it, norm_rule) for it in items]


def normalize_output(raw_output: dict, norm_rule: dict) -> dict:
    """전체 output 파일 구조 정규화 (company/crawled_at 유지 + items 변환)."""
    out = {k: v for k, v in raw_output.items() if k != "items"}
    out["items"] = normalize_items(raw_output.get("items", []), norm_rule)
    return out
