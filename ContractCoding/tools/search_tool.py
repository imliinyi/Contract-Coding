import json
import html
import re
import urllib.parse
import urllib.request

try:
    from duckduckgo_search import DDGS
except Exception:
    DDGS = None

from ContractCoding.utils.log import get_logger


logger = get_logger()

def search_web(query: str) -> str:
    """
    Searches the web using DuckDuckGo and returns a list of results as a JSON string.

    :param query: The search query.
    :return: A JSON string representing a list of dictionaries, where each dictionary contains the 'title', 'link', and 'snippet' of a search result.
    """
    try:
        if DDGS is None:
            return _search_duckduckgo_html(query)
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
            logger.info(f"Search results for query '{query}': {results}")
        
        if not results:
            return json.dumps({"message": "No results found for the query."})

        formatted_results = []
        for result in results:
            formatted_results.append({
                "title": result.get("title"),
                "link": result.get("href"),
                "snippet": result.get("body")
            })
        return json.dumps(formatted_results, indent=2)

    except Exception as e:
        return json.dumps({"error": f"An error occurred during the web search: {str(e)}"})


def _search_duckduckgo_html(query: str) -> str:
    """Dependency-free fallback for environments without duckduckgo_search."""

    url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ContractCoding/1.0 (+https://local)",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return json.dumps({"error": f"web search fallback failed: {exc}"})

    titles = re.findall(r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', body, re.S)
    snippets = re.findall(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>|<div[^>]+class="result__snippet"[^>]*>(.*?)</div>', body, re.S)
    formatted_results = []
    for index, (href, raw_title) in enumerate(titles[:5]):
        snippet_parts = snippets[index] if index < len(snippets) else ("", "")
        raw_snippet = next((part for part in snippet_parts if part), "")
        link = html.unescape(href)
        parsed = urllib.parse.urlparse(link)
        query_values = urllib.parse.parse_qs(parsed.query)
        if "uddg" in query_values:
            link = query_values["uddg"][0]
        formatted_results.append(
            {
                "title": _clean_html(raw_title),
                "link": link,
                "snippet": _clean_html(raw_snippet),
            }
        )
    if not formatted_results:
        return json.dumps({"message": "No results found for the query."})
    return json.dumps(formatted_results, indent=2)


def _clean_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", value or "")
    return html.unescape(re.sub(r"\s+", " ", text)).strip()

search_web.openai_schema = {
    "type": "function",
    "function": {
        "name": "search_web",
        "description": "Searches the web and returns a list of results with titles, links, and snippets.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query."
                }
            },
            "required": ["query"]
        }
    }
}
