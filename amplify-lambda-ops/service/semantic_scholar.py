"""
Semantic Scholar research-paper search op.

Public API at api.semanticscholar.org/graph/v1 — no auth required for basic
queries (rate-limited to ~1 req/sec from a given IP). The Lambda forwards
the user's query, formats the response for chat consumption, and returns
the top N papers with truncated abstracts to keep token usage manageable.

If HFU later hits the rate limit under real load, swap in a paid API key
stored in `prod-amplify-app-secrets` under the `SEMANTIC_SCHOLAR_API_KEY`
key — see the `_request_headers()` helper below.
"""

import os
import json
import logging

import requests

from common.ops import op
from common.validate import validated

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Semantic Scholar endpoint + the fields we ask for. Keep this list narrow —
# every additional field pulls more bytes per result and inflates the
# response we feed back to the LLM. These six cover the citation-level info
# users typically want surfaced inline.
SS_BASE_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
SS_FIELDS = "title,authors,year,abstract,citationCount,openAccessPdf,externalIds"

# Caps. The schema also enforces these, but defending here means a malformed
# direct call (skipping validation) still can't blow the budget.
MAX_LIMIT = 10
ABSTRACT_MAX_CHARS = 500
REQUEST_TIMEOUT_SECONDS = 12


def _request_headers() -> dict:
    """Build outbound headers; include API key if HFU has provisioned one."""
    headers = {"Accept": "application/json"}
    api_key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip()
    if api_key:
        headers["x-api-key"] = api_key
    return headers


def _truncate_abstract(text):
    """Trim abstracts so token cost stays reasonable inside the chat context."""
    if not text:
        return ""
    if len(text) <= ABSTRACT_MAX_CHARS:
        return text
    return text[:ABSTRACT_MAX_CHARS].rstrip() + "…"


def _format_paper(p: dict) -> dict:
    """Pick the fields we care about out of Semantic Scholar's response shape."""
    authors = [a.get("name", "") for a in (p.get("authors") or [])]
    open_pdf = (p.get("openAccessPdf") or {}).get("url")
    external = p.get("externalIds") or {}
    return {
        "title": p.get("title") or "(untitled)",
        "authors": authors,
        "year": p.get("year"),
        "abstract": _truncate_abstract(p.get("abstract")),
        "citationCount": p.get("citationCount", 0),
        "doi": external.get("DOI"),
        "pdfUrl": open_pdf,
        "semanticScholarId": p.get("paperId"),
    }


@op(
    path="/op/semantic_scholar_search",
    tags=["research"],
    name="searchScholarlyPapers",
    description=(
        "Search peer-reviewed academic papers via Semantic Scholar. Use when the "
        "user asks to find papers, scholarly literature, research, or citations "
        "on a topic. Returns up to 10 results with titles, authors, year, brief "
        "abstract, citation counts, DOI, and open-access PDF links when available."
    ),
    params={
        "query": "Free-text search query (research topic, question, or keywords). Required.",
        "limit": f"Number of papers to return (1-{MAX_LIMIT}, default 5).",
    },
    method="POST",
)
@validated(op="search")
def search(event, context, current_user, name, data):
    """
    Lambda handler. `data` is the validated JSON body. Returns:
      { success: bool, data: { query, count, results: [...] } }
    """
    query = (data.get("query") or "").strip()
    limit = int(data.get("limit") or 5)
    limit = max(1, min(MAX_LIMIT, limit))

    if not query:
        return {"success": False, "message": "query is required"}

    logger.info("Semantic Scholar search user=%s query=%r limit=%d",
                current_user, query, limit)

    try:
        resp = requests.get(
            SS_BASE_URL,
            params={"query": query, "limit": limit, "fields": SS_FIELDS},
            headers=_request_headers(),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.exception("Semantic Scholar request failed")
        return {
            "success": False,
            "message": f"Could not reach Semantic Scholar: {exc}",
        }

    if resp.status_code == 429:
        # Rate-limited. Surface a clear-and-actionable message; the chat agent
        # can choose to retry with backoff or tell the user to try again.
        return {
            "success": False,
            "message": (
                "Semantic Scholar rate-limited us. Try again in ~30 seconds, "
                "or contact the Help Desk if this keeps happening."
            ),
        }

    if not resp.ok:
        logger.error("Semantic Scholar non-OK: status=%d body=%s",
                     resp.status_code, resp.text[:500])
        return {
            "success": False,
            "message": f"Semantic Scholar returned status {resp.status_code}.",
        }

    payload = resp.json() or {}
    raw_papers = payload.get("data") or []
    results = [_format_paper(p) for p in raw_papers[:limit]]

    return {
        "success": True,
        "data": {
            "query": query,
            "count": len(results),
            "results": results,
        },
    }
