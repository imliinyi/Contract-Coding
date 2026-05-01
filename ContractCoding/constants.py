"""Shared ContractCoding constants with optional third-party fallbacks."""

try:
    from langgraph.graph import END as GRAPH_END
except Exception:
    GRAPH_END = "__END__"


END = GRAPH_END
