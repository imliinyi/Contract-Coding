from setuptools import find_namespace_packages, setup

setup(
    name="ContractCoding",
    version="0.1.0",
    description="A contract-first long-running agent runtime",
    packages=find_namespace_packages(include=["ContractCoding*"]),
    install_requires=[
        "numpy",
        "langgraph",
        "pydantic",
        "openai",
        "httpx",
        "duckduckgo_search",
        "sympy",
    ],
    python_requires=">=3.9",
    entry_points={
        "console_scripts": [
            "contract-coding=ContractCoding.app.cli:main",
        ],
    },
)
