"""
xlmeta.emit_okf — 추출 결과를 Google OKF v0.1 번들로 쓴다.
번들 = YAML 프론트매터가 붙은 마크다운 디렉터리.
"""

import json
import os
import re
from datetime import datetime, timezone

from .summary import file_tree, _tables, data_table_md, _fmt_val
from .insights import insights_md
from .concepts import concepts_md


def slug(s, fallback="unnamed"):
    if not s:
        return fallback
    s = re.sub(r"[\\/:*?\"<>|]+", "-", s).strip()
    s = re.sub(r"\s+", "-", s)
    return s[:60] or fallback


def yaml_val(v):
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if any(ch in s for ch in ":#{}[]&*!|>'\"%@`,") or s.strip() != s:
        return '"' + s.replace('"', '\\"') + '"'
    return s


def frontmatter(d):
    out = ["---"]
    for k, v in d.items():
        if isinstance(v, list):
            if not v:
                continue
            out.append(f"{k}: [" + ", ".join(yaml_val(x) for x in v) + "]")
        else:
            out.append(f"{k}: {yaml_val(v)}")
    out.append("---")
    return "\n".join(out)


def write_bundle(meta, outdir):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    src = meta["source_file"]
    os.makedirs(os.path.join(outdir, "metrics"), exist_ok=True)
    os.makedirs(os.path.join(outdir, "sources"), exist_ok=True)

    metric_files, source_files = [], []

    # ── 원천(표) 개념 ────────────────────────────────────────────
    for s in meta["sources"]:
        name = s["title"] or f"{s['sheet']} {s['range'].split('!')[1]}"
        fn = slug(name)
        source_files.append((fn, name, s))
        fmd = {
            "type": "Spreadsheet Table",
            "title": name,
            "description": f"{src}의 {s['range']} 영역 ({s['row_count']}행)",
            "resource": f"file://{src}#{s['range']}",
            "timestamp": now,
            "layout_confidence": s["layout_confidence"],
            "key_columns": s["key_columns"],
        }
        if s.get("title_cell"):
            fmd["title_cell"] = f"{s['sheet']}!{s['title_cell']}"
        fm = frontmatter(fmd)
        body = [""]
        if s.get("title_cell"):
            body += [f"> 제목 셀(추측): `{s['sheet']}!{s['title_cell']}` — 단독 텍스트라 데이터가 아니라 표 이름으로 판단.", ""]
        body += ["# Schema", "", "| 열 | 이름 |", "|---|---|"]
        for col, nm in s["columns"].items():
            body.append(f"| `{col}` | {nm} |")
        if s["subtotal_rows"]:
            body += ["", "# 주의", "",
                     f"소계/합계 행: {s['subtotal_rows']} — 지표 추출에서 제외됨."]
        with open(os.path.join(outdir, "sources", fn + ".md"), "w", encoding="utf-8") as f:
            f.write(fm + "\n".join(body) + "\n")

    # ── 지표 개념 ───────────────────────────────────────────────
    id2file = {}
    for m in meta["metrics"]:
        if m["confidence"]["level"] == "low":
            continue
        title = m["title"] or m["id"]
        fn = slug(f"{title}-{m['id']}")
        id2file[m["id"]] = (fn, title)

    for m in meta["metrics"]:
        if m["id"] not in id2file:
            continue
        fn, title = id2file[m["id"]]
        rules = [c for c in m["conditions"] if c["kind"] == "business_rule"]
        desc = (f"{m['region_title'] or m['sheet']}의 {m['name']}"
                + (f" — {rules[0].get('target_name') or rules[0]['target_ref']}"
                   f" {rules[0]['operator']} {rules[0]['value']} 조건 적용" if rules else ""))
        fm = frontmatter({
            "type": "Metric",
            "title": title,
            "description": desc,
            "resource": f"file://{src}#{m['applies_to']}",
            "tags": [m["sheet"]],
            "timestamp": now,
            "source_formula": m["formula"],
            "confidence": m["confidence"]["level"],
            "manual_override_count": len(m["manual_overrides"]),
            "derivation": "deterministic-formula-parse",
        })
        b = ["", f"`{m['applies_to']}` · 원본 수식 `{m['formula']}`", ""]

        if m.get("explanation"):
            b += ["# 계산 설명", "", m["explanation"], ""]

        if m.get("functions_doc"):
            b += ["# 쓰인 함수", "", "| 함수 | 설명 | 문법 |", "|---|---|---|"]
            for fd in m["functions_doc"]:
                b.append(f"| `{fd['name']}` | {fd['how']} | `{fd['syntax']}` |")
            b.append("")

        if m.get("python"):
            b += ["# Python", "",
                  "정확히 변환 가능한 계산만 제공. 복잡한 수식은 지어내지 않음.", "",
                  "```python", m["python"], "```", ""]

        if rules:
            b += ["# 업무 규칙", "",
                  "수식에 내재된 조건. 규정 문서가 아니라 셀에서 추출됨.", "",
                  "| 대상 | 연산 | 값 |", "|---|---|---|"]
            for c in rules:
                b.append(f"| {c.get('target_name') or c['target_ref']} "
                         f"| `{c['operator']}` | `{c['value']}` |")
            b.append("")

        keys = [c for c in m["conditions"] if c["kind"] == "match_key"]
        if keys:
            b += ["# 매칭 키", ""]
            for c in keys:
                b.append(f"- {c.get('target_name') or c['target_ref']} = `{c['value']}`")
            b.append("")

        if m["reads"]:
            b += ["# 원천", "", "| 참조 | 의미 |", "|---|---|"]
            seen = set()
            for r in m["reads"]:
                if r["ref"] in seen:
                    continue
                seen.add(r["ref"])
                b.append(f"| `{r['ref']}` | {r['name'] or '_미확인_'} |")
            b.append("")

        if m["constants"]:
            b += ["# 하드코딩 상수", "",
                  "수식에 직접 박힌 값. 문서화되지 않은 기준일 수 있음.", "",
                  ", ".join(f"`{x}`" for x in m["constants"]), ""]

        if m["manual_overrides"]:
            b += ["# 수기 개입", "",
                  "수식이 있어야 할 자리에 사람이 값을 넣은 셀. "
                  "이 지표는 완전 자동 계산이 아님.", "",
                  "| 셀 | 행 | 값 |", "|---|---|---|"]
            for o in m["manual_overrides"]:
                b.append(f"| `{o['cell']}` | {o['row_label'] or '-'} | {o['value']} |")
            b.append("")

        links = [f"- [{id2file[d][1]}](/metrics/{id2file[d][0]}.md)"
                 for d in m["depends_on"] if d in id2file]
        used = [f"- [{id2file[u][1]}](/metrics/{id2file[u][0]}.md)"
                for u in m.get("used_by", []) if u in id2file]
        if links:
            b += ["# 참조하는 지표", ""] + links + [""]
        if used:
            b += ["# 이 지표를 쓰는 곳", ""] + used + [""]

        srcfn = next((f for f, n, s in source_files if s["range"] == m["region"]), None)
        if srcfn:
            b += ["# 소속 표", "", f"[{m['region_title'] or m['region']}]"
                  f"(/sources/{srcfn}.md)", ""]

        with open(os.path.join(outdir, "metrics", fn + ".md"), "w", encoding="utf-8") as f:
            f.write(fm + "\n".join(b))

    # ── 인덱스 ──────────────────────────────────────────────────
    by_id = {m["id"]: m for m in meta["metrics"]}

    with open(os.path.join(outdir, "metrics", "index.md"), "w", encoding="utf-8") as f:
        f.write("# 지표\n\n" + "\n".join(
            f"* [{t}]({fn}.md) — {by_id[i]['name']}"
            for i, (fn, t) in id2file.items()) + "\n")
    with open(os.path.join(outdir, "sources", "index.md"), "w", encoding="utf-8") as f:
        f.write("# 원천 표\n\n" + "\n".join(
            f"* [{n}]({fn}.md) - {s['range']} ({s['row_count']}행)"
            for fn, n, s in source_files) + "\n")

    low = [m for m in meta["metrics"] if m["confidence"]["level"] == "low"]
    root = [frontmatter({"okf_version": "0.1", "type": "Knowledge Bundle",
                         "title": f"{src} 지표 정의",
                         "description": "엑셀 수식에서 추론 없이 추출한 지표 정의",
                         "timestamp": now}),
            "", "# 목차", "",
            f"* [지표](metrics/) - 추출된 지표 {len(id2file)}건",
            f"* [원천 표](sources/) - {len(source_files)}개 영역", "",
            "# 생성 방식", "",
            "수식을 결정론적으로 파싱해 생성. LLM 추론 없음. "
            "같은 입력이면 같은 출력이며 재실행으로 검증 가능.", ""]
    if low:
        root += ["# 제외된 항목", "",
                 f"이름을 확정하지 못했거나 반복이 부족한 {len(low)}건은 번들에서 제외됨:", ""]
        root += [f"* `{m['anchor_cell']}` — {m['formula'][:60]}" for m in low[:20]]
        root += ["", "추측 대신 누락을 택함. `xlmeta.override.yaml`로 이름을 지정할 수 있음.", ""]
    if meta["unsupported"]:
        root += ["# 미지원", ""] + [f"* `{u['cell']}` — {u['reason']}"
                                    for u in meta["unsupported"][:20]] + [""]
    with open(os.path.join(outdir, "index.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(root))

    # 엑셀 하나 = OKF 한 문서. 흩어진 파일 대신 에이전트가 한 번에 읽는다.
    with open(os.path.join(outdir, "okf.md"), "w", encoding="utf-8") as f:
        f.write(render_single(meta))

    # 셀 원장은 개념이 아니라 원시 데이터 → OKF 밖에 둔다
    with open(os.path.join(outdir, "cell_graph.json"), "w", encoding="utf-8") as f:
        json.dump(meta["cell_graph"], f, ensure_ascii=False, indent=2, default=str)

    return {"metrics": len(id2file), "sources": len(source_files),
            "excluded": len(low), "cells": len(meta["cell_graph"])}


def render_single(meta):
    """엑셀 하나의 지식을 '한 문서'로 합쳐 마크다운 문자열로 돌려준다.
       여러 파일로 흩어진 번들 대신, AI 에이전트가 한 번에 읽을 단일 OKF."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    src = meta["source_file"]
    used = [m for m in meta["metrics"] if m["confidence"]["level"] != "low"]
    low = [m for m in meta["metrics"] if m["confidence"]["level"] == "low"]

    L = [frontmatter({
        "okf_version": "0.1", "type": "Knowledge Bundle",
        "title": f"{src} 지식 번들", "source_file": src,
        "description": "엑셀 수식에서 추론 없이 추출한 지표 정의 (단일 문서)",
        "timestamp": now, "derivation": "deterministic-formula-parse",
    }), "",
        f"# {src} — 스프레드시트 지식 표현", "",
        "*Spreadsheet Knowledge Representation* — 엑셀 수식·레이아웃에서 (LLM 없이) "
        "결정론적으로 추출했습니다. 재실행하면 같은 결과. "
        "구조·실제 데이터·계산 규칙·계산 계보·불일치·개념을 한 문서로 담습니다.", "",
        f"**요약** · 지표 {len(used)}건 · 원천 표 {len(meta['sources'])}개 · 수식 셀 {len(meta['cell_graph'])}개", "",
        "## 구조 (파일 → 시트 → 표 → 컬럼)", "", "```text", file_tree(meta), "```", ""]

    ins_md = insights_md(meta.get("insights"))
    if ins_md:
        L += [ins_md, ""]

    con_md = concepts_md(meta.get("concepts"))
    if con_md:
        L += [con_md, ""]

    if used:
        L += ["## 지표", ""]
        for m in used:
            L += [f"### {m['title'] or m['id']}", "",
                  f"- 적용 범위: `{m['applies_to']}`",
                  f"- 원본 수식: `{m['formula']}`"]
            if m.get("explanation"):
                L.append(f"- 계산 설명: {m['explanation']}")
            rules = [c for c in m["conditions"] if c["kind"] == "business_rule"]
            if rules:
                L.append("- 업무 규칙: " + ", ".join(
                    f"{c.get('target_name') or c['target_ref']} {c['operator']} {c['value']}" for c in rules))
            keys = [c for c in m["conditions"] if c["kind"] == "match_key"]
            if keys:
                L.append("- 매칭 키: " + ", ".join(
                    f"{c.get('target_name') or c['target_ref']} = {c['value']}" for c in keys))
            seen, reads = set(), []
            for r in m["reads"]:
                if r["ref"] in seen:
                    continue
                seen.add(r["ref"])
                reads.append(f"`{r['ref']}`" + (f"({r['name']})" if r["name"] else ""))
            if reads:
                L.append("- 원천: " + ", ".join(reads))
            if m["constants"]:
                L.append("- 하드코딩 상수: " + ", ".join(f"`{x}`" for x in m["constants"]))
            if m["manual_overrides"]:
                L.append("- 수기 개입: " + ", ".join(
                    f"`{o['cell']}`={o['value']}" for o in m["manual_overrides"]))
            dep = [meta_title(meta, d) for d in m.get("depends_on", [])]
            use = [meta_title(meta, u) for u in m.get("used_by", [])]
            if dep:
                L.append("- 재료(참조 지표): " + ", ".join(dep))
            if use:
                L.append("- 쓰이는 곳: " + ", ".join(use))
            if m.get("python"):
                L += ["", "```python", m["python"], "```"]
            L.append("")

    if meta["sources"]:
        L += ["## 원천 표", ""]
        seen_tb = {}
        for s in meta["sources"]:
            name = s["title"] or s["range"]
            L += [f"### {name}  `{s['range']}`  ({s['row_count']}행)", "",
                  "| 열 | 이름 |", "|---|---|"]
            for col, nm in s["columns"].items():
                L.append(f"| `{col}` | {nm} |")
            if s["subtotal_rows"]:
                L += ["", f"> 소계/합계 행 {s['subtotal_rows']} — 지표 추출에서 제외됨."]
            tbs = _tables([s])
            if tbs:
                tb = tbs[0]
                if not tb["all_blank"]:
                    if tb["sig"] in seen_tb:
                        tb["dup_of"] = seen_tb[tb["sig"]]
                    else:
                        seen_tb[tb["sig"]] = f"{tb['sheet']}!{tb['range']}"
                L += ["", "**실제 데이터**", "", data_table_md(tb)]
            L.append("")

    if meta.get("extra_cells"):
        L += ["## 표 밖 내용 (표로 안 잡힌 셀)", ""]
        for sheet, cells in meta["extra_cells"].items():
            L += [f"### {sheet}", ""]
            L += [f"- `{c['ref']}`: {_fmt_val(c['value'])}" for c in cells]
            L.append("")

    if low:
        L += ["## 제외 (이름 미확정·반복 부족)", ""]
        L += [f"- `{m['anchor_cell']}` — {m['formula'][:60]}" for m in low[:20]]
        L.append("")
    if meta["unsupported"]:
        L += ["## 미지원", ""] + [f"- `{u['cell']}` — {u['reason']}"
                                  for u in meta["unsupported"][:20]]
    return "\n".join(L).rstrip() + "\n"


def meta_title(meta, mid):
    m = next((x for x in meta["metrics"] if x["id"] == mid), None)
    return (m["title"] or m["id"]) if m else mid
