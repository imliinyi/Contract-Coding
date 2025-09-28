"""
Configuration file for the DAGAgent.
"""
from pydantic import BaseModel


class Config(BaseModel):
    # DAGAgent
    MAX_RETRY = 3
    MIN_REWARD = -50
    SUCCESS_REWARD = 100
    PATH_PENALTY = 10
    BASE_SALARY_MULTIPLIER = 2
    SUCCESS_RATE_MULTIPLIER = 5
    MIN_PATH_LENGTH = 5
    NODE_PENALTY = 10
    REPEATED_PENALTY = 10
    ALL_EXPLORE = 50
    TERMINATION_POLICY = "Any"

    # Q-Learning
    LEARNING_RATE = 0.1
    DISCOUNT_FACTOR = 0.9
    EPSILON = 0.3
    ENTROPY_WEIGHT = 0.01
    DECAY_RATE = 0.995
    MIN_EPSILON = 0.1
    MIN_ACTION_REWARD = -50
    
    AGENT_SALARIES = {
        "PlanAgent": 5,
        "AnalystAgent": 5,
        "ProgrammingAgent": 5,
        "InspectorAgent": 5,
        "CodeAuditorAgent": 5,
        "TestEngineerAgent": 5
    }

    # System
    Q_TABLE_PATH = "../humaneval_q_table_1.pkl"
    DEFAULT_TRAIN_NUM = 1000
    LOG_PATH = '../agent.log'