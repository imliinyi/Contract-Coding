from typing import List, Dict, Set


def check_layer_revisit(executed_agents: List[Dict[str, any]]) -> bool:
    """
    Check if the current layer of agents has been visited before.
    """
    seen_agents: Set[str] = set()
    for layer in executed_agents:
        layer_agents = set(layer.keys())
        if not layer_agents.isdisjoint(seen_agents):
            return True
        seen_agents.update(layer_agents)
    return False

def check_long_path(executed_agents: List[frozenset], threshold: int = 7) -> bool:
    """
    Check if the path of agents is too long.
    """
    return len(executed_agents) > threshold
