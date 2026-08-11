"""
xlmeta.summary — 뽑아낸 구조/규칙을 사람·AI가 한눈에 읽을 짧은 요약으로 조립한다.

추론하지 않는다. 모든 문장은 추출된 사실에서만 나온다. 재실행하면 같은 결과.
AI에게 넘길 공개 링크 페이지의 본문이 되고, 프리필 헤드라인도 여기서 만든다.
원시 데이터 값은 담지 않는다 — 구조·이름·수식·업무규칙만 (링크로 외부에 나가므로).
"""

from collections import defaultdict

from . import explain as X


def _ro(w):
    """조사 '(으)로'. 받침 없거나 ㄹ 받침 → '로', 그 외 → '으로'."""
    if not w:
        return "로"
    ch = w[-1]
    if not ("가" <= ch <= "힣"):
        return "로"
    jong = (ord(ch) - 0xAC00) % 28
    return "로" if jong in (0, 8) else "으로"


def _rules_of(metrics):
    """지표들에 걸린 업무 규칙(수식 내 조건)을 중복 없이 모은다."""
    seen, out = set(), []
    for m in metrics:
        for c in m.get("conditions", []):
            if c.get("kind") != "business_rule":
                continue
            nm = c.get("target_name") or c.get("target_ref")
            op = c.get("operator", "=")
            val = c.get("value")
            key = (nm, op, val)
            if key in seen:
                continue
            seen.add(key)
            out.append(f"{nm} {op} {val}")
    return out


def _manual_cells(metrics):
    seen = set()
    for m in metrics:
        for o in m.get("manual_overrides", []):
            seen.add(o["cell"])
    return sorted(seen)


def _paragraph(name, regions, named, n_formula, n_manual):
    """이 시트가 무엇을 관리하는 문서인지 한 문단. 사실만 조립.
       데이터 행이 2개 이상인 것만 '표'로 본다(머리글만 있는 문서 블록 제외).
       너무 긴 컬럼명(설명이 식별 열로 오인된 것)은 문장에서 뺀다."""
    tables = [r for r in regions if r["row_count"] >= 2]
    if not tables and not n_formula:
        return (f"‘{name}’ 시트는 표로 인식된 데이터 영역이 없어요. "
                "설명·안내용 시트로 보여요.")
    S = []
    if tables:
        main = max(tables, key=lambda r: r["row_count"])
        tname = main["title"] or main["range"]
        if len(tables) == 1:
            S.append(f"‘{name}’ 시트는 ‘{tname}’ 표를 담고 있어요.")
        else:
            S.append(f"‘{name}’ 시트는 표 {len(tables)}개를 담고 있어요(가장 큰 건 ‘{tname}’).")
        kc_all = [k for k in main["key_columns"] if len(k) <= 20]
        kc = kc_all[:4]
        if kc:
            suffix = " 등" if len(kc_all) > 4 else ""
            label = "·".join(kc) + suffix
            josa = _ro("등") if suffix else _ro(kc[-1])
            S.append(f"{label}{josa} 행을 구분하는 {main['row_count']}행짜리 표예요.")
        else:
            S.append(f"{main['row_count']}행짜리 표예요.")
    else:
        S.append(f"‘{name}’ 시트에는 또렷한 표 영역은 없지만 계산 수식이 있어요.")

    if named:
        top = "·".join(m["title"] for m in named[:3])
        more = f" 등 {len(named)}개" if len(named) > 3 else f" {len(named)}개"
        refs = sorted({r["ref"].split("!")[0] for m in named for r in m.get("reads", [])
                       if "!" in r["ref"] and r["ref"].split("!")[0] != name})
        funcs = sorted({f for m in named for f in m.get("functions", [])})
        fstr = f" ({'·'.join(funcs)})" if funcs else ""
        if refs:
            S.append(f"{top}{more} 값을 {'·'.join(refs)} 시트의 데이터로 계산해요{fstr}.")
        else:
            S.append(f"{top}{more} 값을 이 시트 안에서 계산해요{fstr}.")
        rules = _rules_of(named)
        if rules:
            S.append(f"조건으로 {', '.join(rules)}를 걸어요.")

    if n_formula:
        tail = f"수식 셀 {n_formula}개"
        if n_manual:
            tail += f", 사람이 직접 넣은 값 {n_manual}개(완전 자동은 아님)"
        S.append(tail + ".")
    return " ".join(S)


def _card(name, srcs, metrics, fcell_refs, cell_graph):
    regions = [{
        "range": s["range"].split("!")[-1],
        "title": s["title"],
        "title_cell": s.get("title_cell"),
        "confidence": s["layout_confidence"],
        "header_rows": s["header_rows"],
        "key_columns": [s["columns"].get(c, c) for c in s["key_columns"]],
        "columns": list(s["columns"].values()),
        "row_count": s["row_count"],
    } for s in srcs]

    named = [m for m in metrics if m.get("title")]
    key_columns = []
    for r in regions:
        for c in r["key_columns"]:
            if c not in key_columns:
                key_columns.append(c)

    examples = []
    for ref in fcell_refs[:5]:
        examples.append({"cell": ref.split("!")[-1],
                         "formula": cell_graph[ref]["formula"]})

    metric_cards = [{
        "title": m["title"],
        "formula": m["formula"],
        "functions": m.get("functions", []),
        "rules": _rules_of([m]),
        "ref_sheets": sorted({r["ref"].split("!")[0] for r in m.get("reads", [])
                              if "!" in r["ref"] and r["ref"].split("!")[0] != name}),
    } for m in named]

    n_manual = len(_manual_cells(named))
    return {
        "sheet": name,
        "paragraph": _paragraph(name, regions, named, len(fcell_refs), n_manual),
        "regions": regions,
        "key_columns": key_columns,
        "formula_cell_count": len(fcell_refs),
        "formula_examples": examples,
        "metrics": metric_cards,
        "manual_count": n_manual,
    }


def summarize(meta):
    """extract() 결과 → 시트별 요약 카드 묶음."""
    by_src = defaultdict(list)
    for s in meta["sources"]:
        by_src[s["sheet"]].append(s)
    by_metric = defaultdict(list)
    for m in meta["metrics"]:
        by_metric[m["sheet"]].append(m)
    by_fcell = defaultdict(list)
    for ref in meta["cell_graph"]:
        by_fcell[ref.split("!")[0]].append(ref)

    order = meta.get("sheets") or list(dict.fromkeys(s["sheet"] for s in meta["sources"]))
    cards = [_card(name, by_src[name], by_metric[name],
                   by_fcell.get(name, []), meta["cell_graph"]) for name in order]

    return {
        "source_file": meta["source_file"],
        "sheets": cards,
        "totals": {
            "sheets": len(cards),
            "tables": sum(len(c["regions"]) for c in cards),
            "metrics": sum(len(c["metrics"]) for c in cards),
            "formula_cells": sum(c["formula_cell_count"] for c in cards),
        },
    }


def markdown(summary):
    """공개 페이지·복사용 마크다운. 사람도 AI도 그대로 읽는다."""
    t = summary["totals"]
    L = [f"# {summary['source_file']} — 엑셀 구조 요약", "",
         "> xlmeta가 수식·레이아웃에서 (LLM 없이) 결정론적으로 추출했습니다. "
         "재실행하면 같은 결과. 원시 데이터 값은 담지 않고 구조·규칙만 담습니다.", "",
         f"**한눈에** · 시트 {t['sheets']}개 · 표 {t['tables']}개 · "
         f"지표 {t['metrics']}개 · 수식 셀 {t['formula_cells']}개", ""]
    for c in summary["sheets"]:
        L += ["---", "", f"## 시트: {c['sheet']}", "", c["paragraph"], ""]
        if c["regions"]:
            L += ["**주요 표 영역**", ""]
            for r in c["regions"]:
                ttl = f" — {r['title']}" if r["title"] else ""
                L.append(f"- `{r['range']}` ({r['confidence']}, {r['row_count']}행){ttl}")
            L.append("")
        if c["key_columns"]:
            L += [f"**핵심 컬럼(행 식별)**: {', '.join(c['key_columns'])}", ""]
        headers = []
        for r in c["regions"]:
            headers += [x for x in r["columns"] if x not in headers]
        if headers:
            L += [f"**헤더(열 이름)**: {', '.join(headers)}", ""]
        if c["formula_cell_count"]:
            L += [f"**수식 셀**: {c['formula_cell_count']}개"
                  + (f", 사람이 직접 넣은 값 {c['manual_count']}개" if c["manual_count"] else "")]
            for ex in c["formula_examples"]:
                L.append(f"  - `{ex['cell']}` = `{ex['formula']}`")
            L.append("")
        if c["metrics"]:
            L += ["**지표 · 업무규칙**", ""]
            for m in c["metrics"]:
                rule = f" — 조건: {', '.join(m['rules'])}" if m["rules"] else ""
                L.append(f"- **{m['title']}** `{m['formula']}`{rule}")
            L.append("")
    return "\n".join(L).rstrip() + "\n"


def headline(summary, url):
    """AI 프리필용 짧은 한 줄 + 링크. URL 길이 걱정 없이 넘긴다."""
    t = summary["totals"]
    return (f"‘{summary['source_file']}’ 엑셀의 구조 요약이야 "
            f"(시트 {t['sheets']}·표 {t['tables']}·지표 {t['metrics']}). "
            "표·핵심 컬럼·수식·업무규칙을 이 링크에 정리해뒀어. "
            "이걸 근거로 내 질문에 답하고, 요약에 없는 건 모른다고 해줘. "
            f"링크를 못 열면 알려줘.\n{url}")
