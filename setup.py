from setuptools import setup, find_packages

setup(
    name="MetaFlow",
    version="0.1.0",
    description="A contract-driven multi-agent collaboration framework",
    packages=find_packages(),
    install_requires=[
        "numpy",
        "langgraph",
        "pydantic",
        "openai",
        "httpx",
    ],
    python_requires=">=3.9",
)
