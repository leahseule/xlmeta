"""
xlmeta — 엑셀 수식에서 업무 지표 정의를 추출해 OKF 번들로 출력한다.

추론(LLM)하지 않는다. 수식은 이미 형식언어이므로 파싱한다.
"""

__version__ = "0.1.0"

from .metric import extract
from .emit_okf import write_bundle

__all__ = ["extract", "write_bundle", "__version__"]
