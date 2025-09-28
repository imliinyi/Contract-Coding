from typing import List, Dict, Set


def check_layer_revisit(executed_agents: List[frozenset]) -> bool:
    """
    Check if the current layer of agents has been visited before.
    """
    seen_agents: Set[str] = set()
    for layer_set in executed_agents:
        if not layer_set.isdisjoint(seen_agents):
            return True
        seen_agents.update(layer_set)
    return False

def check_long_path(executed_agents: List[frozenset], threshold: int = 7) -> bool:
    """
    Check if the path of agents is too long.
    """
    return len(executed_agents) > threshold
