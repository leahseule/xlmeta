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


def _sig(m):
    """정의를 비교 가능한 성분으로 분해: 집계함수 · 집계단위(grouping) · 필터 · 대상열."""
    agg = next((f for f in m.get("functions", []) if f in X.AGG_VERB), None)
    grouping = tuple(sorted((c.get("target_name") or _col(c.get("target_ref", "")))
                            for c in m.get("conditions", []) if c.get("kind") == "match_key"))
    filters = tuple(sorted(f"{c.get('target_name') or _col(c.get('target_ref', ''))}"
                           f"{c.get('operator')}{c.get('value')}"
                           for c in m.get("conditions", []) if c.get("kind") == "business_rule"))
    target = None
    if m.get("reads"):
        r0 = m["reads"][0]
        target = r0.get("name") or _col(r0["ref"])
    return {"agg": agg, "grouping": grouping, "filters": filters,
            "target": target, "canonical": canonical(m)}


def _pair_severity(a, b):
    """두 정의 차이의 심각도. 집계단위(grouping)가 다르면 입도 차이(INFO),
       같은 단위인데 집계함수·조건·대상열이 다르면 진짜 버그(HIGH)."""
    if a["grouping"] != b["grouping"]:
        return "info"                       # 입도가 다름 = 같은 라벨 다른 집계 단위
    if a["agg"] != b["agg"] or a["filters"] != b["filters"] or a["target"] != b["target"]:
        return "high"                       # 같은 단위인데 집계·조건·대상이 다름
    return None


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
        distinct = {}                        # 복제본은 canonical로 collapse
        for m in ms:
            s = _sig(m)
            distinct.setdefault(s["canonical"], (s, m))
        if len(distinct) < 2:
            continue                         # 전부 복제 → 불일치 아님(억제)
        sigs = [v[0] for v in distinct.values()]
        sev = "info"
        for i in range(len(sigs)):
            for j in range(i + 1, len(sigs)):
                if _pair_severity(sigs[i], sigs[j]) == "high":
                    sev = "high"
        out.append({
            "concept": concept,
            "severity": sev,
            "definitions": [{
                "where": m["region_title"] or m["sheet"],
                "cell": m["anchor_cell"],
                "formula": m["formula"],
            } for (_s, m) in distinct.values()],
        })
    out.sort(key=lambda x: 0 if x["severity"] == "high" else 1)
    return out


def insights_md(ins):
    """진단 결과를 마크다운으로. 불일치는 맨 위에(가장 중요)."""
    if not ins or not any([ins["inconsistencies"], ins["chains"], ins["cycles"], ins["named_ranges"]]):
        return ""
    L = ["## 진단 · 관계와 불일치", ""]
    highs = [i for i in ins["inconsistencies"] if i["severity"] == "high"]
    infos = [i for i in ins["inconsistencies"] if i["severity"] == "info"]
    if highs:
        L += ["### ⚠️ 정의 불일치 (HIGH) — 같은 단위인데 다르게 계산", ""]
        for inc in highs:
            L.append(f"- **{inc['concept']}** 이(가) {len(inc['definitions'])}곳에서 다르게 계산돼요:")
            for d in inc["definitions"]:
                L.append(f"  - {d['where']}: `{d['formula']}`")
        L.append("")
    if infos:
        L += ["### 명명 참고 (INFO) — 같은 라벨, 다른 집계 단위(입도)", ""]
        L += [f"- {i['concept']} ({len(i['definitions'])}곳, 집계 단위가 달라 값이 다름)" for i in infos]
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
