"""
webapp.qa — xlmeta 지식 문서 위에서 GPT로 질의응답 (Q&A 레이어).

코어(xlmeta 패키지)는 LLM을 전혀 쓰지 않는다. 이 모듈만 OpenAI를 호출한다.
방식은 '전체 넣기(context-stuffing)': 결정론적으로 만든 지식 문서를 통째로 컨텍스트에
넣고, **문서에 있는 것만** 근거로 답하도록 강제한다(추론·환각 금지 = xlmeta 원칙 계승).

API 키는 코드에 넣지 않는다. 환경변수에서만 읽는다:
    OPENAI_API_KEY   (필수)
    OPENAI_MODEL     (선택, 기본 gpt-4o-mini)
"""

import os

# 지식 문서가 아주 큰 경우의 안전 상한(문자 기준). 전체 넣기 전제라 넉넉히 둔다.
# 초과하면 앞부분만 넣고 잘렸음을 알린다(대형 워크북은 추후 검색형으로).
MAX_CONTEXT_CHARS = 120_000

SYSTEM_PROMPT = (
    "너는 'xlmeta'가 스프레드시트에서 **결정론적으로(LLM 없이)** 추출한 지식 문서만 "
    "근거로 답하는 분석 도우미다. 이 지식 문서에는 구조(파일→시트→표→컬럼)·실제 데이터·"
    "계산 규칙·계산 계보·불일치·개념이 담겨 있다.\n\n"
    "규칙:\n"
    "1. 오직 아래 지식 문서 안의 내용으로만 답한다. 문서에 없으면 '문서에 없어요'라고 "
    "말하고 지어내지 않는다.\n"
    "2. 답의 근거 위치를 가능한 한 인용한다 (예: `시트!셀`, 지표명, 개념명, 근거 행).\n"
    "3. 숫자·규칙·수식은 문서 값을 그대로 쓴다. 어림짐작하지 않는다.\n"
    "4. 불일치·확인할 점을 물으면 문서의 '진단/불일치' 근거로 답한다.\n"
    "5. 한국어로, 간결하고 구체적으로 답한다."
)


class NoKeyError(RuntimeError):
    """OPENAI_API_KEY 미설정."""


def _client():
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise NoKeyError()
    from openai import OpenAI  # 지연 import — 키 없으면 패키지도 필요 없다
    return OpenAI(api_key=key)


def available():
    """키가 설정돼 있으면 True (UI에서 버튼 활성/안내에 사용)."""
    return bool(os.environ.get("OPENAI_API_KEY"))


def model_name():
    return os.environ.get("OPENAI_MODEL", "gpt-4o-mini")


def answer(knowledge_md, question, source_file):
    """지식 문서 + 질문 → 근거 기반 한국어 답변 문자열."""
    client = _client()
    body = knowledge_md or ""
    truncated = False
    if len(body) > MAX_CONTEXT_CHARS:
        body = body[:MAX_CONTEXT_CHARS]
        truncated = True

    note = ("\n\n(참고: 지식 문서가 커서 앞부분만 실렸습니다. "
            "없다고 나오면 문서 뒷부분 내용일 수 있어요.)") if truncated else ""
    user = (f"[지식 문서 — {source_file}]\n\n{body}\n\n"
            f"[질문]\n{question}{note}")

    resp = client.chat.completions.create(
        model=model_name(),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        temperature=0,
    )
    return (resp.choices[0].message.content or "").strip()
