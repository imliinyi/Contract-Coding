from setuptools import find_namespace_packages, setup

setup(
    name="ContractCoding",
    version="2.0.0",
    description="Registry-based long-running multi-agent runtime",
    packages=find_namespace_packages(include=["ContractCoding*"]),
    package_data={
        "ContractCoding": [
            "knowledge/skills/*/SKILL.md",
            "knowledge/skills/*/references/*.md",
            "knowledge/skills/*/scripts/*.py",
        ],
    },
    install_requires=[
        "pydantic>=2.0",
        "openai>=1.0",
    ],
    extras_require={
        "test": ["pytest"],
    },
    python_requires=">=3.9",
    entry_points={
        "console_scripts": [
            "contract-coding=ContractCoding.app.cli:main",
        ],
    },
)
