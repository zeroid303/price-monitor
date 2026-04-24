"""공통 크롤 엔진.

책임 분리:
- runner: fetch→extract→normalize→store 오케스트레이션
- adapter: 사이트×카테고리 1쌍당 1개 (SiteAdapter 서브클래스)
- context: RunContext(실행 단위 상태) + RawItem(DOM 실측 단일 레코드)
- logger: JSONL + 콘솔 이중 출력, 에러 artifact 저장
- store: output/{site}_{category}_{raw|normalize}_{now|past}.json rotation
"""
