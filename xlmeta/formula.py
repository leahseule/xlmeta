"""
xlmeta.formula — 수식 문자열만 다룬다. 표 구조는 모른다.
"""

import re

REF = re.compile(
    r"(?:(?P<sheet>'[^']+'|[A-Za-z가-힣_][A-Za-z0-9가-힣_.]*)!)?"
    r"(?P<a1>\$?[A-Z]{1,3}\$?\d{1,7}(?::\$?[A-Z]{1,3}\$?\d{1,7})?"
    r"|\$?[A-Z]{1,3}:\$?[A-Z]{1,3})"
)
FUNCS = re.compile(r"\b([A-Z_][A-Z0-9_.]{1,20})\s*\(")

COND_FUNCS = ("SUMIFS", "COUNTIFS", "AVERAGEIFS", "MAXIFS", "MINIFS",
              "SUMIF", "COUNTIF", "AVERAGEIF")

UNSUPPORTED_FUNCS = ("XLOOKUP", "XMATCH", "FILTER", "UNIQUE", "SORT",
                     "SEQUENCE", "LAMBDA", "GETPIVOTDATA")


def split_args(s):
    """괄호와 따옴표를 존중하며 최상위 콤마로 인자 분리."""
    out, buf, depth, q = [], "", 0, False
    for ch in s:
        if ch == '"':
            q = not q
        if not q:
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth -= 1
            elif ch == "," and depth == 0:
                out.append(buf.strip()); buf = ""; continue
        buf += ch
    if buf.strip():
        out.append(buf.strip())
    return out


def find_call(formula, fname):
    """함수 호출의 인자 문자열을 반환 (첫 번째 호출만)."""
    m = re.search(r"\b" + fname + r"\s*\(", formula, re.I)
    if not m:
        return None
    i, depth = m.end(), 1
    while i < len(formula) and depth:
        if formula[i] == "(":
            depth += 1
        elif formula[i] == ")":
            depth -= 1
        i += 1
    return formula[m.end(): i - 1]


def refs_in(formula, default_sheet):
    """수식의 모든 참조를 (시트, A1)로 추출."""
    out = []
    for m in REF.finditer(formula):
        sheet = (m.group("sheet") or default_sheet).strip("'")
        out.append((sheet, m.group("a1").replace("$", "")))
    return out


def normalize(formula, row):
    """셀 참조의 행번호를 현재 행 기준 상대값으로 치환.
    행 간 '같은 수식'을 판별하기 위한 지문."""
    def sub(m):
        sheet, a1 = m.group("sheet"), m.group("a1")

        def rowsub(mm):
            col, dollar, r = mm.group(1), mm.group(2), int(mm.group(3))
            if dollar:                       # 절대참조는 고정값(임계값 등)
                return f"{col}${r}"
            d = r - row
            return f"{col}{{r{d:+d}}}" if d else f"{col}{{r}}"

        a1 = re.sub(r"(\$?[A-Z]{1,3})(\$?)(\d+)", rowsub, a1)
        return (sheet + "!" if sheet else "") + a1
    return REF.sub(sub, formula)


def functions_used(formula):
    return sorted(set(f.upper() for f in FUNCS.findall(formula)))


def unsupported_in(formula):
    fs = functions_used(formula)
    return [f for f in fs if f in UNSUPPORTED_FUNCS or f.startswith("_XLFN")]


def hardcoded_constants(formula):
    """참조를 제거한 뒤 남는 리터럴 숫자. 0/1은 관용적이라 제외."""
    stripped = REF.sub("", formula)
    return [n for n in re.findall(r"(?<![A-Za-z0-9_.])\d+\.?\d*", stripped)
            if n not in ("0", "1")]


def extract_conditions(formula, default_sheet):
    """SUMIFS 계열의 조건 쌍. 여기에 업무 규칙이 박제되어 있다."""
    conds = []
    for fn in COND_FUNCS:
        args_str = find_call(formula, fn)
        if not args_str:
            continue
        args = split_args(args_str)
        if fn in ("SUMIF", "AVERAGEIF", "COUNTIF"):
            pairs = [(args[0], args[1])] if len(args) >= 2 else []
        elif fn == "COUNTIFS":
            pairs = list(zip(args[0::2], args[1::2]))
        else:                                  # 첫 인자는 합산 대상
            rest = args[1:]
            pairs = list(zip(rest[0::2], rest[1::2]))
        for rng, crit in pairs:
            crit = crit.strip()
            literal = crit.startswith('"') and crit.endswith('"')
            val = crit.strip('"')
            op = "="
            for cand in ("<>", ">=", "<=", ">", "<"):
                if val.startswith(cand):
                    op, val = cand, val[len(cand):]
                    break
            sheet_a1 = refs_in(rng, default_sheet)
            conds.append({
                "target_ref": (f"{sheet_a1[0][0]}!{sheet_a1[0][1]}"
                               if sheet_a1 else rng.strip()),
                "operator": op,
                "value": val,
                "kind": "business_rule" if literal else "match_key",
            })
    return conds
