"""
xlmeta.fingerprint — 정의의 '내용'으로 지문을 만든다.

이름·셀주소·시트이름·행 개수가 아니라 '무엇을 어떻게 계산하는가'만 남긴다.
그래서:
  · 행이 밀리거나 시트 이름이 바뀌어도  → 같은 지문 (승인 유지)
  · 조건 값이나 계산 구조가 바뀌면        → 다른 지문 (승인 자동 만료)

변화 감지와 승인 만료가 같은 한 장치가 된다.
"""

import hashlib
import re

from .explain import AGG_VERB, named_formula


def _col(ref):
    """참조에서 열 문자만. 시트·행 제거.  실적!G6 → G,  H6:H11 → H."""
    a1 = str(ref).split("!")[-1]
    mo = re.match(r"\$?([A-Z]{1,3})", a1)
    return mo.group(1) if mo else a1


def canonical(m):
    """정의를 정규화한 문자열. 같은 정의면 같은 문자열이 되도록."""
    funcs = m.get("functions", [])
    agg = next((f for f in funcs if f in AGG_VERB), None)

    if agg:
        # 집계: 합산 대상(열 이름) + 정렬된 조건. 셀주소·시트·행 없음.
        reads = m.get("reads", [])
        target = (reads[0].get("name") if reads else None) or (_col(reads[0]["ref"]) if reads else "?")
        parts = []
        for c in m.get("conditions", []):
            nm = c.get("target_name") or _col(c.get("target_ref", ""))
            if c.get("kind") == "match_key":
                parts.append(f"{nm}~match")            # 매칭 키: 값(A6)은 무의미, 열만
            else:
                parts.append(f"{nm}{c.get('operator', '=')}{c.get('value', '')}")  # 업무규칙: 값이 중요
        parts.sort()
        return f"{agg}({target};{';'.join(parts)})"

    # 사칙연산·복잡 수식: 이름 넣은 식(구조 보존) + 남은 셀참조 행 제거 + 공백 제거
    s = named_formula(m)
    s = re.sub(r"\$?[A-Z]{1,3}\$?\d+(?::\$?[A-Z]{1,3}\$?\d+)?",
               lambda mo: re.sub(r"\d+", "", mo.group()), s)
    return re.sub(r"\s+", "", s)


def fingerprint(m):
    """정규화된 정의의 짧은 해시."""
    return hashlib.sha256(canonical(m).encode("utf-8")).hexdigest()[:12]
