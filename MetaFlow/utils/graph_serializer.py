from collections import defaultdict
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
        
        for i, layer in enumerate(all_layers):
            mermaid_string += f"    subgraph Layer {i}\n"
            for agent_name in layer.keys():
                mermaid_string += f"        {agent_name}_{i}[{agent_name}]\n"
            mermaid_string += "    end\n"

        # if not all_layers:
        #     return mermaid_string
            
        # layers_sets = [set(layer.keys()) for layer in all_layers]

        # for i, current_layer_set in enumerate(layers_sets):
        #     # Stop if there is no next layer to connect to.
        #     if i + 1 >= len(layers_sets):
        #         continue
            
        #     next_layer_set = layers_sets[i+1]
            
        #     for source_agent in current_layer_set:
        #         source_id = f"{source_agent}_{i}"
                
        #         # Find edges in the trace that originate from this specific agent instance.
        #         for u, v, reward in execution_trace:
        #             if u == source_agent and v in next_layer_set:
        #                 target_id = f"{v}_{i+1}"
        #                 mermaid_string += f"    {source_id} -->|{reward:.2f}| {target_id}\n"
        
        return mermaid_string

    def serialize_mermaid(self, all_layers: List[Dict[str, GeneralState]], execution_trace: List[Tuple[str, str, float]]) -> str:
        """
        Serializes the executed graph into Mermaid.js flowchart syntax for LLM consumption.
        """
        mermaid_string = "graph TD\n"
        
        node_ids = {}
        for i, layer in enumerate(all_layers):
            for agent_name in layer.keys():
                if (agent_name, i) not in node_ids:
                    node_ids[(agent_name, i)] = f"{agent_name}_{i}"

        for i, layer in enumerate(all_layers):
            mermaid_string += f"    subgraph Layer {i}\n"
            for agent_name in layer.keys():
                node_id = node_ids.get((agent_name, i))
                if node_id:
                    mermaid_string += f"        {node_id}[{agent_name}]\n"
            mermaid_string += "    end\n"

        # processed_edges = set()
        # for i, layer in enumerate(all_layers):
        #     for agent_name in layer.keys():
        #         source_node_id = node_ids.get((agent_name, i))
                
        #         for u, v, reward in execution_trace:
        #             if u == agent_name:
        #                 for j, next_layer in enumerate(all_layers):
        #                     if v in next_layer and j > i:
        #                         target_node_id = node_ids.get((v, j))
        #                         if source_node_id and target_node_id:
        #                             edge = (source_node_id, target_node_id)
        #                             if edge not in processed_edges:
        #                                 mermaid_string += f"    {source_node_id} -->|{reward:.2f}| {target_node_id}\n"
        #                                 processed_edges.add(edge)
        #                         break
        
        return mermaid_string