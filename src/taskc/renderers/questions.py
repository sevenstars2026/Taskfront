from __future__ import annotations


class PassthroughQuestionRenderer:
    async def render(self, *, text: str, field_path: str, sensitive: bool) -> str:
        return text


__all__ = ["PassthroughQuestionRenderer"]

