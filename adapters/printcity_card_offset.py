"""프린트시티 오프셋 명함(일반+고급) 어댑터.

데이터 소스: config/targets/card_offset.yaml 의 printcity 섹션 (items).
이 items 는 scripts/build_printcity_card_targets.py 가 data/printcity/*.xlsx 에서 일회성 생성.
어댑터는 items 를 RawItem 으로 변환만 — 엑셀 파싱 로직은 빌드 스크립트에 위임.

price=null (엑셀 value=0/None) 조합도 그대로 yield — raw 원칙.
"""
from typing import Iterator

from engine.adapter import SiteAdapter
from engine.context import RawItem, RunContext


def _yield_from_targets(ctx: RunContext, expected_products: tuple[str, ...]) -> Iterator[RawItem]:
    pc = ctx.targets
    if not isinstance(pc, dict) or "items" not in pc:
        ctx.log.event(
            "fetch.fail", level="error",
            error="printcity targets 에 items 섹션 없음. "
                  "scripts/build_printcity_card_targets.py 를 먼저 실행하세요.",
        )
        return

    vat_included = bool(pc.get("price_vat_included", False))
    ctx.log.event(
        "fetch.start",
        sources=pc.get("sources", []),
        count=len(pc["items"]),
        vat_included=vat_included,
    )

    base_url = ctx.site_config.get("base_url", "")
    for it in pc["items"]:
        product = it["product"]
        # 카테고리 간 교차 오염 방지
        if expected_products and product not in expected_products:
            continue

        yield RawItem(
            product=product,
            category=product,  # 프린트시티는 product 자체를 category 분류로 사용
            paper_name=it.get("paper"),
            coating=it.get("coating"),
            print_mode=it.get("color_mode"),
            size=it.get("size"),
            qty=it.get("qty"),
            price=it.get("price"),  # None 가능 (엑셀 value=0)
            price_vat_included=vat_included,
            url=base_url,
            url_ok=True,
            options={
                "source": "xlsx",
                "paper_code": it.get("paper_code"),
                "coating_code": it.get("coating_code"),
                "color_code": it.get("color_code"),
            },
        )


class Adapter(SiteAdapter):
    site = "printcity"
    category = "card_offset"

    def fetch_and_extract(self, ctx: RunContext) -> Iterator[RawItem]:
        yield from _yield_from_targets(ctx, expected_products=("일반명함", "고급명함"))
