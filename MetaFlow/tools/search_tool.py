import json
from typing import List, Dict
from duckduckgo_search import DDGS

def search_web(query: str) -> str:
    """
    Searches the web using DuckDuckGo and returns a list of results as a JSON string.

    :param query: The search query.
    :return: A JSON string representing a list of dictionaries, where each dictionary contains the 'title', 'link', and 'snippet' of a search result.
    """
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
            print(f"Search results for query '{query}': {results}")
        
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