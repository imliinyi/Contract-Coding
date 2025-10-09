import re
from typing import Dict, List, Tuple

from MetaFlow.utils.state import GeneralState



class GraphSerializer:
    """
    A utility class to serialize graph execution history into different formats.
    """
    def serialize_graph(self, all_layers: List[Dict[str, GeneralState]], execution_trace: List[Tuple[str, str, float]]) -> str:
        """
        Serializes the executed graph into Mermaid.js flowchart syntax for LLM consumption.
        """
        mermaid_string = "graph TD\n"
        node_to_layer = {agent: i for i, layer in enumerate(all_layers) for agent in layer.keys()}

        # Define subgraphs for each layer
        for i, layer in enumerate(all_layers):
            mermaid_string += f"    subgraph Layer {i}\n"
            for agent_name in layer.keys():
                # Create a unique node ID for each agent instance in a layer
                mermaid_string += f"        {agent_name}_{i}[{agent_name}]\n"
            mermaid_string += "    end\n"

        # Define edges from the execution trace
        for u, v, reward in execution_trace:
            source_layer = node_to_layer.get(u)
            target_layer = node_to_layer.get(v)

            if source_layer is not None and target_layer is not None and target_layer > source_layer:
                source_node_id = f"{u}_{source_layer}"
                target_node_id = f"{v}_{target_layer}"
                mermaid_string += f"    {source_node_id} -->|{reward:.2f}| {target_node_id}\n"
        
        return mermaid_string