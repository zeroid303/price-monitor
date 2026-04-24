"""실행 단위 상태(RunContext)와 DOM 실측 레코드(RawItem)."""
from dataclasses import dataclass, field, asdict
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .logger import RunLogger


@dataclass
class RawItem:
    """DOM 실측 한 건. config 폴백 금지 — 못 읽은 값은 None."""
    product: str = ""
    category: str = ""
    paper_name: Optional[str] = None
    coating: Optional[str] = None
    print_mode: Optional[str] = None
    size: Optional[str] = None
    qty: Optional[int] = None
    price: Optional[int] = None
    price_vat_included: Optional[bool] = None
    url: str = ""
    url_ok: bool = True
    options: dict = field(default_factory=dict)
    match_as: Optional[str] = None   # 평량 차이 매칭용 paper_name override (대시보드 그룹 키)
    item_id: str = ""

    def to_dict(self, include_item_id: bool = False) -> dict:
        d = asdict(self)
        if not include_item_id:
            d.pop("item_id", None)
        # match_as 는 값이 있을 때만 포맷에 포함 (기존 raw 호환)
        if d.get("match_as") is None:
            d.pop("match_as", None)
        return d


@dataclass
class RunContext:
    run_id: str
    site: str
    category: str
    site_config: dict
    schema: dict
    targets: list
    log: "RunLogger"
    browser: Any = None  # playwright BrowserContext 등 (어댑터가 필요 시 세팅)
    extras: dict = field(default_factory=dict)
