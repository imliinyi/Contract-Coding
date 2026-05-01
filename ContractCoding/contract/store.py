"""Read and write canonical ContractCoding contract files."""

from __future__ import annotations

import os
import re
from typing import Optional

from ContractCoding.contract.spec import ContractSpec, load_contract_json


class ContractFileStore:
    def __init__(self, workspace_dir: str):
        self.workspace_dir = os.path.abspath(workspace_dir)
        self.state_dir = os.path.join(self.workspace_dir, ".contractcoding")
        self.json_path = os.path.join(self.state_dir, "contract.json")
        self.markdown_path = os.path.join(self.state_dir, "contract.md")
        self.prd_path = os.path.join(self.state_dir, "prd.md")

    def write(self, contract: ContractSpec) -> None:
        os.makedirs(self.state_dir, exist_ok=True)
        with open(self.json_path, "w", encoding="utf-8") as handle:
            handle.write(contract.to_json())
        self.write_markdown_projection(contract)
        self.write_prd_projection(contract)

    def write_markdown_projection(self, contract: ContractSpec) -> None:
        os.makedirs(self.state_dir, exist_ok=True)
        with open(self.markdown_path, "w", encoding="utf-8") as handle:
            handle.write(contract.render_markdown())

    def write_prd_projection(self, contract: ContractSpec) -> None:
        os.makedirs(self.state_dir, exist_ok=True)
        with open(self.prd_path, "w", encoding="utf-8") as handle:
            handle.write(contract.render_prd_markdown())

    def read(self, path: Optional[str] = None) -> ContractSpec:
        contract_path = os.path.abspath(path or self.json_path)
        with open(contract_path, "r", encoding="utf-8") as handle:
            contract = load_contract_json(handle.read())
        contract.validate()
        return contract

    def exists(self) -> bool:
        return os.path.exists(self.json_path)

    def projection_hash(self) -> str:
        if not os.path.exists(self.markdown_path):
            return ""
        with open(self.markdown_path, "r", encoding="utf-8") as handle:
            match = re.search(r"Contract hash:\s*`([^`]+)`", handle.read())
        return match.group(1) if match else ""

    def projection_in_sync(self, contract: Optional[ContractSpec] = None) -> bool:
        if contract is None:
            contract = self.read()
        return self.projection_hash() == contract.content_hash()

    def ensure_markdown_projection(self, contract: Optional[ContractSpec] = None) -> ContractSpec:
        contract = contract or self.read()
        if not self.projection_in_sync(contract):
            self.write_markdown_projection(contract)
        return contract
