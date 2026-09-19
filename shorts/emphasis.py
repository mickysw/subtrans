"""쇼츠 제목에서 강조할 어절을 검증한다. 추가 AI 호출은 하지 않는다."""

from __future__ import annotations


def valid_emphasis(title: str, raw: object) -> list[str]:
    """제목에 실제로 있는 완전한 어절만 최대 두 개 사용한다."""
    if not isinstance(raw, list):
        return []
    words = set(title.split())
    result: list[str] = []
    for item in raw:
        if isinstance(item, str) and item in words and item not in result:
            result.append(item)
        if len(result) == 2:
            break
    return result
