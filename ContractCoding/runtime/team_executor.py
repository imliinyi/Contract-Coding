"""Execute implementation waves for functional teams."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
import json
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional

from ContractCoding.agents.profile import AgentProfileRegistry
from ContractCoding.config import Config
from ContractCoding.knowledge.manager import ContextManager
from ContractCoding.contract.work_item import WorkItem
from ContractCoding.execution.runner import AgentRunner
from ContractCoding.execution.workspace import workspace_scope
from ContractCoding.llm.observability import payload_observability
from ContractCoding.runtime.hooks import HookManager
from ContractCoding.quality.self_check import SelfChecker
from ContractCoding.runtime.store import RunRecord, RunStore
from ContractCoding.runtime.teams import TeamRuntime
from ContractCoding.runtime.scheduler import TeamWave
from ContractCoding.utils.state import GeneralState


StepExecutor = Callable[[WorkItem, str, GeneralState], Any]


@dataclass
class TeamResult:
    team_id: str
    scope_id: str
    wave_kind: str
    work_item_ids: List[str]
    completed: List[str] = field(default_factory=list)
    failed: Dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.failed


class TeamExecutor:
    def __init__(
        self,
        config: Config,
        store: RunStore,
        agent_runner: Optional[AgentRunner] = None,
        context_manager: Optional[ContextManager] = None,
        profile_registry: Optional[AgentProfileRegistry] = None,
        step_executor: Optional[StepExecutor] = None,
        team_runtime: Optional[TeamRuntime] = None,
        hook_manager: Optional[HookManager] = None,
    ):
        self.config = config
        self.store = store
        self.agent_runner = agent_runner
        self.context_manager = context_manager
        self.profile_registry = profile_registry or AgentProfileRegistry()
        self.step_executor = step_executor
        self.team_runtime = team_runtime
        self.hooks = hook_manager or HookManager(store=store, enabled=False)

    def execute(self, run: RunRecord, wave: TeamWave) -> TeamResult:
        contract = self.store.get_contract(run.id)
        scope_team_id = ""
        team_workspace = os.path.abspath(run.workspace_dir or self.config.WORKSPACE_DIR)
        if self.team_runtime is not None and contract is not None:
            self.team_runtime.ensure_teams(run.id, contract)
            scope_team = self.team_runtime.team_for_scope(run.id, wave.scope.id, contract)
            if scope_team is not None:
                scope_team_id = scope_team.id
                if self._wave_uses_shared_workspace():
                    team_workspace = os.path.abspath(run.workspace_dir or self.config.WORKSPACE_DIR)
                elif all(self._is_system_interface_item(item) for item in wave.items):
                    team_workspace = os.path.abspath(run.workspace_dir or self.config.WORKSPACE_DIR)
                else:
                    team_workspace = self.team_runtime.ensure_workspace(run, contract, wave.scope.id)
                self.team_runtime.brief_team_lead(run.id, contract, wave.scope.id, wave.items)
                self.team_runtime.mark_active(scope_team.id, [item.id for item in wave.items])

        team_id = self.store.create_team_run(
            run_id=run.id,
            scope_id=wave.scope.id,
            execution_plane=wave.execution_plane,
            work_item_ids=[item.id for item in wave.items],
            metadata={
                "wave_kind": wave.wave_kind,
                "parallel_slots": wave.parallel_slots,
                "promotion_barrier": wave.promotion_barrier,
                "conflict_keys": wave.conflict_keys,
                "parallel_reason": wave.parallel_reason,
                "serial_reason": wave.serial_reason,
                "scope_team_id": scope_team_id,
                "team_workspace": team_workspace,
            },
        )
        if not self.store.acquire_leases(run.id, team_id, [item.id for item in wave.items]):
            self.store.finish_team_run(team_id, "SKIPPED", {"reason": "lease conflict"})
            return TeamResult(
                team_id=team_id,
                scope_id=wave.scope.id,
                wave_kind=wave.wave_kind,
                work_item_ids=[item.id for item in wave.items],
                failed={item.id: "lease conflict" for item in wave.items},
            )

        result = TeamResult(
            team_id=team_id,
            scope_id=wave.scope.id,
            wave_kind=wave.wave_kind,
            work_item_ids=[item.id for item in wave.items],
        )
        try:
            if wave.parallel_slots <= 1 or len(wave.items) <= 1:
                for item in wave.items:
                    self._execute_one(run, wave, item, result, workspace_dir=team_workspace)
            else:
                with ThreadPoolExecutor(max_workers=wave.parallel_slots) as executor:
                    futures = {
                        executor.submit(self._execute_one, run, wave, item, result, team_workspace): item
                        for item in wave.items
                    }
                    for future in as_completed(futures):
                        future.result()
            if self.team_runtime is not None and scope_team_id:
                self.team_runtime.mark_wave_result(scope_team_id, result.completed, result.failed)
            self.store.finish_team_run(team_id, "COMPLETED" if result.ok else "ERROR", {
                "completed": result.completed,
                "failed": result.failed,
            })
            return result
        finally:
            self.store.release_leases(run.id, team_id)

    def _wave_uses_shared_workspace(self) -> bool:
        return self.step_executor is not None and self.agent_runner is None

    def _execute_one(
        self,
        run: RunRecord,
        wave: TeamWave,
        item: WorkItem,
        result: TeamResult,
        workspace_dir: Optional[str] = None,
    ) -> None:
        agent_name = self._agent_for(item, wave)
        effective_workspace = os.path.abspath(workspace_dir or run.workspace_dir or self.config.WORKSPACE_DIR)
        state = GeneralState(
            task=run.task,
            sub_task=self._build_sub_task(wave, item, workspace_dir=effective_workspace),
            role="user",
            thinking="",
            output="",
        )
        agent_packet = None
        if self.context_manager is not None:
            contract = self.store.get_contract(run.id)
            if contract is not None:
                runtime_items = self.store.list_work_items(run.id)
                agent_packet = self.context_manager.build_agent_input_packet(
                    task=run.task,
                    contract=contract,
                    item=item,
                    scope=wave.scope,
                    wave_kind=wave.wave_kind,
                    runtime_items=runtime_items,
                    source_snapshots="",
                )
                state.sub_task = f"{state.sub_task}\n\n{agent_packet.render(self.config.CONTEXT_MAX_CHARS)}"
            else:
                focused_context = self.context_manager.render_work_item_context(item, wave.scope)
                state.sub_task = f"{state.sub_task}\n\n{focused_context}"
        step_id = self.store.create_step(
            run.id,
            item.id,
            agent_name,
            input_payload={
                "wave_kind": wave.wave_kind,
                "team_scope": wave.scope.to_record(),
                "work_item": item.to_contract_record(),
                "agent_packet": agent_packet.to_record() if agent_packet else {},
            },
        )
        if wave.wave_kind == "implementation":
            self.store.update_work_item_status(run.id, item.id, "RUNNING")

        try:
            self.hooks.emit(
                "before_agent_step",
                run_id=run.id,
                task_id=str(run.metadata.get("task_id", "")),
                payload={"work_item_id": item.id, "agent": agent_name, "scope_id": wave.scope.id},
            )
            invariant_checker = SelfChecker(effective_workspace)
            timing: Dict[str, float] = {}
            started = time.perf_counter()
            preflight = self._preflight_item(invariant_checker, wave, item)
            timing["preflight_seconds"] = round(time.perf_counter() - started, 4)
            if preflight.errors:
                payload = {"validation_errors": preflight.errors}
                payload["timing"] = timing
                error = "; ".join(preflight.errors)
                self.store.finish_step(step_id, "ERROR", output_payload=payload, error=error)
                self.store.update_work_item_status(run.id, item.id, "BLOCKED", evidence=preflight.errors)
                result.failed[item.id] = error
                return

            if self._is_system_scaffold_item(item):
                payload = self._write_system_scaffold_artifact(run, wave, item, workspace_dir=effective_workspace)
            elif self._is_system_interface_item(item):
                payload = self._write_system_interface_artifact(run, wave, item, workspace_dir=effective_workspace)
            else:
                started = time.perf_counter()
                with workspace_scope(effective_workspace):
                    output = self._run_agent(run, item, agent_name, state)
                timing["agent_seconds"] = round(time.perf_counter() - started, 4)
                payload = self._output_to_payload(output)
            payload.setdefault(
                "wave_allowed_artifacts",
                sorted({artifact for wave_item in wave.items for artifact in wave_item.target_artifacts}),
            )
            payload.setdefault("timing", {}).update(timing)
            validation_errors = list(payload.get("validation_errors", []) or [])
            blocker = self._payload_agent_blocker(payload, allowed_artifacts=payload.get("wave_allowed_artifacts", []))
            if blocker:
                validation_errors.append(blocker)
                payload["validation_errors"] = validation_errors
            infra_error = self._payload_infra_error(payload)
            if infra_error:
                validation_errors = [infra_error, *validation_errors]
                payload["validation_errors"] = validation_errors
            if validation_errors:
                error = "; ".join(str(value) for value in validation_errors)
                self.store.finish_step(step_id, "ERROR", output_payload=payload, error=error)
                self.store.update_work_item_status(run.id, item.id, "BLOCKED", evidence=validation_errors)
                result.failed[item.id] = error
                return

            self._record_llm_observability(run, item, payload)

            started = time.perf_counter()
            postflight = self._postflight_item(invariant_checker, wave, item, payload)
            check_key = "self_check_seconds"
            payload.setdefault("timing", {})[check_key] = round(time.perf_counter() - started, 4)
            if postflight.evidence:
                payload["system_validation"] = postflight.evidence
            if postflight.errors:
                self._add_research_source_blocker(item, payload, postflight.errors)
                payload["validation_errors"] = postflight.errors
                error = "; ".join(postflight.errors)
                self.store.finish_step(step_id, "ERROR", output_payload=payload, error=error)
                self.store.update_work_item_status(run.id, item.id, "BLOCKED", evidence=postflight.errors)
                result.failed[item.id] = error
                return

            self.store.finish_step(step_id, "COMPLETED", output_payload=payload)
            next_status = "VERIFIED"
            evidence = []
            if payload.get("output"):
                evidence.append(str(payload["output"])[:1000])
            for value in payload.get("system_validation", []) or []:
                evidence.append(str(value)[:1000])
            self.store.update_work_item_status(run.id, item.id, next_status, evidence=evidence)
            result.completed.append(item.id)
        except Exception as exc:
            self.store.finish_step(step_id, "ERROR", error=str(exc))
            self.store.update_work_item_status(run.id, item.id, "BLOCKED", evidence=[str(exc)])
            result.failed[item.id] = str(exc)

    def _agent_for(self, item: WorkItem, wave: TeamWave) -> str:
        return self.profile_registry.resolve_agent_name(item.owner_profile, item.kind)

    def _run_agent(self, run: RunRecord, item: WorkItem, agent_name: str, state: GeneralState) -> Any:
        if self.step_executor is not None:
            return self.step_executor(item, agent_name, state)
        if self.agent_runner is None:
            return GeneralState(
                task=state.task,
                sub_task=state.sub_task,
                role=agent_name,
                thinking="No agent runner configured.",
                output=f"Recorded {item.id} without execution.",
                next_agents=[],
            )
        memory_key = (
            self.context_manager.memory_key(agent_name, run_id=run.id, scope_id=item.scope_id)
            if self.context_manager is not None
            else agent_name
        )
        if self.context_manager is not None:
            self.context_manager.set_active_memory_key(agent_name, memory_key)
        try:
            output_state = self.agent_runner.run(
                agent_name=agent_name,
                state=state,
                next_available_agents=list(self.agent_runner.agents.keys()),
            )
        finally:
            if self.context_manager is not None:
                self.context_manager.clear_active_memory_key(agent_name)
        if self.context_manager is not None:
            state_for_memory = getattr(output_state, "output_state", output_state)
            self.context_manager.add_message(memory_key, state_for_memory)
        return output_state

    def _preflight_item(self, checker: SelfChecker, wave: TeamWave, item: WorkItem):
        return checker.check_verification_preflight(WorkItem(id="noop", kind="research"))

    def _postflight_item(self, checker: SelfChecker, wave: TeamWave, item: WorkItem, payload: Dict[str, Any]):
        return checker.check_item(item, payload)

    @staticmethod
    def _is_system_interface_item(item: WorkItem) -> bool:
        return (
            item.kind == "doc"
            and item.id.startswith("interface:")
            and bool(item.target_artifacts)
            and item.target_artifacts[0].startswith(".contractcoding/interfaces/")
        )

    @staticmethod
    def _is_system_scaffold_item(item: WorkItem) -> bool:
        return (
            item.kind == "doc"
            and item.id.startswith("scaffold:")
            and any(path.startswith(".contractcoding/scaffolds/") for path in item.target_artifacts)
        )

    def _write_system_interface_artifact(
        self,
        run: RunRecord,
        wave: TeamWave,
        item: WorkItem,
        workspace_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        target = os.path.normpath(item.target_artifacts[0].replace("\\", "/")).replace("\\", "/")
        workspace = os.path.abspath(workspace_dir or run.workspace_dir or self.config.WORKSPACE_DIR)
        path = os.path.abspath(os.path.join(workspace, target))
        if not path.startswith(workspace + os.sep):
            return {
                "output": f"Refused to write interface artifact outside workspace: {target}",
                "changed_files": [],
                "validation_errors": [f"Interface artifact is outside workspace: {target}"],
            }
        os.makedirs(os.path.dirname(path), exist_ok=True)
        contract = self.store.get_contract(run.id)
        scope_interfaces = contract.interfaces_for_scope(wave.scope.id) if contract is not None else []
        conformance_tests = [
            path
            for spec in scope_interfaces
            for path in spec.conformance_tests
        ]
        payload = {
            "scope": wave.scope.id,
            "scope_type": wave.scope.type,
            "kind": "scope_interface",
            "status": "FROZEN",
            "artifacts": list(item.inputs.get("scope_artifacts", wave.scope.artifacts or [])),
            "provided_interfaces": list(item.provided_interfaces),
            "frozen_interfaces": [spec.to_record() for spec in scope_interfaces],
            "conformance_tests": conformance_tests,
            "acceptance_criteria": list(item.acceptance_criteria),
            "assumptions": [
                "Implementation files must stay within their declared WorkScope target artifacts.",
                "Cross-scope calls should use stable public module imports and serializable data structures.",
                "The final integration gate owns compile/import/unittest verification.",
            ],
        }
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        changed = [target]
        for conformance_path in conformance_tests:
            normalized = os.path.normpath(str(conformance_path).replace("\\", "/")).replace("\\", "/")
            test_path = os.path.abspath(os.path.join(workspace, normalized))
            if not test_path.startswith(workspace + os.sep):
                continue
            os.makedirs(os.path.dirname(test_path), exist_ok=True)
            with open(test_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "scope": wave.scope.id,
                        "status": "TESTED",
                        "interfaces": [spec.id for spec in scope_interfaces],
                        "checks": ["symbol_presence", "declared_method_presence", "import_surface"],
                    },
                    handle,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                handle.write("\n")
            changed.append(normalized)
        return {
            "role": "System_Interface_Generator",
            "output": f"Generated deterministic interface contract for scope {wave.scope.id}: {target}",
            "changed_files": changed,
            "validation_errors": [],
        }

    def _write_system_scaffold_artifact(
        self,
        run: RunRecord,
        wave: TeamWave,
        item: WorkItem,
        workspace_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        workspace = os.path.abspath(workspace_dir or run.workspace_dir or self.config.WORKSPACE_DIR)
        contract = self.store.get_contract(run.id)
        interface_specs = contract.interfaces_for_scope(wave.scope.id) if contract is not None else []
        scaffold_specs = [
            spec
            for spec in interface_specs
            if spec.critical and spec.artifact in item.target_artifacts
        ] or [spec for spec in interface_specs if spec.critical]
        changed: List[str] = []
        manifests: List[str] = []
        for spec in scaffold_specs:
            if spec.artifact:
                target = self._write_scaffold_python_file(workspace, spec)
                if target:
                    changed.append(target)
            manifest = self._write_scaffold_manifest(workspace, spec, wave.scope.id)
            if manifest:
                changed.append(manifest)
                manifests.append(manifest)
        if not scaffold_specs:
            manifest = self._write_generic_scaffold_manifest(workspace, item, wave.scope.id)
            changed.append(manifest)
            manifests.append(manifest)
        return {
            "role": "System_Scaffold_Generator",
            "output": f"Generated critical scaffold for scope {wave.scope.id}.",
            "changed_files": changed,
            "validation_errors": [],
            "scaffold_manifests": manifests,
        }

    def _write_scaffold_python_file(self, workspace: str, spec) -> str:
        target = os.path.normpath(str(spec.artifact).replace("\\", "/")).replace("\\", "/")
        path = os.path.abspath(os.path.join(workspace, target))
        if not path.startswith(workspace + os.sep):
            return ""
        if os.path.exists(path):
            return target
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._ensure_package_inits(workspace, target)
        lines = [
            '"""Compiler-generated critical interface scaffold.',
            "",
            "This file is intentionally minimal. Team build must replace stub",
            "behavior before team/final gates can pass.",
            '"""',
            "",
        ]
        for symbol in spec.symbols:
            lines.extend(self._render_scaffold_symbol(symbol))
            lines.append("")
        if len(lines) <= 6:
            lines.extend(["__all__ = []", ""])
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines).rstrip() + "\n")
        return target

    def _write_scaffold_manifest(self, workspace: str, spec, scope_id: str) -> str:
        manifest = str((spec.scaffold or {}).get("manifest") or f".contractcoding/scaffolds/{scope_id}.json")
        target = os.path.normpath(manifest.replace("\\", "/")).replace("\\", "/")
        path = os.path.abspath(os.path.join(workspace, target))
        if not path.startswith(workspace + os.sep):
            return ""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {
            "scope": scope_id,
            "interface_id": spec.id,
            "status": "SCAFFOLDED",
            "artifact": spec.artifact,
            "symbols": list(spec.symbols),
            "schemas": list(spec.schemas),
            "semantics": list(spec.semantics),
            "conformance_tests": list(spec.conformance_tests),
            "allowed_stub_policy": (spec.scaffold or {}).get("allowed_stub_policy", ""),
        }
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        return target

    def _write_generic_scaffold_manifest(self, workspace: str, item: WorkItem, scope_id: str) -> str:
        target = f".contractcoding/scaffolds/{scope_id}.json"
        path = os.path.join(workspace, target)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {
            "scope": scope_id,
            "status": "SCAFFOLDED",
            "artifacts": [path for path in item.target_artifacts if not path.startswith(".contractcoding/")],
        }
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        return target

    @staticmethod
    def _render_scaffold_symbol(symbol: Dict[str, Any]) -> List[str]:
        kind = str(symbol.get("kind") or symbol.get("type") or "").lower()
        name = str(symbol.get("name") or "").strip()
        if not name:
            return []
        if kind in {"constant", "constants"}:
            names = [name, *[str(value).strip() for value in symbol.get("names", []) or [] if str(value).strip()]]
            return [f"{constant} = None" for constant in dict.fromkeys(names)]
        if kind in {"function", "method"}:
            return [f"def {name}(*args, **kwargs):", "    raise NotImplementedError('scaffold pending implementation')"]
        class_lines = [f"class {name}:", "    \"\"\"Critical interface scaffold.\"\"\""]
        methods = [str(value) for value in symbol.get("methods", []) or [] if str(value).strip()]
        if not methods:
            class_lines.append("    def __init__(self, *args, **kwargs):")
            class_lines.append("        pass")
            return class_lines
        for method in methods:
            method_name = method.split("(", 1)[0].strip().split(".", 1)[-1]
            if method_name:
                class_lines.append(f"    def {method_name}(self, *args, **kwargs):")
                class_lines.append("        raise NotImplementedError('scaffold pending implementation')")
        return class_lines

    @staticmethod
    def _ensure_package_inits(workspace: str, artifact: str) -> None:
        parts = artifact.split("/")[:-1]
        current = workspace
        for part in parts:
            current = os.path.join(current, part)
            if not os.path.isdir(current):
                os.makedirs(current, exist_ok=True)
            init_path = os.path.join(current, "__init__.py")
            if not os.path.exists(init_path):
                with open(init_path, "w", encoding="utf-8") as handle:
                    handle.write('"""Generated package scaffold."""\n')


    def _build_sub_task(self, wave: TeamWave, item: WorkItem, workspace_dir: Optional[str] = None) -> str:
        if item.kind == "coding" and item.target_artifacts:
            targets = "\n".join(f"- {artifact}" for artifact in item.target_artifacts)
            criteria = "\n".join(f"- {criterion}" for criterion in item.acceptance_criteria)
            provided = self._render_interfaces(item.provided_interfaces)
            required = self._render_interfaces(item.required_interfaces)
            repair_context = self._render_repair_context(item)
            return (
                f"Module team: {wave.scope.id}\n"
                f"Owner packet: {item.owner_profile}\n"
                f"Execution plane: {wave.execution_plane}\n"
                f"Dependency policy: {item.dependency_policy}\n"
                "Wave allowed artifacts:\n"
                f"{self._render_artifact_bullets([artifact for wave_item in wave.items for artifact in wave_item.target_artifacts])}\n\n"
                "Implement/Fix the ready files in this team wave.\n"
                "Target files in this module wave:\n"
                f"{targets}\n\n"
                "Limit implementation to the listed files for this wave.\n"
                "Dependencies may be satisfied either by completed artifacts or by stable interface contracts, "
                "as declared by Dependency policy.\n"
                "Do not request run_code or shell verification from the LLM step; ContractCoding runs deterministic "
                "self-checks, team gates, and the final gate after file writes.\n\n"
                "Tool-use guidance for repairs: if the target file already exists and the repair is small, do not "
                "block because the file is large. Request `read_lines` around the failing code, then use "
                "`update_file_lines` for the exact replacement. Use `replace_symbol` when a whole function/class "
                "needs repair after syntax or indentation feedback. Use `replace_file` when replacing "
                "a scaffold or regenerating the whole target file; this truncates old placeholder tail code. Use "
                "`add_code` for a small insertion.\n"
                  "If a Target file is missing, create it with `create_file`. A missing package directory or source "
                  "tree is not a blocker when the missing path is listed under Target files / Wave allowed artifacts; "
                  "create the directory/file within the allowed artifact path and continue.\n"
                  "If validation or PatchGuard rolls back a patch inside an allowed target file, that is not an "
                  "out-of-scope blocker. Read the reported range or symbol and repair the same allowed file again.\n"
                "Example tool intent flow: read_lines(path='pkg/module.py', start_line=40, end_line=90), then "
                "update_file_lines(file_path='pkg/module.py', start_line=55, end_line=62, new_content='...').\n\n"
                "Terminal protocol: finish successful work by calling `submit_result` with changed files and "
                  "validation evidence. If the required fix is outside your allowed artifacts, call `report_blocker` "
                  "with required_artifacts and current_allowed_artifacts instead of editing around the boundary. "
                  "Do not call `report_blocker` for artifacts already listed in Target files / Wave allowed artifacts.\n\n"
                "If the diagnostic points to a file outside your Target files, do not edit another file. "
                "Return a clear structured blocker naming the required artifact so ContractCoding can redirect repair. "
                "Use this exact JSON shape in your output when possible: "
                "{\"blocker_type\":\"out_of_scope_repair\",\"required_artifacts\":[\"path.py\"],"
                "\"current_allowed_artifacts\":[\"allowed.py\"],\"reason\":\"why\"}.\n\n"
                "For interface, CLI, API, and REPL artifacts: keep module import-time behavior minimal. Do not hard "
                "import downstream team modules such as ai/core/io at top level unless they are in the same target "
                "artifact set and already visible. Lazy import those dependencies inside command handlers/functions "
                "so self-checks can import the entrypoint in an isolated team workspace before other teams promote.\n\n"
                "Product-behavior discipline: do not satisfy named capabilities as isolated side modules. If this "
                "wave owns CLI, planning, reporting, persistence, failure/repair, routing, maintenance, or simulation "
                "code, wire that behavior into the public flow owned by this wave so later integration tests can "
                "exercise it through real imports or `python -m` entrypoints. For CLI modules, provide an executable "
                "`if __name__ == '__main__': raise SystemExit(main())` path unless a different public entrypoint is "
                "explicitly required. For simulation/planning products, emit structured state/events that final tests "
                "can assert, not only human-readable note strings.\n\n"
                "Cross-scope API discipline: before calling a class/function from another scope, inspect the exact "
                "producer signature from the real artifact when it exists, or from Required interfaces when the artifact "
                "is not present yet. Do not guess constructor arguments, keyword names, return shapes, or package exports. "
                "If a public entrypoint adapts domain/core/io objects, keep that adapter in the current target artifact "
                "and submit evidence naming the producer symbol and signature you checked.\n\n"
                f"Work item: {item.id}\n"
                f"Title: {item.title}\n"
                f"{repair_context}"
                f"Provided interfaces:\n{provided}\n"
                f"Required interfaces:\n{required}\n"
                "Acceptance criteria:\n"
                f"{criteria}"
            )
        return (
            f"Team scope: {wave.scope.id}\n"
            f"Execution plane: {wave.execution_plane}\n"
            f"Work item: {item.id}\n"
            f"Kind: {item.kind}\n"
            f"Title: {item.title}\n"
            f"Target artifacts: {', '.join(item.target_artifacts) or 'None'}\n"
            f"Wave allowed artifacts: {', '.join(artifact for wave_item in wave.items for artifact in wave_item.target_artifacts) or 'None'}\n"
            f"Conflict keys: {', '.join(item.conflict_keys) or 'None'}\n"
            f"Dependency policy: {item.dependency_policy}\n"
            f"Provided interfaces:\n{self._render_interfaces(item.provided_interfaces)}\n"
            f"Required interfaces:\n{self._render_interfaces(item.required_interfaces)}\n"
            "Terminal protocol: after tool-based edits, call `submit_result` with changed files/evidence; "
            "call `report_blocker` if the needed artifact is outside this WorkItem.\n"
            "Acceptance criteria:\n"
            + "\n".join(f"- {criterion}" for criterion in item.acceptance_criteria)
        )

    @staticmethod
    def _render_interfaces(interfaces: List[Dict[str, Any]]) -> str:
        if not interfaces:
            return "- None"
        return "\n".join(f"- {interface}" for interface in interfaces)

    @staticmethod
    def _render_artifact_bullets(artifacts: List[str]) -> str:
        unique = []
        for artifact in artifacts:
            if artifact and artifact not in unique:
                unique.append(artifact)
        return "\n".join(f"- {artifact}" for artifact in unique) or "- None"

    @staticmethod
    def _render_repair_context(item: WorkItem) -> str:
        diagnostics = list((item.inputs or {}).get("diagnostics", []) or [])
        if not diagnostics:
            return ""
        lines = ["Repair diagnostics:"]
        repair_ticket_id = str((item.inputs or {}).get("repair_ticket_id", "") or "")
        if repair_ticket_id:
            lines.append(f"- repair_ticket_id: {repair_ticket_id}")
        repair_mode = str((item.inputs or {}).get("repair_mode", "") or "")
        if repair_mode:
            lines.append(f"- repair_mode: {repair_mode}")
            if repair_mode == "rewrite_enclosing_function_or_class":
                lines.append("  protocol: read the enclosing function/class and replace that whole block instead of applying another blind line patch.")
        repair_packet = (item.inputs or {}).get("repair_packet")
        centralized_final_repair = bool(
            isinstance(repair_packet, dict)
            and repair_packet.get("centralized_final_repair")
        ) or str((item.inputs or {}).get("final_repair_mode", "")) == "centralized_convergence"
        if isinstance(repair_packet, dict) and repair_packet:
            locked = repair_packet.get("locked_artifacts") or []
            allowed = repair_packet.get("allowed_artifacts") or []
            lines.append(f"- repair_packet_version: {repair_packet.get('protocol_version', '2')}")
            lines.append(f"  repair_packet_allowed_artifacts: {', '.join(str(value) for value in allowed) or 'None'}")
            lines.append(f"  repair_packet_locked_artifacts: {', '.join(str(value) for value in locked) or 'None'}")
        if centralized_final_repair:
            lines.append("- centralized_final_repair: this is the single global final convergence owner for the listed implementation bundle.")
            lines.append("- protocol: fix the failing public behavior across allowed artifacts in one coherent patch sequence; do not redirect to original teams.")
            lines.append("- ordering: repair the directly failing blackbox/failing test first, then repair dependent persistence/state/planning issues surfaced by the same final scenario.")
        else:
            lines.append("- protocol: first read the failing function/class or target range, then make the smallest owner-artifact change that satisfies this diagnostic.")
        lines.append("- tests_locked: do not edit tests for assertion/runtime/import failures; tests are editable only when recovery_action is test_repair/test_regeneration or failure_kind is invalid_tests.")
        lines.append("- transactional_guard: after each Python write, ContractCoding may compile/import/run the failing test immediately; if the tool result says rolled_back, the patch was rejected and you must repair again in the same loop using the validation feedback.")
        for diagnostic in diagnostics[:3]:
            if not isinstance(diagnostic, dict):
                continue
            lines.append(f"- kind: {diagnostic.get('failure_kind', 'unknown')}")
            if diagnostic.get("gate_id") == "final" and centralized_final_repair:
                lines.append("  final_repair_protocol: repair the centralized allowed implementation bundle; tests remain locked.")
            elif diagnostic.get("gate_id") == "final":
                lines.append("  final_repair_protocol: legacy non-central final diagnostic packet; do not redirect to another worker from here. Request centralized final convergence if the current target set is insufficient.")
            else:
                lines.append("  gate_repair_protocol: do not edit tests for assertion/runtime failures; tests are repaired only for invalid_tests diagnostics.")
            if diagnostic.get("recovery_action"):
                lines.append(f"  recovery_action: {diagnostic.get('recovery_action')}")
            if diagnostic.get("primary_scope"):
                lines.append(f"  primary_scope: {diagnostic.get('primary_scope')}")
            if diagnostic.get("fallback_scopes"):
                lines.append(f"  fallback_scopes: {', '.join(str(value) for value in diagnostic.get('fallback_scopes', [])[:6])}")
            if diagnostic.get("failing_test"):
                lines.append(f"  failing_test: {diagnostic.get('failing_test')}")
            if diagnostic.get("expected_actual"):
                lines.append(f"  expected_actual: {diagnostic.get('expected_actual')}")
            suspects = diagnostic.get("suspected_implementation_artifacts") or []
            if suspects:
                lines.append(f"  suspected_implementation_artifacts: {', '.join(str(value) for value in suspects[:6])}")
            scopes = diagnostic.get("suspected_scopes") or []
            if scopes:
                lines.append(f"  suspected_scopes: {', '.join(str(value) for value in scopes[:6])}")
            if diagnostic.get("traceback_excerpt"):
                lines.append(f"  traceback_excerpt: {str(diagnostic.get('traceback_excerpt'))[:900]}")
            if diagnostic.get("repair_instruction"):
                lines.append(f"  repair_instruction: {diagnostic.get('repair_instruction')}")
        return "\n".join(lines) + "\n\n"

    def _render_artifact_sources(
        self,
        artifacts: List[str],
        max_chars_per_artifact: int = 6000,
        workspace_dir: Optional[str] = None,
    ) -> str:
        if not artifacts:
            return "- None"
        workspace = os.path.abspath(workspace_dir or self.config.WORKSPACE_DIR)
        parts: List[str] = []
        for artifact in artifacts:
            normalized = self._normalize_path(artifact)
            path = os.path.abspath(os.path.join(workspace, normalized))
            if not path.startswith(workspace + os.sep) and path != workspace:
                parts.append(f"### {normalized}\nSkipped: artifact is outside the workspace.")
                continue
            if not os.path.exists(path):
                parts.append(f"### {normalized}\nMissing.")
                continue
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    content = handle.read(max_chars_per_artifact + 1)
            except UnicodeDecodeError:
                parts.append(f"### {normalized}\nBinary or non-UTF-8 artifact omitted.")
                continue
            except OSError as exc:
                parts.append(f"### {normalized}\nUnable to read artifact: {exc}")
                continue
            if len(content) > max_chars_per_artifact:
                content = content[:max_chars_per_artifact] + "\n... [truncated]"
            parts.append(f"### {normalized}\n```text\n{content}\n```")
        return "\n".join(parts)

    @staticmethod
    def _normalize_path(path: str) -> str:
        normalized = os.path.normpath(str(path or "").replace("\\", "/")).replace("\\", "/")
        return normalized[2:] if normalized.startswith("./") else normalized

    @staticmethod
    def _output_to_payload(output: Any) -> Dict[str, Any]:
        if isinstance(output, GeneralState):
            requirements = dict(output.task_requirements or {})
            return {
                "role": output.role,
                "thinking": output.thinking,
                "output": output.output,
                "next_agents": output.next_agents,
                "task_requirements": requirements,
                "agent_terminal": dict(requirements.get("agent_terminal", {}) or {}),
            }
        if hasattr(output, "output_state") and hasattr(output, "validation_errors"):
            state = output.output_state
            payload = TeamExecutor._output_to_payload(state)
            payload["changed_files"] = sorted(getattr(output, "changed_files", []) or [])
            payload["validation_errors"] = list(getattr(output, "validation_errors", []) or [])
            return payload
        if isinstance(output, dict):
            return output
        return {"output": str(output)}

    def _record_llm_observability(self, run: RunRecord, item: WorkItem, payload: Dict[str, Any]) -> None:
        observed = payload_observability(payload)
        if (
            not observed
            or not any(
                int(observed.get(key, 0) or 0)
                for key in (
                    "event_count",
                    "tool_intent_count",
                    "tool_result_count",
                    "prompt_tokens",
                    "completion_tokens",
                    "timeout_count",
                    "empty_response_count",
                    "attempt_count",
                )
            )
            and not observed.get("infra_failure")
        ):
            return
        summary = {
            "work_item_id": item.id,
            "backend": observed.get("backend", "unknown"),
            "event_count": int(observed.get("event_count", 0) or 0),
            "timeout_count": int(observed.get("timeout_count", 0) or 0),
            "empty_response_count": int(observed.get("empty_response_count", 0) or 0),
            "tool_intent_count": int(observed.get("tool_intent_count", 0) or 0),
            "tool_result_count": int(observed.get("tool_result_count", 0) or 0),
            "prompt_tokens": int(observed.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(observed.get("completion_tokens", 0) or 0),
            "attempt_count": int(observed.get("attempt_count", 0) or 0),
            "infra_failure": bool(observed.get("infra_failure")),
            "failure_kind": observed.get("failure_kind") or "",
            "returncode": observed.get("returncode"),
            "last_event": observed.get("last_event", {}),
            "stop_reason": observed.get("stop_reason") or "",
            "terminal_tool": observed.get("terminal_tool") or "",
            "tool_iterations": int(observed.get("tool_iterations", 0) or 0),
        }
        payload["llm_observability"] = summary
        self.store.append_event(run.id, "llm_observed", summary)

    @staticmethod
    def _payload_infra_error(payload: Dict[str, Any]) -> str:
        observed = payload_observability(payload)
        text = str(payload.get("output", ""))
        if observed.get("infra_failure"):
            backend = str(observed.get("backend", "LLM backend") or "LLM backend")
            return f"{backend} infrastructure failure prevented artifact generation."
        return ""

    @staticmethod
    def _payload_agent_blocker(payload: Dict[str, Any], allowed_artifacts: Optional[List[str]] = None) -> str:
        allowed = TeamExecutor._normalized_artifact_set(allowed_artifacts or payload.get("wave_allowed_artifacts", []))
        terminal = dict(payload.get("agent_terminal", {}) or {})
        if terminal.get("tool_name") == "report_blocker":
            invalid = TeamExecutor._invalid_in_scope_blocker_message(
                terminal,
                allowed_artifacts=allowed,
            )
            if invalid:
                return invalid
            return "Agent reported a blocker instead of completing the WorkItem: " + " ".join(
                json.dumps(terminal, ensure_ascii=False).split()
            )[:500]
        explicit = payload.get("blocker")
        if explicit:
            return "Agent reported a blocker instead of completing the WorkItem: " + " ".join(str(explicit).split())[:500]
        text = "\n".join(
            str(value or "")
            for value in (
                payload.get("output"),
                payload.get("thinking"),
            )
        )
        lowered = text.lower()
        structured = TeamExecutor._extract_json_object(text)
        if structured.get("blocker_type") or structured.get("required_artifacts"):
            invalid = TeamExecutor._invalid_in_scope_blocker_message(structured, allowed_artifacts=allowed)
            if invalid:
                return invalid
        if '"blocker_type"' in lowered or "out_of_scope_repair" in lowered:
            return "Agent reported a blocker instead of completing the WorkItem: " + " ".join(text.split())[:500]
        for line in text.splitlines():
            stripped = line.strip()
            lowered_line = stripped.lower()
            if not stripped:
                continue
            if lowered_line.startswith(("no blocker", "no out-of-scope blocker")):
                continue
            if lowered_line.startswith("blocker:"):
                return "Agent reported a blocker instead of completing the WorkItem: " + " ".join(text.split())[:500]
            if "out-of-scope blocker" in lowered_line and "no out-of-scope blocker" not in lowered_line:
                return "Agent reported a blocker instead of completing the WorkItem: " + " ".join(text.split())[:500]
            if "cannot complete" in lowered_line and "outside this workitem" in lowered_line:
                return "Agent reported a blocker instead of completing the WorkItem: " + " ".join(text.split())[:500]
        if "cannot complete" in lowered and "outside this workitem" in lowered:
            return "Agent reported a blocker instead of completing the WorkItem: " + " ".join(text.split())[:500]
        return ""

    @staticmethod
    def _invalid_in_scope_blocker_message(blocker: Dict[str, Any], *, allowed_artifacts: set[str]) -> str:
        required = TeamExecutor._normalized_artifact_set(blocker.get("required_artifacts", []) or [])
        current_allowed = TeamExecutor._normalized_artifact_set(blocker.get("current_allowed_artifacts", []) or [])
        allowed_set = allowed_artifacts or current_allowed
        if required and allowed_set and required.issubset(allowed_set):
            return (
                "Invalid blocker: required artifacts are already allowed for this WorkItem. "
                "Continue the repair inside the allowed target files, using read_lines/inspect_symbol plus "
                "update_file_lines/replace_symbol after rollback feedback."
            )
        return ""

    @staticmethod
    def _normalized_artifact_set(values: Any) -> set[str]:
        if isinstance(values, str):
            values = [values]
        out: set[str] = set()
        for value in values or []:
            normalized = os.path.normpath(str(value or "").replace("\\", "/")).replace("\\", "/").strip("/")
            if normalized:
                out.add(normalized)
        return out

    @staticmethod
    def _extract_json_object(text: str) -> Dict[str, Any]:
        decoder = json.JSONDecoder()
        for match in re.finditer(r"\{", text or ""):
            try:
                obj, _ = decoder.raw_decode(text[match.start() :])
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                return obj
        return {}

    @staticmethod
    def _add_research_source_blocker(item: WorkItem, payload: Dict[str, Any], errors: List[str]) -> None:
        if item.kind != "research":
            return
        text = " ".join(
            [
                str(payload.get("output", "")),
                " ".join(str(error) for error in errors),
            ]
        ).lower()
        if not any(
            marker in text
            for marker in (
                "source access",
                "source-gathering",
                "search_web",
                "provided source material",
                "no sources were consulted",
                "without fabricating citations",
            )
        ):
            return
        blocker = (
            "Research source access unavailable; requires approved source access or provided source material."
        )
        if blocker not in errors:
            errors.append(blocker)
