"""사이트×카테고리 어댑터 패키지.

모듈명 규약: {site}_{category} (예: dtpia_card, wowpress_envelope)
각 모듈은 SiteAdapter 서브클래스를 노출:
- class Adapter(SiteAdapter):  ← 엔진이 Adapter() 인스턴스화
  또는
- adapter: SiteAdapter           ← 모듈 레벨 인스턴스
"""
