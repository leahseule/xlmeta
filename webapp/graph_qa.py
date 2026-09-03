"""
webapp.graph_qa — xlmeta의 extract() 결과(구조+셀 그래프)를 Neo4j에 올리고,
자연어 질문을 Cypher로 바꿔 답하는 Q&A 레이어 (프로토타입).

qa.py(문서 전체 넣기)와는 결이 다른 방식: "가장 많이 참조되는 셀은?" 같은
집계·다단계 참조 질문, "프로젝트코드 P-2403의 프로젝트명은?" 같은 표 안
행 기반 조회에 강하다. 코어(xlmeta 패키지)는 그대로, 이 모듈만 Neo4j·
OpenAI를 부른다.

그래프 스키마 (File→Sheet→Table을 세로/가로 두 축으로 잇는 구조):
    (:File)-[:HAS_SHEET]->(:Sheet)-[:HAS_TABLE]->(:Table)
    (:Table)-[:HAS_COLUMN]->(:Column)-[:HAS_VALUE]->(:Cell)   # 세로: 어느 컬럼인가
    (:Table)-[:HAS_ROW]->(:Row)-[:HAS_CELL]->(:Cell)           # 가로: 어느 행인가
    (:Cell)-[:READS]->(:Cell)                                   # 수식 의존관계

Cell 노드가 Column·Row 양쪽에서 다 들어오는 간선을 받아서, "코드로 이름 찾기"
같은 행 기반 조회를 Row라는 다리로 건널 수 있다.

여러 파일이 같은 Neo4j 인스턴스를 같이 쓰므로, 모든 노드에 sid(분석 세션 ID)를
붙인다. GPT가 만든 Cypher를 실행하기 '직전에' 코드가 가로채 sid 필터를 강제로
끼워 넣는다(ScopedNeo4jGraph) — GPT가 필터를 깜빡해도 안전하게, xlmeta의
"결정론적으로" 원칙을 이 레이어에도 적용한다.

공개 챗봇이라 GPT가 만든 Cypher를 실행할 때 routing_=READ로 돌린다 — Neo4j
서버가 쓰기 쿼리 자체를 거부한다(단일 서버에서도 실제로 강제됨, 클러스터 라우팅
힌트가 아님). 프롬프트 인젝션으로 삭제·수정을 유도해도 서버 단에서 막힌다.

환경변수:
    OPENAI_API_KEY   (필수)
    NEO4J_URI        (필수, 예: bolt://localhost:7687)
    NEO4J_USERNAME   (필수)
    NEO4J_PASSWORD   (필수)

Neo4j에는 APOC 플러그인이 설치돼 있어야 한다(스키마 조회에 사용).
"""

import os
import re

from langchain_core.prompts import PromptTemplate

_LABELS = ["File", "Sheet", "Table", "Column", "Row", "Cell"]
_LABEL_PATTERN = re.compile(r":(" + "|".join(_LABELS) + r")(\s*\{([^}]*)\})?")
_REQUIRED_ENV = ("OPENAI_API_KEY", "NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD")

CYPHER_PROMPT = PromptTemplate.from_template("""\
너는 스프레드시트 구조 그래프를 조회하는 Cypher를 짠다. 스키마:
{schema}

규칙:
- 텍스트 값(value, name, title)을 비교할 때는 '=' 대신 CONTAINS를 쓰고, 대소문자·띄어쓰기 차이를 무시하려면
  toLower(replace(toString(x), ' ', '')) CONTAINS toLower(replace('질문의 값', ' ', '')) 형태로 짠다.
  value 속성은 숫자일 수도 있으니 항상 toString()으로 먼저 문자열로 바꾼다.
- "A를 알면 같은 행의 B를 찾아라" 유형(행 기반 조회)은 반드시 아래 예시와 같은 패턴으로 짠다:
  Column(A)로 셀을 먼저 찾고 -> WITH로 그 결과를 확정지은 뒤 -> Row를 타고 -> Column(B)에 속한 Cell을 찾는다.
  두 MATCH를 WITH 없이 바로 이어 쓰면 Neo4j가 실행 순서를 섞어서 엉뚱한 셀을 훑을 수 있으니,
  반드시 첫 MATCH+WHERE 다음에 WITH로 경계를 끊는다.

예시 질문 1 (알려진 값이 어느 컬럼 값인지 질문에 명시된 경우): "프로젝트코드 P-9000의 담당자는?"
예시 Cypher:
MATCH (codeCol:Column {{name:'프로젝트코드'}})-[:HAS_VALUE]->(codeCell:Cell)
WHERE toLower(replace(toString(codeCell.value), ' ', '')) CONTAINS toLower(replace('P-9000', ' ', ''))
WITH codeCell
MATCH (codeCell)<-[:HAS_CELL]-(row:Row)-[:HAS_CELL]->(targetCell:Cell)<-[:HAS_VALUE]-(targetCol:Column {{name:'담당자'}})
RETURN targetCell.value

예시 질문 2 (알려진 값이 어느 컬럼 값인지 질문에 안 나온 경우 — 컬럼명을 함부로 추측하지 말고,
컬럼 제약 없이 값 자체로 셀을 먼저 찾는다): "동서기업의 담당자는?"
예시 Cypher:
MATCH (anchorCell:Cell)
WHERE toLower(replace(toString(anchorCell.value), ' ', '')) CONTAINS toLower(replace('동서기업', ' ', ''))
WITH anchorCell
MATCH (anchorCell)<-[:HAS_CELL]-(row:Row)-[:HAS_CELL]->(targetCell:Cell)<-[:HAS_VALUE]-(targetCol:Column {{name:'담당자'}})
RETURN targetCell.value

- 다른 설명 없이 Cypher 쿼리만 출력한다.

질문: {question}
Cypher:""")


class NotConfigured(RuntimeError):
    """필요한 환경변수(OPENAI_API_KEY/NEO4J_*)가 없음."""


def _require_env():
    missing = [k for k in _REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        raise NotConfigured(", ".join(missing))


def available():
    """환경변수가 다 있으면 True (UI에서 챗봇 탭 활성/안내에 사용). Neo4j 접속 자체는 확인 안 함."""
    try:
        _require_env()
        return True
    except NotConfigured:
        return False


def model_name():
    return os.environ.get("OPENAI_MODEL", "gpt-4o-mini")


def _inject_sid(match):
    label = match.group(1)
    inner = match.group(3)
    if inner is not None and inner.strip():
        return f":{label} {{sid: $sid, {inner.strip()}}}"
    return f":{label} {{sid: $sid}}"


def _plain_graph():
    from langchain_neo4j import Neo4jGraph

    return Neo4jGraph(
        url=os.environ["NEO4J_URI"],
        username=os.environ["NEO4J_USERNAME"],
        password=os.environ["NEO4J_PASSWORD"],
    )


def _scoped_graph(sid):
    from langchain_neo4j import Neo4jGraph
    from neo4j import Query, RoutingControl

    class ScopedNeo4jGraph(Neo4jGraph):
        """GPT가 만든 Cypher를 실행하는 전용 그래프. 두 겹으로 방어한다:
        (1) sid 필터를 강제로 끼워 넣어 다른 파일 데이터가 안 섞이게,
        (2) routing_=READ로 실행해 서버 차원에서 쓰기 쿼리 자체를 거부하게
            (공개 챗봇이라 프롬프트 인젝션으로 삭제/수정을 시도해도 Neo4j가 막는다).
        """

        def query(self, query, params=None):
            if not _LABEL_PATTERN.search(query):
                raise ValueError("sid로 범위를 좁힐 수 없는 쿼리라 실행을 막았어요.")
            scoped = _LABEL_PATTERN.sub(_inject_sid, query)
            params = dict(params or {})
            params["sid"] = sid
            data, _, _ = self._driver.execute_query(
                Query(text=scoped, timeout=self.timeout),
                database_=self._database,
                parameters_=params,
                routing_=RoutingControl.READ,
            )
            return [r.data() for r in data]

    return ScopedNeo4jGraph(
        url=os.environ["NEO4J_URI"],
        username=os.environ["NEO4J_USERNAME"],
        password=os.environ["NEO4J_PASSWORD"],
    )


def load_structured_graph(sid, result):
    """분석 시점에 한 번 호출 — extract() 결과(구조+cell_graph)를 Neo4j에 sid로 태그해 적재.

    MERGE만 쓰므로 같은 sid로 재호출해도 안전(재분석·재시작 시 중복 안 생김).
    """
    _require_env()
    graph = _plain_graph()
    source_file = result["source_file"]

    graph.query("MERGE (f:File {source_file: $sf, sid: $sid})", {"sf": source_file, "sid": sid})

    for sheet in result["sheets"]:
        graph.query(
            """
            MERGE (s:Sheet {name: $name, sid: $sid})
            WITH s
            MATCH (f:File {source_file: $sf, sid: $sid})
            MERGE (f)-[:HAS_SHEET]->(s)
            """,
            {"name": sheet, "sid": sid, "sf": source_file},
        )

    for table in result["sources"]:
        table_key = table["title"] or table["range"]
        graph.query(
            """
            MERGE (t:Table {key: $key, sid: $sid})
            SET t.title = $title, t.range = $range
            WITH t
            MATCH (s:Sheet {name: $sheet, sid: $sid})
            MERGE (s)-[:HAS_TABLE]->(t)
            """,
            {"key": table_key, "sid": sid, "title": table["title"], "range": table["range"], "sheet": table["sheet"]},
        )

        for letter, name in table["columns"].items():
            graph.query(
                """
                MERGE (c:Column {table: $table, letter: $letter, sid: $sid})
                SET c.name = $name
                WITH c
                MATCH (t:Table {key: $table, sid: $sid})
                MERGE (t)-[:HAS_COLUMN]->(c)
                """,
                {"table": table_key, "letter": letter, "sid": sid, "name": name},
            )

        col_letters = table["data"]["col_letters"]
        for row in table["data"]["rows"]:
            r = row["r"]
            graph.query(
                """
                MERGE (row:Row {table: $table, r: $r, sid: $sid})
                WITH row
                MATCH (t:Table {key: $table, sid: $sid})
                MERGE (t)-[:HAS_ROW]->(row)
                """,
                {"table": table_key, "r": r, "sid": sid},
            )
            for letter, value in zip(col_letters, row["cells"]):
                cell_name = f"{table['sheet']}!{letter}{r}"
                graph.query(
                    """
                    MERGE (c:Cell {name: $name, sid: $sid})
                    SET c.value = $value
                    WITH c
                    MATCH (row:Row {table: $table, r: $r, sid: $sid})
                    MATCH (col:Column {table: $table, letter: $letter, sid: $sid})
                    MERGE (row)-[:HAS_CELL]->(c)
                    MERGE (col)-[:HAS_VALUE]->(c)
                    """,
                    {"name": cell_name, "sid": sid, "value": value, "table": table_key, "r": r, "letter": letter},
                )

    # 수식 의존관계(READS)도 같은 Cell 노드 위에 그대로 얹는다.
    for cell_name, info in result["cell_graph"].items():
        graph.query(
            "MERGE (c:Cell {name: $name, sid: $sid}) SET c.formula = $formula",
            {"name": cell_name, "sid": sid, "formula": info.get("formula")},
        )
    for cell_name, info in result["cell_graph"].items():
        for ref in info.get("reads", []):
            graph.query(
                """
                MERGE (a:Cell {name: $a, sid: $sid})
                MERGE (b:Cell {name: $b, sid: $sid})
                MERGE (a)-[:READS]->(b)
                """,
                {"a": cell_name, "b": ref, "sid": sid},
            )


def ask(sid, question):
    """이 파일(sid)의 구조 그래프에 자연어로 질문 -> Cypher 생성·실행 -> 자연어 답변."""
    _require_env()
    from langchain_neo4j import GraphCypherQAChain
    from langchain_openai import ChatOpenAI

    chain = GraphCypherQAChain.from_llm(
        ChatOpenAI(model=model_name(), temperature=0),
        graph=_scoped_graph(sid),
        cypher_prompt=CYPHER_PROMPT,
        allow_dangerous_requests=True,
    )
    result = chain.invoke({"query": question})
    return result["result"]
