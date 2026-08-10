"""A dependency-free LangGraph-style node example.

The function only compiles task input. A host graph decides whether and how to
route the returned result; this module never invokes an agent or tool.
"""

from __future__ import annotations

from typing import Any, TypedDict

from taskc import TaskCompiler


class GraphState(TypedDict, total=False):
    user_request: str
    application_context: dict[str, Any]
    compilation: dict[str, Any]
    next_node: str


def make_compile_node(compiler: TaskCompiler):
    def compile_task(state: GraphState) -> GraphState:
        result = compiler.compile(
            state["user_request"],
            context=state.get("application_context", {}),
        )
        return {
            **state,
            "compilation": result.model_dump(mode="json", exclude_none=True),
            "next_node": "dispatch" if result.status == "ready" else "clarify",
        }

    return compile_task

