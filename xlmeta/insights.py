"""
xlmeta.insights — 지표들 사이의 관계를 결정론적으로 진단한다. LLM 없이, 수식 구조만으로.

  1) 전이 의존 체인   A→B→C 몇 단계인지 (깊이·경로)
  2) 순환 참조        A→B→A 처럼 도는 관계
  3) 정의된 이름       엑셀 named range (name → 셀)
  4) 정의 불일치       같은 개념(제목)을 두 군데서 다르게 계산 — 킬러 기능
"""

import re
from collections import defaultdict

from . import explain as X


def _col(ref):
    a1 = str(ref).split("!")[-1]
    mo = re.match(r"\$?([A-Z]{1,3})", a1)
    return mo.group(1) if mo else a1


def canonical(m):
    """정의를 셀주소·시트와 무관한 '구조' 문자열로 정규화. 같은 뜻이면 같은 문자열.
       (뺐던 지문 로직 재사용 — 불일치 탐지의 핵심)"""
    funcs = m.get("functions", [])
    agg = next((f for f in funcs if f in X.AGG_VERB), None)
    if agg:
        reads = m.get("reads", [])
        target = (reads[0].get("name") if reads else None) or (_col(reads[0]["ref"]) if reads else "?")
        parts = []
        for c in m.get("conditions", []):
            nm = c.get("target_name") or _col(c.get("target_ref", ""))
            if c.get("kind") == "match_key":
                parts.append(f"{nm}~match")
            else:
                parts.append(f"{nm}{c.get('operator', '=')}{c.get('value', '')}")
        parts.sort()
        return f"{agg}({target};{';'.join(parts)})"
    s = X.named_formula(m)
    s = re.sub(r"\$?[A-Z]{1,3}\$?\d+(?::\$?[A-Z]{1,3}\$?\d+)?",
               lambda mo: re.sub(r"\d+", "", mo.group()), s)
    return re.sub(r"\s+", "", s)


def _cycles(by_id, name):
    color, found = {}, []

    def dfs(u, path):
        color[u] = 1
        path.append(u)
        for v in by_id[u].get("depends_on", []):
            if v not in by_id:
                continue
            if color.get(v) == 1:                       # 회색 = 조상 → 역방향 간선 = 순환
                found.append(path[path.index(v):] + [v])
            elif color.get(v, 0) == 0:
                dfs(v, path)
        color[u] = 2
        path.pop()

    for u in by_id:
        if color.get(u, 0) == 0:
            dfs(u, [])

    seen, out = set(), []
    for cyc in found:
        key = frozenset(cyc)
        if key in seen:
            continue
        seen.add(key)
        out.append({"ids": cyc, "chain": " → ".join(name(x) for x in cyc)})
    return out


def _chains(by_id, name, in_cycle):
    memo = {}

    def depth(mid, stack):
        if mid in stack:
            return 0, [mid]
        if mid in memo:
            return memo[mid]
        deps = [d for d in by_id[mid].get("depends_on", []) if d in by_id]
        if not deps:
            memo[mid] = (0, [mid])
            return memo[mid]
        best = max((depth(d, stack | {mid}) for d in deps), key=lambda t: t[0])
        memo[mid] = (best[0] + 1, [mid] + best[1])
        return memo[mid]

    out = []
    for mid in by_id:
        if mid in in_cycle:
            continue
        d, path = depth(mid, set())
        if d >= 1:                                      # 한 단계 이상 의존하는 것만
            out.append({"metric": name(mid), "depth": d,
                        "chain": " → ".join(name(x) for x in path)})
    out.sort(key=lambda x: -x["depth"])
    return out


def _inconsistencies(metrics):
    by_title = defaultdict(list)
    for m in metrics:
        t = (m.get("title") or "").strip()
        if t:
            by_title[t].append(m)

    out = []
    for concept, ms in by_title.items():
        if len(ms) < 2:
            continue
        if len({canonical(m) for m in ms}) > 1:         # 같은 이름인데 정의가 다름
            out.append({
                "concept": concept,
                "definitions": [{
                    "where": m["region_title"] or m["sheet"],
                    "cell": m["anchor_cell"],
                    "formula": m["formula"],
                    "canonical": canonical(m),
                } for m in ms],
            })
    return out


def insights_md(ins):
    """진단 결과를 마크다운으로. 불일치는 맨 위에(가장 중요)."""
    if not ins or not any([ins["inconsistencies"], ins["chains"], ins["cycles"], ins["named_ranges"]]):
        return ""
    L = ["## 진단 · 관계와 불일치", ""]
    if ins["inconsistencies"]:
        L += ["### ⚠️ 정의 불일치 — 같은 개념을 다르게 계산", ""]
        for inc in ins["inconsistencies"]:
            L.append(f"- **{inc['concept']}** 이(가) {len(inc['definitions'])}곳에서 다르게 계산돼요:")
            for d in inc["definitions"]:
                L.append(f"  - {d['where']}: `{d['formula']}`")
        L.append("")
    if ins["chains"]:
        L += ["### 의존 체인 (몇 단계 계산인지)", ""]
        L += [f"- {c['chain']}  ({c['depth']}단계)" for c in ins["chains"]]
        L.append("")
    if ins["cycles"]:
        L += ["### 순환 참조 (도는 관계)", ""]
        L += [f"- {c['chain']}" for c in ins["cycles"]]
        L.append("")
    if ins["named_ranges"]:
        L += ["### 정의된 이름 (named range)", ""]
        L += [f"- `{n['name']}` → `{n['refers_to']}`" for n in ins["named_ranges"]]
        L.append("")
    return "\n".join(L).rstrip()


def analyze(meta):
    metrics = [m for m in meta["metrics"] if m["confidence"]["level"] != "low"]
    by_id = {m["id"]: m for m in metrics}
    name = lambda mid: (by_id[mid]["title"] or mid) if mid in by_id else mid

    cycles = _cycles(by_id, name)
    in_cycle = {mid for c in cycles for mid in c["ids"]}     # 순환에 낀 건 체인 계산 제외

    return {
        "chains": _chains(by_id, name, in_cycle),
        "cycles": [{"chain": c["chain"]} for c in cycles],
        "named_ranges": meta.get("named_ranges", []),
        "inconsistencies": _inconsistencies(metrics),
    }
