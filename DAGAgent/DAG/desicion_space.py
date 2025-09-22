import os
import json
import random
import pickle
import logging

import numpy as np
from datetime import datetime
from abc import ABC
from typing import List
from collections import defaultdict

from DAGAgent.config import Config


logging.basicConfig(
    # filename=Config.LOG_PATH,
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s %(message)s'
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


class DesicionSpace(ABC):
    """
    DecisionSpace is a class that represents the decision space of the DAGAgent.
    """
    def __init__(self, agents: List[str], config: Config) -> None:
        super().__init__()
        self.agents = agents
        self.config = config
        self.learning_rate = config.LEARNING_RATE
        self.discount_factor = config.DISCOUNT_FACTOR
        self.epsilon = config.EPSILON
        self.entropy_weight = config.ENTROPY_WEIGHT
        self.q_table_path = config.Q_TABLE_PATH
        self.q_table = defaultdict(dict)

        for state in agents:
            self.q_table[state] = {
                action: 0 for action in agents if action != state
            }

        if os.path.exists(self.q_table_path):
            self.load_q_table(self.q_table_path)

    def get_available_agents(self, state: str) -> List[str]:
        """
        Get the available agents for the state.
        """
        if state in self.q_table and self.q_table[state]:
            return [agent for agent, value in self.q_table[state].items() if value > self.config.MIN_ACTION_REWARD]
        return self.agents

    def get_next_avail_agents(self, state: str, available_agents: List[str]) -> List[str]:
        """
        Get the next available agents for the state.
        """
        # Sorted by Q value
        sorted_by_q = sorted(self.q_table[state].items(), key=lambda x: x[1], reverse=True)

        q_groups = defaultdict(list)
        for node, q in sorted_by_q:
            q_groups[q].append(node)

        top_two_q_values = list(q_groups.keys())[:2]
        best_nodes = []
        for q in top_two_q_values:
            best_nodes.extend(q_groups[q])

        random_node = []
        if random.random() < self.epsilon:
            random_node.append(random.choice(available_nodes - best_nodes))

        return random_node + (best_nodes if best_nodes else [])

    def save_q_table(self, path: str = "q_table.pkl"):
        """
        Save the Q-table to the path.
        """
        q_table_regular = {
            state: dict(actions) for state, actions in self.q_table.items()
        }

        with open(path, "wb") as f:
            pickle.dump(q_table_regular, f)

        jsonl_path = path.replace(".pkl", "_history.jsonl")
        with open(jsonl_path, "a") as f:
            record = {
                "timestamp": datetime.now().isoformat(),
                "q_table": q_table_regular
            }
            f.write(json.dumps(record) + "\n")

        logger.info(f"Q-table saved at {path}, exported to {jsonl_path}")

    def load_q_table(self, path: str = "q_table.pkl"):
        """
        Load the Q-table from the path.
        """
        path = path or self.q_table_path
        with open(path, "rb") as f:
            loaded = pickle.load(f)
            # Exchange format
            self.q_table = defaultdict(lambda: defaultdict(float), {
                k: defaultdict(float, v) for k, v in loaded.items()
            })
        logger.info(f"Q-table is loaded from {path}")

    def update_from_experience(self, experiences: List[Dict[str, List[str]]]) -> None:
        """
        Update the Q-table from the experiences.
        """
        for experience in experiences:
            state = experience["state"]
            action = experience["action"]
            reward = experience["reward"]

            if action in self.q_table[state]:
                self.q_table[state][action] += self.learning_rate * (reward + self.discount_factor * max(self.q_table[action].values()) - self.q_table[state][action])
