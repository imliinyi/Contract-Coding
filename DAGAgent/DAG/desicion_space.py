import os
import json
import random
import pickle
import logging

from langgraph.graph import END
import numpy as np
from datetime import datetime
from abc import ABC
from typing import List, Dict
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
        self.salaries = config.AGENT_SALARIES
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
        available_agents = set(available_agents)
        # Sorted by Q value
        sorted_by_q = sorted(self.q_table[state].items(), key=lambda x: x[1], reverse=True)

        q_groups = defaultdict(list)
        for agent, q in sorted_by_q:
            q_groups[q].append(agent)

        top_two_q_values = list(q_groups.keys())[:2]
        best_agents = []
        for q in top_two_q_values:
            best_agents.extend(q_groups[q])

        random_agent = []
        if random.random() < self.epsilon:
            random_agent.append(random.choice(available_agents - best_agents))

        return random_agent + (best_agents if best_agents else [])

    def calculate_reward(self, current_state: str, action: str, path_len: int, success_rate: float) -> float:
        """
        Calculate the reward for the action.
        """
        if action == END:
            path_penalty = max(0, self.config.MIN_PATH_LENGTH - path_len) * self.config.PATH_PENALTY
            return self.config.SUCCESS_REWARD - path_penalty
        else:
            repeated_penalty = self.config.REPEATED_PENALTY if action == current_state else 0
            executed_penalty = self.salaries[action] * self.config.BASE_SALARY_MULTIPLIER
            success_reward = success_rate * self.config.SUCCESS_REWARD
            return success_reward - executed_penalty - repeated_penalty

    def calculate_group_reward(self, current_state: str, action_group: List[str], 
                    path_len: int, success_rates: List[float], learn_terminating_only: bool = False) -> Dict[str, float]:
        """
        Calculate the reward for the action group.
        """
        rewards = {}
        assert len(action_group) == len(success_rates), "Action group and success rates must have the same length"
        for action, success_rate in zip(action_group, success_rates):
            if learn_terminating_only and action != END:
                rewards[action] = -999
                continue
            rewards[action] = self.calculate_reward(current_state, action, path_len, success_rate)

        return rewards

    def decay_epsilon(self) -> None:
        """
        Decay epsilon.
        """
        self.epsilon *= self.config.EPSILON_DECAY

    def update_from_experience(self, experiences: List[Dict[str, List[str]]]) -> None:
        """
        Update the Q-table from the experiences.
        """
        for experience in experiences:
            state = experience["state"]
            action = experience["action"]
            reward = experience["reward"]

            if reward > -999:
                self.q_table[state][action] += self.learning_rate * (reward + self.discount_factor * 
                            max(self.q_table[action].values()) - self.q_table[state][action])

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
                self.q_table[state][action] += self.learning_rate * (reward + 
                    self.discount_factor * max(self.q_table[action].values()) - self.q_table[state][action])
