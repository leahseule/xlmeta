"""
xlmeta CLI 진입점.

    python -m xlmeta report.xlsx -o okf_bundle
"""

import argparse
import sys

from . import __version__, extract, write_bundle


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="xlmeta",
        description="엑셀 수식에서 업무 지표 정의를 추출해 OKF 번들로 출력한다 (LLM 미사용).",
    )
    p.add_argument("xlsx", help="분석할 .xlsx 파일 경로")
    p.add_argument("-o", "--out", default="okf_bundle",
                   help="출력 디렉터리 (기본: okf_bundle)")
    p.add_argument("--version", action="version",
                   version=f"xlmeta {__version__}")
    args = p.parse_args(argv)

    meta = extract(args.xlsx)
    stats = write_bundle(meta, args.out)

    print(f"입력   : {meta['source_file']}")
    print(f"출력   : {args.out}/")
    print(f"지표   : {stats['metrics']}건")
    print(f"원천 표: {stats['sources']}개")
    print(f"제외   : {stats['excluded']}건 (신뢰도 낮음 · index.md에 명시)")
    print(f"셀 원장: {stats['cells']}개 (cell_graph.json)")
    if meta["unsupported"]:
        print(f"미지원 : {len(meta['unsupported'])}건 (index.md에 명시)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
