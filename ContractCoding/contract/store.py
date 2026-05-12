"""Contract artifact writer for the rewritten runtime."""

from __future__ import annotations

import json
import os

from ContractCoding.contract.spec import ContractSpec


class ContractFileStore:
    def __init__(self, workspace_dir: str):
        self.workspace_dir = os.path.abspath(workspace_dir)
        self.root = os.path.join(self.workspace_dir, ".contractcoding")

    def write(self, contract: ContractSpec) -> str:
        os.makedirs(self.root, exist_ok=True)
        path = os.path.join(self.root, "contract.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(contract.to_json())
        self.write_kernel_artifacts(contract)
        return path

    def write_kernel_artifacts(self, contract: ContractSpec) -> None:
        kernel_dir = os.path.join(self.root, "kernel")
        slices_dir = os.path.join(self.root, "slices")
        teams_dir = os.path.join(self.root, "teams")
        subcontracts_dir = os.path.join(self.root, "team_subcontracts")
        team_states_dir = os.path.join(self.root, "team_states")
        capsules_dir = os.path.join(self.root, "interface_capsules")
        os.makedirs(kernel_dir, exist_ok=True)
        os.makedirs(slices_dir, exist_ok=True)
        os.makedirs(teams_dir, exist_ok=True)
        os.makedirs(subcontracts_dir, exist_ok=True)
        os.makedirs(team_states_dir, exist_ok=True)
        os.makedirs(capsules_dir, exist_ok=True)
        with open(os.path.join(kernel_dir, "product_kernel.json"), "w", encoding="utf-8") as handle:
            json.dump(contract.product_kernel.to_record(), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        with open(os.path.join(kernel_dir, "canonical_substrate.json"), "w", encoding="utf-8") as handle:
            json.dump(contract.canonical_substrate.to_record(), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        for feature_slice in contract.feature_slices:
            with open(os.path.join(slices_dir, f"{feature_slice.id}.json"), "w", encoding="utf-8") as handle:
                json.dump(feature_slice.to_record(), handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
        for feature_team in contract.feature_teams:
            with open(os.path.join(teams_dir, f"{feature_team.id}.json"), "w", encoding="utf-8") as handle:
                json.dump(feature_team.to_record(), handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
        for state in contract.team_states:
            with open(os.path.join(team_states_dir, f"{state.team_id}.json"), "w", encoding="utf-8") as handle:
                json.dump(state.to_record(), handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
        for subcontract in contract.team_subcontracts:
            filename = subcontract.id.replace(":", "_")
            with open(os.path.join(subcontracts_dir, f"{filename}.json"), "w", encoding="utf-8") as handle:
                json.dump(subcontract.to_record(), handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
        for capsule in contract.interface_capsules:
            filename = capsule.id.replace(":", "_")
            with open(os.path.join(capsules_dir, f"{filename}.json"), "w", encoding="utf-8") as handle:
                json.dump(capsule.to_record(), handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")


def load_contract(path: str) -> ContractSpec:
    with open(path, "r", encoding="utf-8") as handle:
        return ContractSpec.from_mapping(json.load(handle))
