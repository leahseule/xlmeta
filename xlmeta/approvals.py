"""
xlmeta.approvals — '이 정의는 담당자가 확인해줬다'는 승인을 지문에 붙여 보관한다.

승인은 이름·셀주소가 아니라 지문(fingerprint)에 붙는다. 그래서 정의가
정말로 바뀌면(지문이 달라지면) 승인이 자동으로 만료돼 pending으로 강등된다.
기본값이 pending이라, 변화를 놓쳐도 틀린 정의가 AI에게 가지 않는다.
"""

import json
import os
from datetime import datetime, timezone

DEFAULT_PATH = "xlmeta.approvals.json"


def load(path):
    """승인 파일을 읽어 {지문: 승인정보} 로 돌려준다. 없으면 빈 dict."""
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save(store, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)


def approve(store, fp, name, canonical, by):
    """지문 fp를 승인 처리. 같은 지문을 다시 승인하면 갱신."""
    store[fp] = {
        "name": name,
        "canonical": canonical,
        "approved_by": by,
        "approved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return store
