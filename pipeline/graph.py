"""
LangGraph graph definition.

Structure:
  START
    ├── resume_parser     ─┐
    ├── github_fetch       ├── (parallel fan-out, fan-in at merge_normalize)
    └── prompt_interpreter ┘
          ↓
    merge_normalize
          ↓
    component_selector
          ↓
    media_mapper
          ↓
    copy_generation  ◄──────────────────────────────┐
          ↓                                          │ (retry loop — max 2)
    assemble_validate                                │
          ↓                                          │
    [retry_router edge] ─── passed ──► END          │
                        └── retry ───────────────────┘
                        └── max_retries ──► fallback ──► END

All nodes read from LangGraphState, return partial dict updates.
No node touches another node's state keys.
"""
from __future__ import annotations

import os
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.redis import AsyncRedisSaver

from pipeline.state import LangGraphState
from pipeline.edges.resume_mode_router import resume_mode_router
from pipeline.edges.retry_router import retry_router

# Node imports — each is an async function: (state) -> dict
from pipeline.nodes.resume_parser import resume_parser_node
from pipeline.nodes.github_fetch import github_fetch_node
from pipeline.nodes.prompt_interpreter import prompt_interpreter_node
from pipeline.nodes.merge_normalize import merge_normalize_node
from pipeline.nodes.component_selector import component_selector_node
from pipeline.nodes.media_mapper import media_mapper_node
from pipeline.nodes.copy_generation import copy_generation_node
from pipeline.nodes.layout_assembler import assemble_validate_node


def build_graph() -> StateGraph:
    """
    Constructs and returns the compiled LangGraph.
    Called once at application startup.
    """
    graph = StateGraph(LangGraphState)

    # ── Register nodes ────────────────────────────────────────────────────
    graph.add_node("resume_parser", resume_parser_node)
    graph.add_node("github_fetch", github_fetch_node)
    graph.add_node("prompt_interpreter", prompt_interpreter_node)
    graph.add_node("merge_normalize", merge_normalize_node)
    graph.add_node("component_selector", component_selector_node)
    graph.add_node("media_mapper", media_mapper_node)
    graph.add_node("copy_generation", copy_generation_node)
    graph.add_node("assemble_validate", assemble_validate_node)

    # ── Fan-out: START → three parallel nodes ─────────────────────────────
    # LangGraph fires all three simultaneously.
    # They write to disjoint state keys — no race condition.
    graph.add_edge(START, "resume_parser")
    graph.add_edge(START, "github_fetch")
    graph.add_edge(START, "prompt_interpreter")

    # ── Fan-in: all three → merge_normalize ───────────────────────────────
    # LangGraph waits for all three before releasing to merge.
    graph.add_edge("resume_parser", "merge_normalize")
    graph.add_edge("github_fetch", "merge_normalize")
    graph.add_edge("prompt_interpreter", "merge_normalize")

    # ── Sequential stage 2 ────────────────────────────────────────────────
    graph.add_edge("merge_normalize", "component_selector")
    graph.add_edge("component_selector", "media_mapper")
    graph.add_edge("media_mapper", "copy_generation")
    graph.add_edge("copy_generation", "assemble_validate")

    # ── Conditional retry edge ─────────────────────────────────────────────
    # retry_router reads state["validation_result"] and state["retry_count"]
    # Returns: "copy_generation" (retry) | "done" (success) | "fallback"
    graph.add_conditional_edges(
        "assemble_validate",
        retry_router,
        {
            "copy_generation": "copy_generation",   # loop back with feedback in state
            "done": END,
            "fallback": END,                        # max retries — fallback layout already set
        }
    )

    return graph


async def get_compiled_graph(redis_url: str | None = None):
    """
    Compiles the graph with Redis checkpointing.
    Checkpointer persists full state after every node —
    enables crash recovery and per-node debugging.

    Call once at startup and reuse the compiled app.
    """
    url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
    checkpointer = AsyncRedisSaver.from_conn_string(url)
    await checkpointer.asetup()

    graph = build_graph()
    app = graph.compile(checkpointer=checkpointer)
    return app