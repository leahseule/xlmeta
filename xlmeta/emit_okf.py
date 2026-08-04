"""
xlmeta.emit_okf — 추출 결과를 Google OKF v0.1 번들로 쓴다.
번들 = YAML 프론트매터가 붙은 마크다운 디렉터리.
"""

import json
import os
import re
from datetime import datetime, timezone


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
        fm = frontmatter({
            "type": "Spreadsheet Table",
            "title": name,
            "description": f"{src}의 {s['range']} 영역 ({s['row_count']}행)",
            "resource": f"file://{src}#{s['range']}",
            "timestamp": now,
            "layout_confidence": s["layout_confidence"],
            "key_columns": s["key_columns"],
        })
        body = ["", "# Schema", "", "| 열 | 이름 |", "|---|---|"]
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
            "status": m.get("status", "pending"),
            "fingerprint": m.get("fingerprint", ""),
            "manual_override_count": len(m["manual_overrides"]),
            "derivation": "deterministic-formula-parse",
        })
        b = ["", f"`{m['applies_to']}` · 원본 수식 `{m['formula']}`", ""]

        appr = m.get("approval")
        if m.get("status") == "approved" and appr:
            b += [f"> ✅ **승인됨** — {appr.get('approved_by')} · {appr.get('approved_at')}  ",
                  f"> 지문 `{m.get('fingerprint')}` — 정의가 바뀌면 자동 만료됩니다.", ""]
        else:
            b += ["> ⏳ **대기(pending)** — 담당자 승인 전. **AI가 이 정의를 신뢰해선 안 됩니다.**  ",
                  f"> 지문 `{m.get('fingerprint')}` — `--approve`로 승인하세요.", ""]

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

    def _mark(i):
        return "✅" if by_id.get(i, {}).get("status") == "approved" else "⏳"

    with open(os.path.join(outdir, "metrics", "index.md"), "w", encoding="utf-8") as f:
        f.write("# 지표\n\n승인 ✅ · 대기 ⏳\n\n" + "\n".join(
            f"* {_mark(i)} [{t}]({fn}.md) — {by_id[i]['name']}"
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
            "# 승인 상태", "",
            f"* 승인됨 {sum(1 for i in id2file if by_id.get(i, {}).get('status') == 'approved')}건 "
            "— 담당자가 확인함. AI가 써도 됨.",
            f"* 대기 {sum(1 for i in id2file if by_id.get(i, {}).get('status') != 'approved')}건 "
            "— 승인 전. **AI가 신뢰하면 안 됨.**", "",
            "정의(내용)가 바뀌면 지문이 달라져 승인이 자동 만료됩니다. 기본값은 대기(pending).", "",
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

    # 셀 원장은 개념이 아니라 원시 데이터 → OKF 밖에 둔다
    with open(os.path.join(outdir, "cell_graph.json"), "w", encoding="utf-8") as f:
        json.dump(meta["cell_graph"], f, ensure_ascii=False, indent=2, default=str)

    return {"metrics": len(id2file), "sources": len(source_files),
            "excluded": len(low), "cells": len(meta["cell_graph"])}
