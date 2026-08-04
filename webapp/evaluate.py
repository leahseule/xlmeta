"""
webapp.evaluate — 수식의 실제 계산값을 직접 산출한다.

openpyxl은 수식 결과를 캐시하지 않으므로(코드로 만든 샘플은 특히 값이 없다),
xlmeta가 다루는 함수(SUMIFS 계열 · SUM · 사칙연산)에 한해 값을 계산해
화면에 "이 칸의 값"으로 보여줄 수 있게 한다. 계산 못 하는 수식은 None을 돌려준다.
"""

import re
from openpyxl.utils import range_boundaries

AGG = ("SUMIFS", "SUMIF", "AVERAGEIFS", "AVERAGEIF", "COUNTIFS", "COUNTIF", "SUM")

REF = re.compile(
    r"(?:(?P<sheet>'[^']+'|[A-Za-z가-힣_][A-Za-z0-9가-힣_.]*)!)?"
    r"(?P<a1>\$?[A-Z]{1,3}\$?\d{1,7}(?::\$?[A-Z]{1,3}\$?\d{1,7})?"
    r"|\$?[A-Z]{1,3}:\$?[A-Z]{1,3})"
)


def _split_args(s):
    out, buf, depth, q = [], "", 0, False
    for ch in s:
        if ch == '"':
            q = not q
        if not q:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                out.append(buf.strip()); buf = ""; continue
        buf += ch
    if buf.strip():
        out.append(buf.strip())
    return out


def _to_num(x):
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        try:
            return float(x.replace(",", ""))
        except ValueError:
            return None
    return None


class Evaluator:
    """워크북(data_only=False) 위에서 수식 값을 재귀·메모이즈하며 계산한다."""

    def __init__(self, wb):
        self.wb = wb
        self.cache = {}

    def value(self, sheet, r, c):
        key = (sheet, r, c)
        if key in self.cache:
            return self.cache[key]
        self.cache[key] = None                      # 순환 참조 방어
        try:
            v = self.wb[sheet].cell(row=r, column=c).value
        except Exception:
            return None
        if isinstance(v, str) and v.startswith("="):
            try:
                v = self._expr(v[1:], sheet)
            except Exception:
                v = None
        self.cache[key] = v
        return v

    # ── 참조 해석 ──────────────────────────────────────────────
    def _split(self, ref, home):
        ref = ref.strip()
        if "!" in ref:
            sh, a1 = ref.split("!", 1)
            return sh.strip().strip("'"), a1.strip()
        return home, ref

    def _ref_value(self, ref, home):
        sh, a1 = self._split(ref, home)
        minc, minr, _maxc, _maxr = range_boundaries(a1.replace("$", ""))
        return self.value(sh, minr or 1, minc)

    def _coords(self, ref, home):
        sh, a1 = self._split(ref, home)
        ws = self.wb[sh]
        minc, minr, maxc, maxr = range_boundaries(a1.replace("$", ""))
        minr = minr or 1
        maxr = maxr or (ws.max_row or 1)
        return sh, [(r, c) for r in range(minr, maxr + 1) for c in range(minc, maxc + 1)]

    # ── 식 평가 ────────────────────────────────────────────────
    def _expr(self, expr, sheet):
        expr = expr.strip()
        m = re.fullmatch(r"([A-Za-z]+)\s*\((.*)\)", expr, re.S)
        if m and m.group(1).upper() in AGG:
            return self._agg(m.group(1).upper(), m.group(2), sheet)
        return self._arith(expr, sheet)

    def _arith(self, expr, sheet):
        def repl(mo):
            ref = (mo.group("sheet") + "!" if mo.group("sheet") else "") + mo.group("a1")
            n = _to_num(self._ref_value(ref, sheet))
            return str(n if n is not None else 0)
        s = REF.sub(repl, expr)
        if not re.fullmatch(r"[0-9.\s+\-*/()eE]+", s):
            return None
        try:
            return eval(s, {"__builtins__": {}}, {})   # 숫자·연산자만 통과
        except Exception:
            return None

    def _agg(self, fn, args_str, sheet):
        args = _split_args(args_str)
        if fn == "SUM":
            total = 0.0
            for a in args:
                sh, coords = self._coords(a, sheet)
                for (r, c) in coords:
                    n = _to_num(self.value(sh, r, c))
                    if n is not None:
                        total += n
            return total

        if fn in ("SUMIF", "AVERAGEIF"):
            rng, crit = args[0], args[1]
            sum_ref = args[2] if len(args) > 2 else args[0]
            csh, ccoords = self._coords(rng, sheet)
            ssh, scoords = self._coords(sum_ref, sheet)
            total, cnt = 0.0, 0
            for i, (r, c) in enumerate(ccoords):
                if self._match(self.value(csh, r, c), crit, sheet) and i < len(scoords):
                    n = _to_num(self.value(ssh, *scoords[i]))
                    if n is not None:
                        total += n; cnt += 1
            return (total / cnt if cnt else None) if fn == "AVERAGEIF" else total

        if fn == "COUNTIF":
            csh, ccoords = self._coords(args[0], sheet)
            return sum(1 for (r, c) in ccoords
                       if self._match(self.value(csh, r, c), args[1], sheet))

        if fn == "COUNTIFS":
            pairs = list(zip(args[0::2], args[1::2]))
            _sh, base = self._coords(pairs[0][0], sheet)
            return sum(1 for i in range(len(base))
                       if all(self._pair_match(cr, cv, i, sheet) for cr, cv in pairs))

        # SUMIFS / AVERAGEIFS
        sum_ref = args[0]
        pairs = list(zip(args[1::2], args[2::2]))
        ssh, scoords = self._coords(sum_ref, sheet)
        total, cnt = 0.0, 0
        for i, (r, c) in enumerate(scoords):
            if all(self._pair_match(cr, cv, i, sheet) for cr, cv in pairs):
                n = _to_num(self.value(ssh, r, c))
                if n is not None:
                    total += n; cnt += 1
        return (total / cnt if cnt else None) if fn == "AVERAGEIFS" else total

    def _pair_match(self, crit_range, crit, i, sheet):
        csh, ccoords = self._coords(crit_range, sheet)
        if i >= len(ccoords):
            return False
        return self._match(self.value(csh, *ccoords[i]), crit, sheet)

    def _match(self, cell_v, crit, home):
        crit = crit.strip()
        if crit.startswith('"') and crit.endswith('"'):
            target = crit[1:-1]
        elif re.fullmatch(r"-?\d+(\.\d+)?", crit):
            target = crit
        elif REF.fullmatch(crit):
            target = self._ref_value(crit, home)
        else:
            target = crit
        op = "="
        if isinstance(target, str):
            for cand in ("<>", ">=", "<=", ">", "<"):
                if target.startswith(cand):
                    op, target = cand, target[len(cand):]
                    break
        cn, tn = _to_num(cell_v), _to_num(target)
        if cn is not None and tn is not None:
            a, b = cn, tn
        else:
            a = "" if cell_v is None else str(cell_v)
            b = "" if target is None else str(target)
        return {
            "=": a == b, "<>": a != b, ">": a > b,
            "<": a < b, ">=": a >= b, "<=": a <= b,
        }.get(op, False)
