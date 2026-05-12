"""Agent-facing tool catalog for Runtime V5."""

from ContractCoding.tools.code_tool import build_run_code
from ContractCoding.tools.contract_tool import build_contract_tools
from ContractCoding.tools.file_tool import build_file_tools
from ContractCoding.tools.math_tool import solve_math_expression
from ContractCoding.tools.search_tool import search_web


TOOL_CATALOG = {
    "filesystem": [
        "file_tree",
        "read_file",
        "read_lines",
        "search_text",
        "code_outline",
        "inspect_symbol",
        "create_file",
        "replace_file",
        "write_file",
        "update_file_lines",
        "replace_symbol",
        "add_code",
        "report_blocker",
        "submit_result",
    ],
    "contract": [
        "contract_snapshot",
        "inspect_module_api",
        "run_public_flow",
    ],
    "execution": ["run_code"],
    "research": ["search_web"],
    "calculation": ["solve_math_expression"],
}


__all__ = [
    "TOOL_CATALOG",
    "build_contract_tools",
    "build_file_tools",
    "build_run_code",
    "search_web",
    "solve_math_expression",
]

