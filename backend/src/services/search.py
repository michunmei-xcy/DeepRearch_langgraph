"""Search service: hybrid search (Tavily + DuckDuckGo) wrapped as LangChain @tool."""

from __future__ import annotations

import logging
import os
from typing import Any

from langchain_community.tools import DuckDuckGoSearchResults
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_core.tools import tool

from utils import deduplicate_and_format_sources, format_sources

logger = logging.getLogger(__name__)

MAX_RESULTS_PER_BACKEND = 5
MAX_TOKENS_PER_SOURCE = int(os.getenv("MAX_TOKENS_PER_SOURCE", "2000"))
MAX_TOTAL_CONTEXT_TOKENS = int(os.getenv("MAX_TOTAL_CONTEXT_TOKENS", "8000"))

_tavily = TavilySearchResults(max_results=MAX_RESULTS_PER_BACKEND)
_ddg = DuckDuckGoSearchResults(max_results=MAX_RESULTS_PER_BACKEND, output_format="list")


def _normalize_tavily(results: list[dict]) -> list[dict[str, Any]]:
    """Normalize Tavily results to {url, title, content}."""
    normalized = []
    for r in results:
        if not isinstance(r, dict):
            continue
        normalized.append({
            "url": r.get("url", ""),
            "title": r.get("title") or r.get("url", ""),
            "content": r.get("content", ""),
        })
    return normalized


def _normalize_ddg(results: list[dict]) -> list[dict[str, Any]]:
    """Normalize DuckDuckGo results to {url, title, content}."""
    normalized = []
    for r in results:
        if not isinstance(r, dict):
            continue
        normalized.append({
            "url": r.get("link", ""),
            "title": r.get("title", ""),
            "content": r.get("snippet", ""),
        })
    return normalized


def _hybrid_search_results(query: str) -> list[dict[str, Any]]:
    """Run Tavily and DuckDuckGo, merge and return normalized result list."""
    results: list[dict[str, Any]] = []

    try:
        tavily_raw = _tavily.invoke(query)
        if isinstance(tavily_raw, list):
            results.extend(_normalize_tavily(tavily_raw))
            logger.info("Tavily returned %d results", len(tavily_raw))
    except Exception as exc:
        logger.warning("Tavily search failed: %s", exc)

    try:
        ddg_raw = _ddg.invoke(query)
        if isinstance(ddg_raw, list):
            results.extend(_normalize_ddg(ddg_raw))
            logger.info("DuckDuckGo returned %d results", len(ddg_raw))
    except Exception as exc:
        logger.warning("DuckDuckGo search failed: %s", exc)

    return results


@tool
def web_search(query: str) -> str:
    """Search the web using hybrid search (Tavily + DuckDuckGo) and return formatted results with sources.
    Use this tool to find up-to-date information about any topic."""
    results = _hybrid_search_results(query)

    if not results:
        return "搜索未返回任何结果。"

    search_response = {"results": results}
    context = deduplicate_and_format_sources(
        search_response,
        max_tokens_per_source=MAX_TOKENS_PER_SOURCE,
        # max_total_context_tokens=MAX_TOTAL_CONTEXT_TOKENS,
    )
    return context


def get_sources_summary(query: str) -> str:
    """Return a bullet-list summary of sources for SSE sources event."""
    results = _hybrid_search_results(query)
    return format_sources({"results": results})
