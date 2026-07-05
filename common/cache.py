"""JSON 파일 캐시 헬퍼 (단일 책임: 안전한 load/dump — file handle leak 방지).

batch/data·features의 산재한 `json.load(open(...))`/`json.dump(.., open(..))`(with 미사용)를 통합.
"""
import json
import os


def load_json(path: str, default=None):
    """path가 있으면 JSON 로드, 없으면 default. (with문으로 핸들 누수 방지)"""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def dump_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)   # 신선 환경(캐시 디렉터리 부재) 대응
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
