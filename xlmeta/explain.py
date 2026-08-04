"""
xlmeta.explain — 추출된 지표를 사람·AI가 그대로 읽을 수 있는 형태로 옮긴다.

  · explain_metric  : 계산을 일상 언어(한국어)로
  · functions_doc   : 쓰인 함수의 뜻과 문법
  · pythonize       : 정확히 가능할 때만 파이썬 표현으로 (아니면 None)

LLM을 쓰지 않는다. 수식을 결정론적으로 파싱·번역하며, 정확히 못 바꾸는 수식은
지어내지 않고 None을 돌려준다. (README의 '추측하지 않는다'와 같은 원칙.)
"""

import re

AGG_VERB = {
    "SUMIFS": "모두 더한", "SUMIF": "모두 더한",
    "COUNTIFS": "센", "COUNTIF": "센",
    "AVERAGEIFS": "평균 낸", "AVERAGEIF": "평균 낸",
    "MAXIFS": "가장 큰", "MINIFS": "가장 작은",
}
PY_AGG = {
    "SUMIFS": "sum", "SUMIF": "sum", "SUM": "sum",
    "COUNTIFS": "count", "COUNTIF": "count",
    "AVERAGEIFS": "mean", "AVERAGEIF": "mean", "AVERAGE": "mean",
    "MAXIFS": "max", "MAX": "max", "MINIFS": "min", "MIN": "min",
}

FUNC_DOC = {
    "SUM": ("범위의 숫자를 모두 더해요.", "SUM(숫자1, [숫자2], …)"),
    "SUMIF": ("조건 하나에 맞는 행의 값만 더해요.", "SUMIF(조건범위, 조건, [합계범위])"),
    "SUMIFS": ("여러 조건을 모두 만족하는 행의 값만 더해요.", "SUMIFS(합계범위, 조건범위1, 조건1, [조건범위2, 조건2], …)"),
    "COUNT": ("숫자가 든 칸의 개수를 세요.", "COUNT(값1, [값2], …)"),
    "COUNTA": ("비어 있지 않은 칸의 개수를 세요.", "COUNTA(값1, [값2], …)"),
    "COUNTIF": ("조건에 맞는 칸의 개수를 세요.", "COUNTIF(범위, 조건)"),
    "COUNTIFS": ("여러 조건을 모두 만족하는 칸의 개수를 세요.", "COUNTIFS(조건범위1, 조건1, [조건범위2, 조건2], …)"),
    "AVERAGE": ("범위의 평균을 내요.", "AVERAGE(숫자1, [숫자2], …)"),
    "AVERAGEIF": ("조건에 맞는 값의 평균을 내요.", "AVERAGEIF(조건범위, 조건, [평균범위])"),
    "AVERAGEIFS": ("여러 조건을 만족하는 값의 평균을 내요.", "AVERAGEIFS(평균범위, 조건범위1, 조건1, …)"),
    "MAX": ("범위에서 가장 큰 값을 골라요.", "MAX(숫자1, [숫자2], …)"),
    "MAXIFS": ("조건에 맞는 값 중 가장 큰 값을 골라요.", "MAXIFS(최댓값범위, 조건범위1, 조건1, …)"),
    "MIN": ("범위에서 가장 작은 값을 골라요.", "MIN(숫자1, [숫자2], …)"),
    "MINIFS": ("조건에 맞는 값 중 가장 작은 값을 골라요.", "MINIFS(최솟값범위, 조건범위1, 조건1, …)"),
    "IF": ("조건이 참이면 A, 거짓이면 B를 돌려줘요.", "IF(조건, 참일_때, 거짓일_때)"),
    "IFS": ("여러 조건을 차례로 검사해 처음 맞는 값을 돌려줘요.", "IFS(조건1, 값1, [조건2, 값2], …)"),
    "IFERROR": ("계산이 오류면 대체 값을 돌려줘요.", "IFERROR(값, 오류일_때)"),
    "ROUND": ("지정한 자리에서 반올림해요.", "ROUND(숫자, 자릿수)"),
    "ROUNDUP": ("지정한 자리에서 올림해요.", "ROUNDUP(숫자, 자릿수)"),
    "ROUNDDOWN": ("지정한 자리에서 버림해요.", "ROUNDDOWN(숫자, 자릿수)"),
    "VLOOKUP": ("표 첫 열에서 값을 찾아 같은 행의 다른 열 값을 가져와요.", "VLOOKUP(찾을값, 표범위, 열번호, [정확도])"),
    "HLOOKUP": ("표 첫 행에서 값을 찾아 같은 열의 다른 행 값을 가져와요.", "HLOOKUP(찾을값, 표범위, 행번호, [정확도])"),
    "XLOOKUP": ("범위에서 값을 찾아 대응하는 값을 가져와요.", "XLOOKUP(찾을값, 찾을범위, 반환범위, [없을때])"),
    "INDEX": ("행·열 번호로 표에서 값을 꺼내요.", "INDEX(범위, 행번호, [열번호])"),
    "MATCH": ("범위에서 값의 위치(번호)를 찾아요.", "MATCH(찾을값, 범위, [일치유형])"),
    "LEN": ("글자 수를 세요.", "LEN(문자열)"),
    "LEFT": ("왼쪽에서 몇 글자를 잘라요.", "LEFT(문자열, [개수])"),
    "RIGHT": ("오른쪽에서 몇 글자를 잘라요.", "RIGHT(문자열, [개수])"),
    "MID": ("가운데에서 몇 글자를 잘라요.", "MID(문자열, 시작위치, 개수)"),
    "TODAY": ("오늘 날짜를 돌려줘요.", "TODAY()"),
    "ABS": ("절댓값(부호를 뗀 값)을 돌려줘요.", "ABS(숫자)"),
}


# ── 한국어 조사 ──────────────────────────────────────────────
def _batchim(word):
    if not word:
        return False
    o = ord(word[-1])
    if o < 0xAC00 or o > 0xD7A3:
        return False
    return (o - 0xAC00) % 28 != 0


def _subj(w):
    return "이" if _batchim(w) else "가"


def _obj(w):
    return "을" if _batchim(w) else "를"


def _name_map(m):
    rm = {}
    for r in m.get("reads", []):
        if r.get("name"):
            rm[r["ref"]] = r["name"]
            rm[r["ref"].split("!")[-1]] = r["name"]
    return rm


def named_formula(m):
    """셀 참조를 사람이 읽는 열 이름으로 바꾼 식. 예: =D6+H6 → 발생원가 + 예비비."""
    f = str(m.get("formula", "")).lstrip("=")
    for r in sorted(m.get("reads", []), key=lambda x: -len(x["ref"])):
        name = r.get("name") or r["ref"].split("!")[-1]
        f = f.replace(r["ref"], name)
        bare = r["ref"].split("!")[-1]
        f = re.sub(r"(?<![A-Za-z0-9_!.$])" + re.escape(bare) + r"(?![A-Za-z0-9_(])", name, f)
    return (f.replace("*", " × ").replace("/", " ÷ ")
            .replace("+", " + ").replace("-", " − "))


# ── 조건 → 문장 ──────────────────────────────────────────────
def _cond_phrase(c):
    nm = c.get("target_name") or c.get("target_ref")
    j = _subj(nm)
    v = c.get("value", "")
    op = c.get("operator", "=")
    if op == "=":
        return f"{nm}{j} ‘{v}’인"
    if op == "<>":
        return f"{nm}{j} ‘{v}’{_subj(v)} 아닌"
    if op == ">":
        return f"{nm}{j} {v} 초과인"
    if op == ">=":
        return f"{nm}{j} {v} 이상인"
    if op == "<":
        return f"{nm}{j} {v} 미만인"
    if op == "<=":
        return f"{nm}{j} {v} 이하인"
    return f"{nm} {op} {v}"


# ── 지표 → 일상 언어 설명 ────────────────────────────────────
def explain_metric(m):
    funcs = m.get("functions", [])
    agg = next((f for f in funcs if f in AGG_VERB), None)
    if agg:
        reads = m.get("reads", [])
        src = reads[0]["ref"].split("!")[0] if reads else m.get("sheet", "")
        is_count = agg.startswith("COUNT")
        target = "행의 개수" if is_count else ((reads[0].get("name") if reads else None) or "값")
        conds = []
        for c in m.get("conditions", []):
            nm = c.get("target_name") or c.get("target_ref")
            if c.get("kind") == "match_key":
                conds.append(f"{nm}{_subj(nm)} 같고")
            else:
                conds.append(_cond_phrase(c))
        cond_str = (", ".join(conds) + " 행의 ") if conds else ""
        return f"{src} 시트에서 {cond_str}{target}{_obj(target)} {AGG_VERB[agg]} 값이에요."
    if not funcs:
        return f"이 값은 계산식 그대로예요: {named_formula(m)}"
    interp = interpret_formula(m.get("formula", ""), _name_map(m))
    return interp or f"이 값은 이렇게 계산돼요: {named_formula(m)}"


def functions_doc(functions):
    out = []
    for fn in functions:
        d = FUNC_DOC.get(fn)
        out.append({"name": fn,
                    "how": d[0] if d else "엑셀 함수",
                    "syntax": d[1] if d else f"{fn}(…)"})
    return out


def pythonize(m):
    """정확히 변환 가능할 때만 파이썬 표현을 돌려준다. 아니면 None."""
    funcs = m.get("functions", [])
    agg = next((f for f in funcs if f in PY_AGG), None)
    if agg:
        reads = m.get("reads", [])
        src = reads[0]["ref"].split("!")[0] if reads else m.get("sheet", "")
        target = ((reads[0].get("name") if reads else None) or "값")
        conds = []
        for c in m.get("conditions", []):
            nm = c.get("target_name") or c.get("target_ref")
            op = "==" if c["operator"] == "=" else "!=" if c["operator"] == "<>" else c["operator"]
            val = c.get("value", "")
            rhs = f'이_행["{nm}"]' if c.get("kind") == "match_key" else f'"{val}"'
            conds.append(f'row["{nm}"] {op} {rhs}')
        cond = (" if " + " and ".join(conds)) if conds else ""
        kind = PY_AGG[agg]
        if kind == "count":
            return f"sum(1 for row in {src}{cond})"
        return f'{kind}(row["{target}"] for row in {src}{cond})'
    if not funcs and "&" not in str(m.get("formula", "")):
        return (named_formula(m).replace(" × ", " * ").replace(" ÷ ", " / ")
                .replace(" − ", " - "))
    return None   # 복잡한 수식은 정확히 못 바꿈 → 지어내지 않는다


# ── 수식 → 일상 언어 해석 (IF·LEFT·& 등) ─────────────────────
def _tokenize(f):
    return re.findall(
        r'"(?:[^"]|"")*"|\d+(?:\.\d+)?|<>|<=|>=|[()+\-*/&=<>%,]|[^\s()+\-*/&=<>%,]+', f)


class _Parser:
    def __init__(self, toks):
        self.t = toks
        self.i = 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def eat(self):
        tk = self.peek()
        self.i += 1
        return tk

    def expr(self):
        return self._comparison()

    def _comparison(self):
        l = self._concat()
        while self.peek() in ("=", "<>", "<", ">", "<=", ">="):
            l = {"t": "op", "op": self.eat(), "l": l, "r": self._concat()}
        return l

    def _concat(self):
        l = self._add()
        while self.peek() == "&":
            self.eat()
            l = {"t": "op", "op": "&", "l": l, "r": self._add()}
        return l

    def _add(self):
        l = self._mul()
        while self.peek() in ("+", "-"):
            l = {"t": "op", "op": self.eat(), "l": l, "r": self._mul()}
        return l

    def _mul(self):
        l = self._atom()
        while self.peek() in ("*", "/"):
            l = {"t": "op", "op": self.eat(), "l": l, "r": self._atom()}
        return l

    def _atom(self):
        tk = self.peek()
        if tk is None:
            return {"t": "empty"}
        if tk == "(":
            self.eat()
            e = self.expr()
            if self.peek() == ")":
                self.eat()
            return e
        if tk.startswith('"'):
            self.eat()
            return {"t": "str", "v": tk[1:-1].replace('""', '"')}
        if tk[0].isdigit():
            return {"t": "num", "v": self.eat()}
        self.eat()
        if self.peek() == "(":
            self.eat()
            args = []
            if self.peek() != ")":
                args.append(self.expr())
                while self.peek() == ",":
                    self.eat()
                    args.append(self.expr())
            if self.peek() == ")":
                self.eat()
            return {"t": "fn", "name": tk.upper(), "args": args}
        return {"t": "ref", "v": tk}


_COMP = {
    "=": "가 {r}이면", "<>": "가 {r}이 아니면", ">": "가 {r}보다 크면",
    "<": "가 {r}보다 작으면", ">=": "가 {r} 이상이면", "<=": "가 {r} 이하이면",
}


def _cond_text(node, rm):
    if node and node.get("t") == "op" and node["op"] in _COMP:
        l = _tr(node["l"], rm)
        r = _tr(node["r"], rm)
        return l + _COMP[node["op"]].format(r=r)
    return f"{_tr(node, rm)}이면"


def _tr_fn(node, rm):
    a = [_tr(x, rm) for x in node["args"]]
    n = node["name"]
    if n == "IF":
        return f"{_cond_text(node['args'][0], rm)} → {a[1]}, 아니면 → {a[2]}"
    if n == "IFS":
        parts = []
        k = 0
        while k + 1 < len(node["args"]):
            parts.append(f"{_cond_text(node['args'][k], rm)} → {a[k + 1]}")
            k += 2
        return "; ".join(parts)
    if n == "IFERROR":
        return f"{a[0]} (오류가 나면 {a[1]})"
    if n == "LEFT":
        return f"{a[0]}의 왼쪽 {a[1] if len(a) > 1 else '1'}글자"
    if n == "RIGHT":
        return f"{a[0]}의 오른쪽 {a[1] if len(a) > 1 else '1'}글자"
    if n == "MID":
        return f"{a[0]}의 {a[1]}번째부터 {a[2]}글자"
    if n == "LEN":
        return f"{a[0]}의 글자 수"
    if n == "ROUND":
        return f"{a[0]}을(를) 소수 {a[1]}자리로 반올림한 값"
    if n == "ROUNDUP":
        return f"{a[0]}을(를) 소수 {a[1]}자리로 올림한 값"
    if n == "ROUNDDOWN":
        return f"{a[0]}을(를) 소수 {a[1]}자리로 버림한 값"
    if n == "SUM":
        return f"{', '.join(a)}의 합계"
    if n == "MAX":
        return f"{', '.join(a)} 중 가장 큰 값"
    if n == "MIN":
        return f"{', '.join(a)} 중 가장 작은 값"
    if n == "AVERAGE":
        return f"{', '.join(a)}의 평균"
    if n == "ABS":
        return f"{a[0]}의 절댓값"
    if n == "CONCATENATE":
        return f"{'와 '.join(a)}를 이어붙인 값"
    if n == "VLOOKUP":
        return f"{a[1]}에서 {a[0]}을(를) 찾아 {a[2]}번째 열의 값"
    return f"{n}({', '.join(a)})"


def _tr(node, rm):
    if not node:
        return ""
    t = node.get("t")
    if t == "str":
        return f"‘{node['v']}’"
    if t == "num":
        return node["v"]
    if t == "ref":
        v = node["v"]
        return rm.get(v) or rm.get(v.split("!")[-1]) or v
    if t == "op":
        if node["op"] == "&":
            parts = []

            def collect(nn):
                if nn.get("t") == "op" and nn["op"] == "&":
                    collect(nn["l"])
                    collect(nn["r"])
                else:
                    parts.append(_tr(nn, rm))
            collect(node)
            return ", ".join(parts) + "를 이어붙인 값"
        l = _tr(node["l"], rm)
        r = _tr(node["r"], rm)
        return {"+": f"{l} + {r}", "-": f"{l} − {r}",
                "*": f"{l} × {r}", "/": f"{l} ÷ {r}"}.get(node["op"], f"{l} {node['op']} {r}")
    if t == "fn":
        return _tr_fn(node, rm)
    return ""


def interpret_formula(formula, name_map=None):
    try:
        toks = _tokenize(str(formula).lstrip("="))
        ast = _Parser(toks).expr()
        out = _tr(ast, name_map or {}).strip()
        return out or None
    except Exception:
        return None
