"""프린트시티 봉투 어댑터.

소스: config/targets/envelope.yaml 의 printcity 섹션 (정적 엑셀 기반).
얇은 어댑터 — target items 그대로 RawItem 으로 변환. Playwright 사용 X.
"""
from typing import Iterator

from engine.adapter import SiteAdapter
from engine.context import RawItem, RunContext


PRODUCT_URL_SLUGS = {
    "칼라봉투": "EnvelopeOffset_ver2",
    "기성봉투": "EnvelopeMaster",
}


def _product_url(base_url: str, product: str) -> str:
    slug = PRODUCT_URL_SLUGS.get(product)
    return f"{base_url.rstrip('/')}/product/{slug}" if slug else base_url


class Adapter(SiteAdapter):
    site = "printcity"
    category = "envelope"

    def fetch_and_extract(self, ctx: RunContext) -> Iterator[RawItem]:
        pc = ctx.targets if isinstance(ctx.targets, dict) else {}
        items = pc.get("items", [])
        vat_included = ctx.site_config.get("vat_included", False)
        base_url = ctx.site_config.get("base_url", "")

        ctx.log.event("fetch.start", source="xlsx_static",
                      total=len(items), vat_included=vat_included)

        for it in items:
            yield RawItem(
                product=it["product"],
                category=it["product"],
                paper_name=it.get("paper"),
                coating=None,                    # 봉투는 코팅 없음
                print_mode=it.get("print_mode"),
                size=it.get("size"),
                qty=it.get("qty"),
                price=it.get("price"),
                price_vat_included=vat_included,
                url=_product_url(base_url, it["product"]),
                url_ok=True,
                options={
                    "source": "xlsx",
                    "paper_code": it.get("paper_code"),
                    "color_code": it.get("color_code"),
                    "size_code": it.get("size_code"),
                    "size_label": it.get("size_label"),
                },
            )
