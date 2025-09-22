"""
Price calculation module for the DAGAgent.
"""

MODEL_PRICE = {
    "gpt-4": {"prompt": 0.00003, "completion": 0.00006},
    "gpt-3.5-turbo": {"prompt": 0.0000015, "completion": 0.000002},
    "gpt-4o": {"prompt": 0.000003, "completion": 0.000006}
}



def calculate_price(tokens: List[int], model: str) -> float:
    """
    Calculate the price of a given number of tokens for a specific model.

    Args:
        tokens (List[int]): The number of prompt tokens and completion tokens.
        model (str): The model name.

    Returns:
        float: The price of the tokens.
    """
    if model not in MODEL_PRICE:
        raise ValueError(f"Model {model} not found in MODEL_PRICE")
    prompt_price = tokens[0] * MODEL_PRICE[model]["prompt"]
    completion_price = tokens[1] * MODEL_PRICE[model]["completion"]
    return prompt_price + completion_price
