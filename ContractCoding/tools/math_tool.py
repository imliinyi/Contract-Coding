import sympy

def solve_math_expression(expression: str, mode: str = 'symbolic') -> str:
    """
    Solves a mathematical expression using a symbolic computation library (SymPy).

    :param expression: The mathematical expression to solve.
    :param mode: The calculation mode. Currently only 'symbolic' is supported.
    :return: The result of the calculation as a string, or an error message.
    """
    if mode != 'symbolic':
        return f"Error: Mode '{mode}' is not supported. Only 'symbolic' mode is available."

    try:
        # Use sympify to safely parse the expression
        parsed_expression = sympy.sympify(expression)
        
        # Evaluate the expression
        result = parsed_expression.doit()
        
        return str(result)
    except (sympy.SympifyError, SyntaxError) as e:
        return f"Error: Invalid mathematical expression. Details: {e}"
    except Exception as e:
        return f"An error occurred during calculation: {str(e)}"

solve_math_expression.openai_schema = {
    "type": "function",
    "function": {
        "name": "solve_math_expression",
        "description": "Solves a mathematical expression using a symbolic computation library (SymPy).",
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "The mathematical expression to solve. e.g., 'sqrt(9)', 'solve(x**2 - 4, x)'"
                },
                "mode": {
                    "type": "string",
                    "description": "The calculation mode. Defaults to 'symbolic'.",
                    "enum": ["symbolic"]
                }
            },
            "required": ["expression"]
        }
    }
}