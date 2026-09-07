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

_CYPHER_PROMPT_TEMPLATE = PromptTemplate.from_template("""\
너는 스프레드시트 구조 그래프를 조회하는 Cypher를 짠다. 스키마:
{schema}

이 파일에 실제로 존재하는 Column 이름 전체 목록(이 목록에 있는 이름만 정확히 그대로 쓴다):
{columns}

규칙:
- Column {{name: ...}}에 쓰는 값은 반드시 위 목록에 있는 이름 중 하나를 정확히 그대로 골라 쓴다.
  질문에 나온 표현이 목록과 글자가 다르게 느껴져도(예: 질문엔 "예산 집행률"이라고 나왔는데
  목록엔 "예산"과 "집행률"이 따로 있는 경우), 목록에 없는 이름을 지어내거나 합치지 말고
  뜻이 가장 가까운 목록 속 이름 하나(또는 필요하면 여러 개)를 그대로 쓴다.
- 텍스트 값(value, name, title)을 비교할 때는 '=' 대신 CONTAINS를 쓰고, 대소문자·띄어쓰기 차이를 무시하려면
  toLower(replace(toString(x), ' ', '')) CONTAINS toLower(replace('질문의 값', ' ', '')) 형태로 짠다.
  value 속성은 숫자일 수도 있으니 항상 toString()으로 먼저 문자열로 바꾼다.
- "A를 알면 같은 행의 B를 찾아라" 유형(행 기반 조회)은 반드시 아래 예시와 같은 패턴으로 짠다:
  Column(A)로 셀을 먼저 찾고 -> WITH로 그 결과를 확정지은 뒤 -> Row를 타고 -> Column(B)에 속한 Cell을 찾는다.
  두 MATCH를 WITH 없이 바로 이어 쓰면 Neo4j가 실행 순서를 섞어서 엉뚱한 셀을 훑을 수 있으니,
  반드시 첫 MATCH+WHERE 다음에 WITH로 경계를 끊는다.
- 특정 Cell의 값을 답으로 반환할 때는, 그 값만 반환하지 말고 **그 Cell의 name(셀 주소, 예:
  '원가현황!D6')도 항상 같이 RETURN**한다. 사용자가 그 셀로 바로 이동할 수 있게 하는 데 쓴다.

예시 질문 1 (알려진 값이 어느 컬럼 값인지 질문에 명시된 경우): "프로젝트코드 P-9000의 담당자는?"
예시 Cypher:
MATCH (codeCol:Column {{name:'프로젝트코드'}})-[:HAS_VALUE]->(codeCell:Cell)
WHERE toLower(replace(toString(codeCell.value), ' ', '')) CONTAINS toLower(replace('P-9000', ' ', ''))
WITH codeCell
MATCH (codeCell)<-[:HAS_CELL]-(row:Row)-[:HAS_CELL]->(targetCell:Cell)<-[:HAS_VALUE]-(targetCol:Column {{name:'담당자'}})
RETURN targetCell.value AS 값, targetCell.name AS 셀

예시 질문 2 (알려진 값이 어느 컬럼 값인지 질문에 안 나온 경우 — 컬럼명을 함부로 추측하지 말고,
컬럼 제약 없이 값 자체로 셀을 먼저 찾는다): "동서기업의 담당자는?"
예시 Cypher:
MATCH (anchorCell:Cell)
WHERE toLower(replace(toString(anchorCell.value), ' ', '')) CONTAINS toLower(replace('동서기업', ' ', ''))
WITH anchorCell
MATCH (anchorCell)<-[:HAS_CELL]-(row:Row)-[:HAS_CELL]->(targetCell:Cell)<-[:HAS_VALUE]-(targetCol:Column {{name:'담당자'}})
RETURN targetCell.value AS 값, targetCell.name AS 셀

예시 질문 3 (질문에 표 이름까지 나온 경우 — 같은 이름의 컬럼이 다른 표에도 있을 수 있으니,
Table로 먼저 범위를 좁히고 그 안에서만 코드도 찾고 답도 찾는다. 표 제목은 뒤에 다른 말이
더 붙어있을 수 있으니(예: '원가현황 (2026년 7월)') 여기도 CONTAINS로 부분 일치시킨다):
"원가현황 테이블에서 P-9000의 담당자는?"
예시 Cypher:
MATCH (t:Table)
WHERE toLower(replace(toString(t.title), ' ', '')) CONTAINS toLower(replace('원가현황', ' ', ''))
WITH t
MATCH (t)-[:HAS_COLUMN]->(codeCol:Column {{name:'프로젝트코드'}})-[:HAS_VALUE]->(codeCell:Cell)
WHERE toLower(replace(toString(codeCell.value), ' ', '')) CONTAINS toLower(replace('P-9000', ' ', ''))
WITH t, codeCell
MATCH (t)-[:HAS_ROW]->(row:Row)-[:HAS_CELL]->(codeCell)
MATCH (row)-[:HAS_CELL]->(targetCell:Cell)<-[:HAS_VALUE]-(:Column {{name:'담당자'}})
RETURN targetCell.value AS 값, targetCell.name AS 셀

예시 질문 4 (질문의 표현이 실제 컬럼 이름과 글자가 다른 경우 — 컬럼 목록에서 "예산 집행률"이
없고 "예산"·"집행률"이 따로 있다면, 둘을 합친 이름을 지어내지 말고 목록에 있는 실제 이름
"집행률"을 그대로 쓴다): "온산의 예산 집행률을 알려줘"
예시 Cypher:
MATCH (anchorCell:Cell)
WHERE toLower(replace(toString(anchorCell.value), ' ', '')) CONTAINS toLower(replace('온산', ' ', ''))
WITH anchorCell
MATCH (anchorCell)<-[:HAS_CELL]-(row:Row)-[:HAS_CELL]->(targetCell:Cell)<-[:HAS_VALUE]-(:Column {{name:'집행률'}})
RETURN targetCell.value AS 값, targetCell.name AS 셀

- 다른 설명 없이 Cypher 쿼리만 출력한다.

질문: {question}
Cypher:""")

QA_PROMPT = PromptTemplate.from_template("""\
너는 'xlmeta'가 스프레드시트에서 결정론적으로 추출한 그래프를, Cypher로 조회한
결과만 근거로 답하는 분석 도우미다.

규칙:
1. 아래 [조회 결과]에 있는 값만 그대로 쓴다. 단위·통화·서술을 새로 지어내지 않는다
   (예: 숫자만 있으면 숫자만 말하고 '원/달러/개' 같은 단위를 임의로 붙이지 않는다).
2. [조회 결과]가 비어 있으면 "문서에서 못 찾았어요"라고 답하고 추측하지 않는다.
3. [조회 결과]에 서로 다른 값이 여러 개 있으면(같은 항목이 여러 표에서 다르게 계산된
   경우 등) 하나를 임의로 고르지 말고, 있는 그대로 다 보여주며 어디서 나온 값인지
   구분해 말한다.
4. 한국어로, 간결하고 구체적으로 답한다.
5. 마크다운으로 답한다: 핵심 숫자·값은 **굵게**, 값이 여러 개거나 항목을 나열할 땐
   `- ` 글머리 목록을 쓴다. 셀 주소(예: 원가현황!D6)는 `인라인 코드`로 감싼다.
6. 답이 짧고 단순하면(값 하나만 묻는 질문 등) 소제목 없이 바로 답한다.
   답이 여러 구간으로 나뉠 만큼 복잡하면(여러 표를 비교하거나, 여러 항목을 각각
   설명하는 경우 등) `### 소제목`으로 구간을 나눠서 구조화한다. 소제목은 짧고
   명확하게(예: "### 원가현황 기준", "### 경영요약 기준").
   과한 서식(표, 코드블록)은 쓰지 않는다 — 소제목·굵게·목록·인라인 코드 네 가지만.

질문: {question}
[조회 결과]: {context}

답:""")


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


def _column_names(sid):
    """이 파일(sid)에 실제로 존재하는 Column.name 전체 목록. GPT가 컬럼명을 지어내지
    않고 이 목록에서만 고르게 하는 데 쓴다(실제로 없는 이름을 지어내는 문제가 있었음)."""
    rows = _plain_graph().query(
        "MATCH (c:Column {sid: $sid}) RETURN DISTINCT c.name AS name",
        {"sid": sid},
    )
    return sorted({r["name"] for r in rows if r.get("name")})


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

    셀 하나마다 쿼리 하나씩 날리면(로컬 Neo4j에선 문제없었지만) Neo4j Aura처럼
    원격이면 왕복 지연시간이 곱해져 파일 하나에 수백 번 왕복 → 수십 초~gunicorn
    타임아웃까지 걸릴 수 있다. UNWIND로 묶어서 파일 크기와 무관하게 쿼리 8개 안팎으로
    끝낸다(2026-09-07, Aura 전환 후 실제로 겪은 문제).
    """
    _require_env()
    graph = _plain_graph()
    source_file = result["source_file"]

    graph.query("MERGE (f:File {source_file: $sf, sid: $sid})", {"sf": source_file, "sid": sid})

    graph.query(
        """
        UNWIND $sheets AS name
        MERGE (s:Sheet {name: name, sid: $sid})
        WITH s, name
        MATCH (f:File {source_file: $sf, sid: $sid})
        MERGE (f)-[:HAS_SHEET]->(s)
        """,
        {"sheets": result["sheets"], "sid": sid, "sf": source_file},
    )

    tables, columns, rows, cells = [], [], [], []
    for table in result["sources"]:
        table_key = table["title"] or table["range"]
        tables.append({"key": table_key, "title": table["title"], "range": table["range"], "sheet": table["sheet"]})
        for letter, name in table["columns"].items():
            columns.append({"table": table_key, "letter": letter, "name": name})

        col_letters = table["data"]["col_letters"]
        for row in table["data"]["rows"]:
            r = row["r"]
            rows.append({"table": table_key, "r": r})
            for letter, value in zip(col_letters, row["cells"]):
                cells.append({
                    "table": table_key, "r": r, "letter": letter,
                    "name": f"{table['sheet']}!{letter}{r}", "value": value,
                })

    if tables:
        graph.query(
            """
            UNWIND $tables AS t
            MERGE (table:Table {key: t.key, sid: $sid})
            SET table.title = t.title, table.range = t.range
            WITH table, t
            MATCH (s:Sheet {name: t.sheet, sid: $sid})
            MERGE (s)-[:HAS_TABLE]->(table)
            """,
            {"tables": tables, "sid": sid},
        )
    if columns:
        graph.query(
            """
            UNWIND $columns AS col
            MERGE (c:Column {table: col.table, letter: col.letter, sid: $sid})
            SET c.name = col.name
            WITH c, col
            MATCH (t:Table {key: col.table, sid: $sid})
            MERGE (t)-[:HAS_COLUMN]->(c)
            """,
            {"columns": columns, "sid": sid},
        )
    if rows:
        graph.query(
            """
            UNWIND $rows AS row
            MERGE (r:Row {table: row.table, r: row.r, sid: $sid})
            WITH r, row
            MATCH (t:Table {key: row.table, sid: $sid})
            MERGE (t)-[:HAS_ROW]->(r)
            """,
            {"rows": rows, "sid": sid},
        )
    if cells:
        graph.query(
            """
            UNWIND $cells AS cell
            MERGE (c:Cell {name: cell.name, sid: $sid})
            SET c.value = cell.value
            WITH c, cell
            MATCH (row:Row {table: cell.table, r: cell.r, sid: $sid})
            MATCH (col:Column {table: cell.table, letter: cell.letter, sid: $sid})
            MERGE (row)-[:HAS_CELL]->(c)
            MERGE (col)-[:HAS_VALUE]->(c)
            """,
            {"cells": cells, "sid": sid},
        )

    # 수식 의존관계(READS)도 같은 Cell 노드 위에 그대로 얹는다.
    formulas = [{"name": name, "formula": info.get("formula")} for name, info in result["cell_graph"].items()]
    if formulas:
        graph.query(
            "UNWIND $formulas AS f MERGE (c:Cell {name: f.name, sid: $sid}) SET c.formula = f.formula",
            {"formulas": formulas, "sid": sid},
        )

    edges = [
        {"a": name, "b": ref}
        for name, info in result["cell_graph"].items()
        for ref in info.get("reads", [])
    ]
    if edges:
        graph.query(
            """
            UNWIND $edges AS e
            MERGE (a:Cell {name: e.a, sid: $sid})
            MERGE (b:Cell {name: e.b, sid: $sid})
            MERGE (a)-[:READS]->(b)
            """,
            {"edges": edges, "sid": sid},
        )


_CELL_REF_RE = re.compile(r"^.+![A-Z]+\d+$")


def _collect_cell_refs(value, found):
    """context(list/dict가 중첩된 구조) 안에서 '시트!주소' 모양 문자열만 골라낸다."""
    if isinstance(value, str):
        if _CELL_REF_RE.match(value):
            found.add(value)
    elif isinstance(value, dict):
        for v in value.values():
            _collect_cell_refs(v, found)
    elif isinstance(value, list):
        for v in value:
            _collect_cell_refs(v, found)


def ask(sid, question):
    """이 파일(sid)의 구조 그래프에 자연어로 질문 -> Cypher 생성·실행 -> (자연어 답변, 근거 셀 주소 목록)."""
    _require_env()
    from langchain_neo4j import GraphCypherQAChain
    from langchain_openai import ChatOpenAI

    columns = _column_names(sid)
    cypher_prompt = _CYPHER_PROMPT_TEMPLATE.partial(columns=", ".join(columns))

    chain = GraphCypherQAChain.from_llm(
        ChatOpenAI(model=model_name(), temperature=0),
        graph=_scoped_graph(sid),
        cypher_prompt=cypher_prompt,
        qa_prompt=QA_PROMPT,
        allow_dangerous_requests=True,
        return_intermediate_steps=True,
    )
    result = chain.invoke({"query": question})

    context = next(
        (step["context"] for step in result.get("intermediate_steps", []) if "context" in step),
        [],
    )
    cell_refs = set()
    _collect_cell_refs(context, cell_refs)

    return result["result"], sorted(cell_refs)
