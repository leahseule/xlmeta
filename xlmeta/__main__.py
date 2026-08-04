"""
xlmeta CLI 진입점.

    python -m xlmeta report.xlsx -o okf_bundle
    python -m xlmeta report.xlsx --approve 초과율 --by 홍길동   # 정의 승인
    python -m xlmeta report.xlsx -o okf_bundle --approvals xlmeta.approvals.json
"""

import argparse
import sys

from . import __version__, extract, write_bundle
from . import approvals as A


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="xlmeta",
        description="엑셀 수식에서 업무 지표 정의를 추출해 OKF 번들로 출력한다 (LLM 미사용).",
    )
    p.add_argument("xlsx", help="분석할 .xlsx 파일 경로")
    p.add_argument("-o", "--out", default="okf_bundle",
                   help="출력 디렉터리 (기본: okf_bundle)")
    p.add_argument("--approvals", default=A.DEFAULT_PATH,
                   help=f"승인 저장 파일 (기본: {A.DEFAULT_PATH}). 있으면 지표에 승인/대기 상태 표시")
    p.add_argument("--approve", metavar="지표이름",
                   help="이 지표를 승인 처리(지문 저장)하고 종료")
    p.add_argument("--by", default="unknown", help="승인자 (with --approve)")
    p.add_argument("--version", action="version", version=f"xlmeta {__version__}")
    args = p.parse_args(argv)

    store = A.load(args.approvals)
    meta = extract(args.xlsx, store)

    # ── 승인 모드 ────────────────────────────────────────────
    if args.approve:
        target = next((m for m in meta["metrics"]
                       if args.approve in (m.get("title"), m.get("name"))), None)
        if not target:
            print(f"지표를 찾을 수 없습니다: {args.approve}", file=sys.stderr)
            names = ", ".join(sorted({m.get("title") or m["id"] for m in meta["metrics"]}))
            print(f"가능한 지표: {names}", file=sys.stderr)
            return 1
        A.approve(store, target["fingerprint"], args.approve, target["canonical"], args.by)
        A.save(store, args.approvals)
        print(f"승인됨: {args.approve}  [{target['fingerprint']}]  by {args.by}")
        print(f"정의  : {target['canonical']}")
        print(f"저장  : {args.approvals}")
        return 0

    # ── 번들 출력 모드 ──────────────────────────────────────
    stats = write_bundle(meta, args.out)
    used = [m for m in meta["metrics"] if m["confidence"]["level"] != "low"]
    approved = sum(1 for m in used if m.get("status") == "approved")
    pending = sum(1 for m in used if m.get("status") == "pending")

    print(f"입력   : {meta['source_file']}")
    print(f"출력   : {args.out}/")
    print(f"지표   : {stats['metrics']}건  (승인 {approved} · 대기 {pending})")
    print(f"원천 표: {stats['sources']}개")
    print(f"제외   : {stats['excluded']}건 (신뢰도 낮음 · index.md에 명시)")
    print(f"셀 원장: {stats['cells']}개 (cell_graph.json)")
    if meta["unsupported"]:
        print(f"미지원 : {len(meta['unsupported'])}건 (index.md에 명시)")
    if pending:
        print(f"\n※ 대기 {pending}건은 담당자 승인 전이라 AI가 쓰면 안 됩니다.")
        print(f"  승인: python -m xlmeta {args.xlsx} --approve <지표이름> --by <이름>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
