import collections.abc
import json
import re
import threading
from typing import Any, Dict, Optional

from MetaFlow.utils.log import get_logger


def _deep_merge(d1: Dict, d2: Dict) -> Dict:
    """
    Recursively merges two dictionaries.
    Nested dictionaries are merged, lists are concatenated, and other values are overwritten.
    """
    for k, v in d2.items():
        if k in d1 and isinstance(d1[k], dict) and isinstance(v, collections.abc.Mapping):
            d1[k] = _deep_merge(d1[k], v)
        elif k in d1 and isinstance(d1[k], list) and isinstance(v, list):
            d1[k].extend(v)
        else:
            d1[k] = v
    return d1


class DocumentManager:
    """
    Manages a global, collaborative document for a single workflow run.
    Provides methods for agents to read, write, and modify shared knowledge.
    """
    def __init__(self):
        self._document: str = ""
        self.logger = get_logger()
        self._version: int = 0
        self._lock = threading.RLock()
        self._history: Dict[int, str] = {0: ""}

        self._contract_section_specs = [
            {
                "key": "Project Overview",
                "heading": "### 1.1 Project Overview",
                "aliases": [
                    "### Project Overview",
                ],
            },
            {
                "key": "User Stories (Features)",
                "heading": "### 1.2 User Stories (Features)",
                "aliases": [
                    "### User Stories (Features)",
                ],
            },
            {
                "key": "Constraints",
                "heading": "### 1.3 Constraints",
                "aliases": [
                    "### Constraints",
                ],
            },
            {
                "key": "Directory Structure",
                "heading": "### 2.1 Directory Structure",
                "aliases": [
                    "### Directory Structure",
                ],
            },
            {
                "key": "Global Shared Knowledge",
                "heading": "### 2.2 Global Shared Knowledge",
                "aliases": [
                    "### Global Shared Knowledge",
                ],
            },
            {
                "key": "Dependency Relationships",
                "heading": "### 2.3 Dependency Relationships(MUST):",
                "aliases": [
                    "### Dependency Relationships(MUST):",
                    "### Dependency Relationships",
                ],
            },
            {
                "key": "Symbolic API Specifications",
                "heading": "### 2.4 Symbolic API Specifications",
                "aliases": [
                    "### Symbolic API Specifications",
                ],
            },
            {
                "key": "Status Model & Termination Guard",
                "heading": "### Status Model & Termination Guard",
                "aliases": [],
            },
        ]

        self._contract_key_to_heading = {s["key"]: s["heading"] for s in self._contract_section_specs}

        self._contract_heading_to_key: Dict[str, str] = {}
        for spec in self._contract_section_specs:
            self._contract_heading_to_key[spec["heading"]] = spec["key"]
            for alias in spec.get("aliases", []) or []:
                self._contract_heading_to_key[alias] = spec["key"]

        self._contract_headings = list(self._contract_heading_to_key.keys())

    def get(self) -> str:
        """Returns a copy of the entire document with line numbers."""
        # lines = self._document.split('\n')
        # numbered_lines = [f"{i:3d}: {line}" for i, line in enumerate(lines)]
        # return '\n'.join(lines)
        return self._document

    def get_version(self) -> int:
        return self._version

    def execute_actions(self, actions: list):
        """
        Executes a list of document actions based on the new role-oriented model.

        :param actions: A list of action dictionaries, e.g.,
                        [
                          {"type": "add", "agent_name": "Frontend_Engineer", "content": "New UI component..."},
                          {"type": "update", "agent_name": "Backend_Engineer", "content": {"api_spec": ...}},
                        ]
        """
        if not isinstance(actions, list):
            return

        for action in actions:
            action_type = action.get("type")
            content = action.get("content", "")

            # if action_type == "add":
            #     line = action.get("line")
            #     # Ensure line is a valid integer within bounds
            #     try:
            #         line = int(line) if line is not None else 1
            #     except (ValueError, TypeError):
            #         line = 1
            #     documents = self._document.split('\n')

            #     if not self._document.strip():
            #         self._document = content
            #         self.logger.info(f"Document was empty. Set content directly.")
            #         continue

            #     if line < 1:
            #         line = 1
            #     elif line > len(documents) + 1:
            #         line = len(documents) + 1

            #     content = content.split('\n')
            #     documents[line:line] = content
            #     self._document = '\n'.join(documents)
                
            #     self.logger.info(f"Added content to document.")

            if action_type == "add":
                with self._lock:
                    if not isinstance(content, str):
                        try:
                            content = json.dumps(content, ensure_ascii=False)
                        except Exception:
                            content = str(content)

                    content = self._strip_surrounding_blank_lines(content)

                    section = action.get("section")
                    if section is not None:
                        agent_name = action.get("agent_name", "")
                        if agent_name not in ("Project_Manager", "Architect"):
                            self.logger.warning(
                                f"Ignored section add by non-PM agent: {agent_name} section={section}"
                            )
                            continue

                        if agent_name == "Architect" and str(section) != "Symbolic API Specifications":
                            self.logger.warning(
                                f"Ignored section add by Architect for non-symbolic section: {agent_name} section={section}"
                            )
                            continue

                        self._document = self._insert_after_section_end(
                            doc=self._document,
                            section_key=str(section),
                            insert_content=content,
                            agent_name=agent_name,
                        )
                        self._document = self._postprocess_document(self._document)
                        self._version += 1
                        self._history[self._version] = self._document
                        with open('document.md', "w", encoding="utf-8") as f:
                            f.write(self._document)
                        continue

                    if getattr(self, '_aggregate_mode', False):
                        if not hasattr(self, '_queued_actions'):
                            self._queued_actions = []
                        self._queued_actions.append({"type": "add", "content": content})
                    else:
                        sep = "\n\n" if self._document else ""
                        self._document = (self._document or "") + sep + (content or "")
                        self._document = self._postprocess_document(self._document)
                        self._version += 1
                        self._history[self._version] = self._document
                    with open('document.md', "w", encoding="utf-8") as f:
                        f.write(self._document)

            elif action_type == "update":
                agent_name = action.get("agent_name", "")
                base_version = action.get("base_version", self._version)

                with self._lock:
                    # If in layer aggregation mode, queue the update and delay merge
                    if getattr(self, '_aggregate_mode', False):
                        if not hasattr(self, '_queued_actions'):
                            self._queued_actions = []
                        self._queued_actions.append(action)
                    else:
                        # Immediate merge relative to provided base_version using diff→range patches
                        base_doc = self._history.get(base_version, self._document)
                        try:
                            if isinstance(content, collections.abc.Mapping):
                                patches = self._section_patch_to_range_patches(
                                    base_doc=base_doc,
                                    section_patch=dict(content),
                                    agent_name=agent_name,
                                )
                            else:
                                update_doc = self._normalize_update_content_to_full_document(
                                    base_doc=base_doc,
                                    update_content=content,
                                    agent_name=agent_name,
                                )
                                patches = self._diff_to_range_patches(base_doc, update_doc, agent=agent_name)
                            merged_doc = self._apply_range_patches(base_doc, patches)
                            self._document = self._postprocess_document(merged_doc)
                            self._version += 1
                            self._history[self._version] = self._document
                            self.logger.info("Update applied via base-relative range merge.")
                        except Exception as e:
                            # Fallback: append update with a conflict marker to avoid losing changes
                            hdr = f"\n\n<!-- update_conflict from {agent_name or 'unknown_agent'} base_v{base_version} vs cur_v{self._version} -->\n"
                            try:
                                fallback = json.dumps(content, ensure_ascii=False) if not isinstance(content, str) else content
                            except Exception:
                                fallback = str(content)
                            self._document = (self._document or "") + hdr + (fallback or "")
                            self._document = self._postprocess_document(self._document)
                            self._version += 1
                            self._history[self._version] = self._document
                            self.logger.error(f"Immediate range merge failed, appended content instead: {e}")
                    with open('document.md', "w", encoding="utf-8") as f:
                        f.write(self._document)
        
            # elif action_type == "delete":
            #     if agent_name in self._document:
            #         del self._document[agent_name]
            #         logger.info(f"Deleted {agent_name}'s space.")

    # --- Internal helpers for layered merge ---
    def _apply_layered_patch(self, base_doc: str, update_doc: str, current_doc: str) -> str:
        """
        Attempt a three-way style merge: compute diff between base_doc and update_doc,
        and apply non-conflicting hunks to the current document; conflicting hunks are appended
        with conflict markers at localized positions.
        """
        import difflib

        current_lines = current_doc.split('\n') if current_doc else []
        base_lines = base_doc.split('\n') if base_doc else []
        update_lines = update_doc.split('\n') if update_doc else []

        sm = difflib.SequenceMatcher(a=base_lines, b=update_lines)
        opcodes = sm.get_opcodes()

        def find_region(doc_lines, region_lines, start_hint=0):
            if not region_lines:
                return -1
            # Try exact match search
            for i in range(start_hint, max(0, len(doc_lines) - len(region_lines)) + 1):
                if doc_lines[i:i+len(region_lines)] == region_lines:
                    return i
            return -1

        # Build a working copy of current document
        work = current_lines[:]
        cursor_hint = 0

        for tag, i1, i2, j1, j2 in opcodes:
            if tag == 'equal':
                # advance hint by trying to find the equal region in the working doc
                base_seg = base_lines[i1:i2]
                idx = find_region(work, base_seg, start_hint=cursor_hint)
                if idx != -1:
                    cursor_hint = idx + len(base_seg)
                continue

            base_seg = base_lines[i1:i2]
            new_seg = update_lines[j1:j2]

            # Find anchor in current working doc based on base segment
            anchor_idx = find_region(work, base_seg, start_hint=0)

            if tag == 'replace':
                if anchor_idx != -1:
                    # Replace exact matched segment
                    work[anchor_idx:anchor_idx+len(base_seg)] = new_seg
                    cursor_hint = anchor_idx + len(new_seg)
                else:
                    # Conflict: append localized marker
                    marker = [f"<!-- layered_merge_conflict: replace base[{i1}:{i2}] not found in current -->"]
                    work.extend(marker + new_seg)
                    cursor_hint = len(work)

            elif tag == 'delete':
                if anchor_idx != -1:
                    del work[anchor_idx:anchor_idx+len(base_seg)]
                    cursor_hint = anchor_idx
                else:
                    # Unable to find delete region—skip to avoid removing others' changes
                    marker = [f"<!-- layered_merge_skip: delete base[{i1}:{i2}] not found in current -->"]
                    work.extend(marker)
                    cursor_hint = len(work)

            elif tag == 'insert':
                # Insert based on context: try preceding base line as anchor; else append
                insert_at = -1
                if i1 > 0:
                    prev_ctx = base_lines[i1-1:i1]
                    insert_at = find_region(work, prev_ctx, start_hint=0)
                    if insert_at != -1:
                        insert_at = insert_at + len(prev_ctx)
                if insert_at == -1 and i1 < len(base_lines):
                    next_ctx = base_lines[i1:i1+1]
                    insert_at = find_region(work, next_ctx, start_hint=0)
                if insert_at != -1:
                    work[insert_at:insert_at] = new_seg
                    cursor_hint = insert_at + len(new_seg)
                else:
                    # No good anchor; append
                    marker = [f"<!-- layered_merge_note: insert at end for base_pos {i1} -->"]
                    work.extend(marker + new_seg)
                    cursor_hint = len(work)

        return '\n'.join(work)

    def begin_layer_aggregation(self, base_version: int) -> None:
        with self._lock:
            self._aggregate_mode = True
            self._layer_base_version = base_version
            self._queued_actions = []

    def is_aggregating(self) -> bool:
        return getattr(self, '_aggregate_mode', False)

    def queue_actions(self, actions: list) -> None:
        if not isinstance(actions, list):
            return
        with self._lock:
            if not getattr(self, '_aggregate_mode', False):
                # Fallback: execute immediately
                self.execute_actions(actions)
                return
            for a in actions:
                self._queued_actions.append(a)

    def commit_layer_aggregation(self, merge_strategy: str = 'layered_merge') -> None:
        with self._lock:
            if not getattr(self, '_aggregate_mode', False):
                return
            base_doc = self._history.get(getattr(self, '_layer_base_version', self._version), self._document)

            # Collect range patches from queued 'update' actions (order preserved)
            range_patches = []
            full_updates = []
            for action in getattr(self, '_queued_actions', []):
                if action.get('type') == 'update':
                    full_updates.append(action)

            # Normalize full document updates into base-relative range patches
            for fu in full_updates:
                try:
                    fu_content = fu.get('content', '')
                    if isinstance(fu_content, collections.abc.Mapping):
                        patches = self._section_patch_to_range_patches(
                            base_doc=base_doc,
                            section_patch=dict(fu_content),
                            agent_name=fu.get('agent_name', ''),
                        )
                    else:
                        update_doc = self._normalize_update_content_to_full_document(
                            base_doc=base_doc,
                            update_content=fu_content,
                            agent_name=fu.get('agent_name', ''),
                        )
                        patches = self._diff_to_range_patches(base_doc, update_doc, agent=fu.get('agent_name', ''))
                    range_patches.extend(patches)
                except Exception as e:
                    # Fallback: replace whole base with content (treated as one patch)
                    range_patches.append({
                        'start': 1,
                        'end': len(base_doc.split('\n')),
                        'content': fu.get('content', ''),
                        'action': 'replace',
                        'agent': fu.get('agent_name', '')
                    })

            # Apply patches relative to base_doc
            try:
                work_doc = self._apply_range_patches(base_doc, range_patches)
            except Exception:
                work_doc = base_doc
                for fu in full_updates:
                    work_doc = self._apply_layered_patch(base_doc=base_doc, update_doc=fu.get('content', ''), current_doc=work_doc)

            for a in getattr(self, '_queued_actions', []) or []:
                if a.get('type') != 'add':
                    continue

                add_content = a.get('content', '')
                if not isinstance(add_content, str):
                    try:
                        add_content = json.dumps(add_content, ensure_ascii=False)
                    except Exception:
                        add_content = str(add_content)

                section = a.get('section')
                if section is not None:
                    agent_name = a.get('agent_name', '')
                    if agent_name not in ('Project_Manager', 'Architect'):
                        self.logger.warning(
                            f"Ignored section add by non-PM agent: {agent_name} section={section}"
                        )
                        continue
                    if agent_name == 'Architect' and str(section) != 'Symbolic API Specifications':
                        self.logger.warning(
                            f"Ignored section add by Architect for non-symbolic section: {agent_name} section={section}"
                        )
                        continue
                    work_doc = self._insert_after_section_end(
                        doc=work_doc,
                        section_key=str(section),
                        insert_content=add_content,
                        agent_name=agent_name,
                    )
                else:
                    sep = "\n\n" if work_doc else ""
                    work_doc = (work_doc or "") + sep + (add_content or "")

            # Commit once
            self._document = self._postprocess_document(work_doc)
            self._version += 1
            self._history[self._version] = self._document
            with open('document.md', "w", encoding="utf-8") as f:
                f.write(self._document)
            # Reset aggregation context
            self._aggregate_mode = False
            self._queued_actions = []

    def _normalize_update_content_to_full_document(self, base_doc: str, update_content: Any, agent_name: str) -> str:
        if isinstance(update_content, str):
            return update_content

        if isinstance(update_content, collections.abc.Mapping):
            return self._apply_section_patch_to_document(
                base_doc=base_doc,
                section_patch=dict(update_content),
                agent_name=agent_name,
            )

        try:
            return json.dumps(update_content, ensure_ascii=False)
        except Exception:
            return str(update_content)

    def _canonicalize_contract_section_key(self, raw_key: str) -> Optional[str]:
        if not raw_key:
            return None

        k = str(raw_key).strip()

        if k in self._contract_key_to_heading:
            return k

        if k in self._contract_heading_to_key:
            return self._contract_heading_to_key.get(k)

        k_norm = k.lower().strip()
        k_norm = k_norm.replace("###", "").strip()

        k_norm = re.sub(r"^\d+(?:\.\d+)?\s+", "", k_norm)
        k_norm = k_norm.replace("(must)", "")
        k_norm = k_norm.replace("must", "")
        k_norm = k_norm.replace(":", "")
        k_norm = re.sub(r"\s+", " ", k_norm).strip()

        aliases = {
            "project overview": "Project Overview",
            "user stories (features)": "User Stories (Features)",
            "user stories": "User Stories (Features)",
            "features": "User Stories (Features)",
            "constraints": "Constraints",
            "directory structure": "Directory Structure",
            "global shared knowledge": "Global Shared Knowledge",
            "dependency relationships": "Dependency Relationships",
            "symbolic api specifications": "Symbolic API Specifications",
            "status model & termination guard": "Status Model & Termination Guard",
            "status model": "Status Model & Termination Guard",
            "termination guard": "Status Model & Termination Guard",
        }
        return aliases.get(k_norm)

    def _postprocess_document(self, doc: str) -> str:
        text = doc or ""
        text = self._strip_internal_markers(text)
        text = self._dedupe_symbolic_api_section(text)
        text = self._normalize_blank_lines(text)
        return text

    def _strip_surrounding_blank_lines(self, text: str) -> str:
        lines = (text or "").replace('\r\n', '\n').replace('\r', '\n').split('\n')
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(lines)

    def _normalize_blank_lines(self, doc: str) -> str:
        lines = (doc or "").split('\n')
        out: list[str] = []
        blank_run = 0
        for ln in lines:
            if not ln.strip():
                blank_run += 1
                if blank_run <= 1:
                    out.append("")
                continue
            blank_run = 0
            out.append(ln)

        while out and not out[-1].strip():
            out.pop()

        return "\n".join(out)

    def _strip_internal_markers(self, doc: str) -> str:
        lines = (doc or "").split('\n')
        kept: list[str] = []
        marker_re = re.compile(
            r"^<!--\s*(section_patch|section_add|rejected_partial_section_patch|unknown_section_patch_keys|unknown_section_add_key|unknown_section_add_heading)\b"
        )
        for ln in lines:
            if marker_re.match(ln.strip()):
                continue
            kept.append(ln)
        return "\n".join(kept)

    def _dedupe_symbolic_api_section(self, doc: str) -> str:
        if not (doc or "").strip():
            return doc or ""

        lines = (doc or "").split('\n')
        heading_to_idx, key_to_heading_idx, _ = self._index_contract_headings(lines)
        if "Symbolic API Specifications" not in key_to_heading_idx:
            return doc

        heading_idx = key_to_heading_idx["Symbolic API Specifications"]

        def next_heading_idx(from_idx: int) -> int:
            candidates = [i for i in heading_to_idx.values() if i > from_idx]
            return min(candidates) if candidates else len(lines)

        body_start = heading_idx + 1
        body_end = next_heading_idx(heading_idx)
        body_lines = lines[body_start:body_end]
        body_text = "\n".join(body_lines)

        preamble, order, blocks = self._split_symbolic_api_blocks(body_text)
        if not blocks:
            return doc

        occurrences: list[str] = []
        file_re = re.compile(r"^(?:[-*]\s*)?\*\*File:\*\*\s*`?([^`]+)`?\s*$")
        current_file: Optional[str] = None
        for ln in body_lines:
            m = file_re.match(ln.strip())
            if m:
                current_file = m.group(1).strip()
                occurrences.append(current_file)

        unique_order: list[str] = []
        seen: set[str] = set()
        for f in occurrences:
            if f in seen:
                continue
            seen.add(f)
            unique_order.append(f)

        out_lines: list[str] = []
        out_lines.extend(preamble)
        if out_lines:
            out_lines.append("")

        for i, f in enumerate(unique_order):
            block = blocks.get(f)
            if not block:
                continue
            out_lines.extend(block)
            if i != len(unique_order) - 1:
                out_lines.append("")

        new_body = "\n".join(out_lines).strip("\n")
        new_body_lines = new_body.split('\n') if new_body else []
        replacement = new_body_lines
        if replacement and replacement[-1].strip():
            replacement.append("")
        lines[body_start:body_end] = replacement
        return "\n".join(lines)

    def _index_contract_headings(self, lines: list[str]) -> tuple[Dict[str, int], Dict[str, int], Dict[str, str]]:
        heading_to_idx: Dict[str, int] = {}
        key_to_heading_idx: Dict[str, int] = {}
        key_to_heading_str: Dict[str, str] = {}
        for idx, line in enumerate(lines):
            heading = line.strip()
            key = self._contract_heading_to_key.get(heading)
            if not key:
                continue

            if heading not in heading_to_idx:
                heading_to_idx[heading] = idx

            if key not in key_to_heading_idx:
                key_to_heading_idx[key] = idx
                key_to_heading_str[key] = heading
                continue

            canonical_heading = self._contract_key_to_heading.get(key)
            if canonical_heading and heading == canonical_heading and key_to_heading_str.get(key) != canonical_heading:
                key_to_heading_idx[key] = idx
                key_to_heading_str[key] = heading

        return heading_to_idx, key_to_heading_idx, key_to_heading_str

    def _split_symbolic_api_blocks(self, text: str) -> tuple[list[str], list[str], Dict[str, list[str]]]:
        lines = (text or "").replace('\r\n', '\n').replace('\r', '\n').split('\n')
        file_re = re.compile(r"^(?:[-*]\s*)?\*\*File:\*\*\s*`?([^`]+)`?\s*$")

        preamble: list[str] = []
        ordered_files: list[str] = []
        blocks: Dict[str, list[str]] = {}
        current_file: Optional[str] = None
        current_block: list[str] = []

        def flush():
            nonlocal current_file, current_block
            if current_file is None:
                return
            blocks[current_file] = current_block
            ordered_files.append(current_file)
            current_file = None
            current_block = []

        for ln in lines:
            m = file_re.match(ln.strip())
            if m:
                flush()
                current_file = m.group(1).strip()
                current_block = [ln]
                continue
            if current_file is None:
                preamble.append(ln)
            else:
                current_block.append(ln)
        flush()

        while preamble and not preamble[0].strip():
            preamble.pop(0)
        while preamble and not preamble[-1].strip():
            preamble.pop()

        return preamble, ordered_files, blocks

    def _merge_symbolic_api_section_body(self, existing_body: str, patch_body: str) -> str:
        patch_preamble, patch_order, patch_blocks = self._split_symbolic_api_blocks(patch_body)
        if not patch_blocks:
            return patch_body

        existing_preamble, existing_order, existing_blocks = self._split_symbolic_api_blocks(existing_body)
        if not existing_blocks:
            return patch_body

        merged_blocks = dict(existing_blocks)
        for f, block_lines in patch_blocks.items():
            merged_blocks[f] = block_lines

        merged_order = list(existing_order)
        for f in patch_order:
            if f not in merged_order:
                merged_order.append(f)

        out_lines: list[str] = []
        out_lines.extend(existing_preamble)
        if out_lines:
            out_lines.append("")
        for i, f in enumerate(merged_order):
            out_lines.extend(merged_blocks[f])
            if i != len(merged_order) - 1:
                out_lines.append("")

        return "\n".join(out_lines).strip("\n")

    def _section_patch_to_range_patches(self, base_doc: str, section_patch: Dict[str, Any], agent_name: str) -> list[dict]:
        doc = base_doc or ""
        if not doc.strip():
            doc = self._build_empty_contract_document()
        lines = doc.split('\n')

        heading_to_idx, key_to_heading_idx, _ = self._index_contract_headings(lines)

        def next_heading_idx(from_idx: int) -> int:
            candidates = [i for i in heading_to_idx.values() if i > from_idx]
            return min(candidates) if candidates else len(lines)

        def looks_like_partial_only_status_update(text: str) -> bool:
            t = (text or "").strip()
            if not t:
                return True
            if "**File:**" in t:
                return False
            status_lines = [ln for ln in t.split("\n") if ln.strip()]
            if not status_lines:
                return True
            return all(
                re.match(r"^\*\s*\*\*Status(?::)?\*\*:?\s*\w+\s*$", ln.strip())
                for ln in status_lines
            )

        def extract_status_from_lines(block_lines: list[str]) -> Optional[str]:
            for ln in block_lines:
                m = re.search(r"\*\*Status(?::)?\*\*:?\s*(\w+)", ln)
                if m:
                    return m.group(1).strip()
            return None

        patches: list[dict] = []

        for raw_key, raw_value in (section_patch or {}).items():
            canonical_key = self._canonicalize_contract_section_key(str(raw_key))
            if not canonical_key:
                continue

            if canonical_key not in key_to_heading_idx:
                continue

            heading_idx = key_to_heading_idx[canonical_key]
            body_start_idx = heading_idx + 1
            body_end_idx = next_heading_idx(heading_idx)

            existing_body_text = "\n".join(lines[body_start_idx:body_end_idx])

            value = "" if raw_value is None else str(raw_value)
            value = value.replace('\r\n', '\n').replace('\r', '\n')

            candidate_lines = [ln for ln in value.split('\n')]
            while candidate_lines and not candidate_lines[0].strip():
                candidate_lines.pop(0)
            if candidate_lines and candidate_lines[0].strip() in self._contract_headings:
                candidate_lines = candidate_lines[1:]
            while candidate_lines and not candidate_lines[-1].strip():
                candidate_lines.pop()

            candidate_body_text = "\n".join(candidate_lines)

            if canonical_key == "Symbolic API Specifications":
                _, patch_order, patch_blocks = self._split_symbolic_api_blocks(candidate_body_text)
                if patch_blocks:
                    section_lines = lines[body_start_idx:body_end_idx]
                    file_re = re.compile(r"^(?:[-*]\s*)?\*\*File:\*\*\s*`?([^`]+)`?\s*$")

                    def find_block_range(file_path: str) -> Optional[tuple[int, int]]:
                        start = None
                        for i in range(len(section_lines) - 1, -1, -1):
                            m = file_re.match(section_lines[i].strip())
                            if m and m.group(1).strip() == file_path:
                                start = i
                                break
                        if start is None:
                            return None
                        end = len(section_lines)
                        for j in range(start + 1, len(section_lines)):
                            if file_re.match(section_lines[j].strip()):
                                end = j
                                break
                        return start, end

                    for f in patch_order:
                        block_lines = patch_blocks.get(f) or []
                        if not block_lines:
                            continue

                        new_status = extract_status_from_lines(block_lines)

                        block_content_lines = []
                        block_content_lines.extend(block_lines)
                        block_content_lines.append("")
                        block_content = "\n".join(block_content_lines)

                        rng = find_block_range(f)
                        if rng is not None:
                            s, e = rng

                            existing_block_lines = section_lines[s:e]
                            existing_status = extract_status_from_lines(existing_block_lines)
                            if new_status == 'VERIFIED' and existing_status not in ('DONE', 'VERIFIED'):
                                self.logger.warning(
                                    f"Rejected premature VERIFIED status update for {f}: existing={existing_status} agent={agent_name}"
                                )
                                continue

                            start_line = (body_start_idx + s) + 1
                            end_line = body_start_idx + e
                            patches.append({
                                'start': start_line,
                                'end': end_line,
                                'content': block_content,
                                'action': 'replace',
                                'agent': agent_name,
                            })
                        else:
                            if new_status == 'VERIFIED':
                                self.logger.warning(
                                    f"Rejected VERIFIED insert for unknown file block {f}: agent={agent_name}"
                                )
                                continue

                            if body_start_idx < body_end_idx:
                                insert_after_line = body_end_idx
                            else:
                                insert_after_line = heading_idx + 1
                            patches.append({
                                'start': insert_after_line,
                                'end': insert_after_line,
                                'content': block_content,
                                'action': 'insert_after',
                                'agent': agent_name,
                            })

                    continue

            would_clobber = False
            if existing_body_text and "**File:**" in existing_body_text and "**File:**" not in candidate_body_text:
                would_clobber = True
            if canonical_key == "Symbolic API Specifications" and looks_like_partial_only_status_update(candidate_body_text):
                would_clobber = True
            if would_clobber:
                self.logger.warning(
                    f"Rejected partial section patch that would clobber content: section={canonical_key} agent={agent_name}"
                )
                continue

            new_body_lines = []
            if candidate_body_text:
                new_body_lines.extend(candidate_body_text.split('\n'))
            new_body_lines.append("")
            new_body_content = "\n".join(new_body_lines)

            heading_line_number = heading_idx + 1
            if body_start_idx < body_end_idx:
                start_line = body_start_idx + 1
                end_line = body_end_idx
                patches.append({
                    'start': start_line,
                    'end': end_line,
                    'content': new_body_content,
                    'action': 'replace',
                    'agent': agent_name,
                })
            else:
                patches.append({
                    'start': heading_line_number,
                    'end': heading_line_number,
                    'content': new_body_content,
                    'action': 'insert_after',
                    'agent': agent_name,
                })

        return patches

    def _apply_section_patch_to_document(self, base_doc: str, section_patch: Dict[str, Any], agent_name: str) -> str:
        if not base_doc.strip():
            base_doc = self._build_empty_contract_document()

        lines = base_doc.split('\n')

        heading_to_idx: Dict[str, int] = {}
        key_to_heading_idx: Dict[str, int] = {}
        key_to_heading_str: Dict[str, str] = {}
        for idx, line in enumerate(lines):
            heading = line.strip()
            key = self._contract_heading_to_key.get(heading)
            if not key:
                continue

            if heading not in heading_to_idx:
                heading_to_idx[heading] = idx

            if key not in key_to_heading_idx:
                key_to_heading_idx[key] = idx
                key_to_heading_str[key] = heading
            else:
                canonical_heading = self._contract_key_to_heading.get(key)
                if canonical_heading and heading == canonical_heading and key_to_heading_str.get(key) != canonical_heading:
                    key_to_heading_idx[key] = idx
                    key_to_heading_str[key] = heading

        def _next_heading_idx(from_idx: int) -> int:
            candidates = [i for i in heading_to_idx.values() if i > from_idx]
            return min(candidates) if candidates else len(lines)

        def _looks_like_partial_only_status_update(text: str) -> bool:
            t = (text or "").strip()
            if not t:
                return True
            if "**File:**" in t:
                return False
            status_lines = [ln for ln in t.split("\n") if ln.strip()]
            if not status_lines:
                return True
            return all(re.match(r"^\*\s*\*\*Status\*\*\s*:\s*\w+\s*$", ln.strip()) for ln in status_lines)

        unknown_items: Dict[str, Any] = {}

        for raw_key, raw_value in (section_patch or {}).items():
            canonical_key = self._canonicalize_contract_section_key(str(raw_key))
            if not canonical_key:
                unknown_items[str(raw_key)] = raw_value
                continue

            heading = self._contract_key_to_heading.get(canonical_key)
            if not heading:
                unknown_items[str(raw_key)] = raw_value
                continue

            value = "" if raw_value is None else str(raw_value)
            value = value.replace('\r\n', '\n').replace('\r', '\n')

            candidate_lines = [ln for ln in value.split('\n')]
            while candidate_lines and not candidate_lines[0].strip():
                candidate_lines.pop(0)
            if candidate_lines and candidate_lines[0].strip() in self._contract_headings:
                candidate_lines = candidate_lines[1:]

            while candidate_lines and not candidate_lines[-1].strip():
                candidate_lines.pop()

            new_body_lines: list[str] = []
            new_body_lines.extend(candidate_lines)
            new_body_lines.append("")

            existing_body_text = ""
            if canonical_key in key_to_heading_idx:
                h_idx = key_to_heading_idx[canonical_key]
                body_start = h_idx + 1
                body_end = _next_heading_idx(h_idx)
                existing_body_text = "\n".join(lines[body_start:body_end])

            would_clobber = False
            if existing_body_text and "**File:**" in existing_body_text and "**File:**" not in value:
                would_clobber = True
            if canonical_key == "Symbolic API Specifications" and _looks_like_partial_only_status_update(value):
                would_clobber = True

            if would_clobber:
                self.logger.warning(
                    f"Rejected partial section patch that would clobber content: section={canonical_key} agent={agent_name}"
                )
                continue

            if canonical_key in key_to_heading_idx:
                h_idx = key_to_heading_idx[canonical_key]
                body_start = h_idx + 1
                body_end = _next_heading_idx(h_idx)
                lines[body_start:body_end] = new_body_lines

                delta = len(new_body_lines) - (body_end - body_start)
                if delta != 0:
                    updated_heading_to_idx = {}
                    for h, i in heading_to_idx.items():
                        if i > h_idx:
                            updated_heading_to_idx[h] = i + delta
                        else:
                            updated_heading_to_idx[h] = i
                    heading_to_idx = updated_heading_to_idx
                    updated_key_to_heading_idx = {}
                    for k, i in key_to_heading_idx.items():
                        if i > h_idx:
                            updated_key_to_heading_idx[k] = i + delta
                        else:
                            updated_key_to_heading_idx[k] = i
                    key_to_heading_idx = updated_key_to_heading_idx
            else:
                if lines and lines[-1].strip():
                    lines.append("")
                canonical_heading = self._contract_key_to_heading.get(canonical_key, heading)
                heading_to_idx[canonical_heading] = len(lines)
                key_to_heading_idx[canonical_key] = len(lines)
                key_to_heading_str[canonical_key] = canonical_heading
                lines.append(canonical_heading)
                lines.extend(new_body_lines)

        if unknown_items:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(f"<!-- unknown_section_patch_keys by {agent_name or 'unknown_agent'} -->")
            try:
                lines.append(json.dumps(unknown_items, ensure_ascii=False))
            except Exception:
                lines.append(str(unknown_items))

        return "\n".join(lines)

    def _insert_after_section_end(self, doc: str, section_key: str, insert_content: str, agent_name: str) -> str:
        base_doc = (doc or "").replace('\r\n', '\n').replace('\r', '\n')
        if not base_doc.strip():
            base_doc = self._build_empty_contract_document()

        canonical_key = self._canonicalize_contract_section_key(section_key)
        if not canonical_key:
            marker = f"\n\n<!-- unknown_section_add_key by {agent_name or 'unknown_agent'}: {section_key} -->\n"
            return (base_doc or "") + marker + (insert_content or "")

        canonical_heading = self._contract_key_to_heading.get(canonical_key)
        if not canonical_heading:
            marker = f"\n\n<!-- unknown_section_add_heading by {agent_name or 'unknown_agent'}: {canonical_key} -->\n"
            return (base_doc or "") + marker + (insert_content or "")

        lines = base_doc.split('\n')

        section_heading_idxs: list[tuple[int, str]] = []
        for idx, line in enumerate(lines):
            raw = line.strip()
            if not raw.startswith("###"):
                continue
            k = self._canonicalize_contract_section_key(raw)
            if k:
                section_heading_idxs.append((idx, k))

        section_heading_idxs.sort(key=lambda x: x[0])

        def _next_heading_idx(from_idx: int) -> int:
            for i, _k in section_heading_idxs:
                if i > from_idx:
                    return i
            return len(lines)

        insert_lines = []
        insert_content = (insert_content or "").replace('\r\n', '\n').replace('\r', '\n')
        insert_lines.extend(insert_content.split('\n') if insert_content else [])
        insert_lines.append("")

        target_heading_idx = None
        for i, k in reversed(section_heading_idxs):
            if k == canonical_key:
                target_heading_idx = i
                break

        if target_heading_idx is not None:
            h_idx = target_heading_idx
            end_idx = _next_heading_idx(h_idx)
            while end_idx > h_idx + 1 and not lines[end_idx - 1].strip():
                end_idx -= 1
            lines[end_idx:end_idx] = insert_lines
            return "\n".join(lines)

        sep = "\n\n" if base_doc.strip() else ""
        return (base_doc or "") + sep + (self._strip_surrounding_blank_lines(insert_content) or "")

    def _build_empty_contract_document(self) -> str:
        lines: list[str] = []
        lines.append("## Product Requirement Document (PRD)")
        lines.append("")
        lines.append("### 1.1 Project Overview")
        lines.append("")
        lines.append("### 1.2 User Stories (Features)")
        lines.append("")
        lines.append("### 1.3 Constraints")
        lines.append("")
        lines.append("## Technical Architecture Document (System Design)")
        lines.append("")
        lines.append("### 2.1 Directory Structure")
        lines.append("")
        lines.append("### 2.2 Global Shared Knowledge")
        lines.append("")
        lines.append("### 2.3 Dependency Relationships(MUST):")
        lines.append("")
        lines.append("### 2.4 Symbolic API Specifications")
        lines.append("")
        lines.append("### Status Model & Termination Guard")
        lines.append("")
        return "\n".join(lines)

    # --- Range patches (git-like line ranges) ---
    def _apply_range_patches(self, base_doc: str, patches: list) -> str:
        """
        Apply base-relative patches. Overlapping ranges are grouped; union range is removed
        and replacements are concatenated in arrival order.
        """
        base_lines = base_doc.split('\n') if base_doc else []
        n = len(base_lines)

        # normalize patches
        norm = []
        order = 0
        for p in patches:
            start = max(1, int(p.get('start', 1)))
            end = max(start, int(p.get('end', start)))
            action = p.get('action', 'replace')
            content_lines = str(p.get('content', '')).split('\n')
            norm.append({
                'start': min(start, n + 1),
                'end': min(end, n),
                'action': action,
                'content_lines': content_lines,
                'agent': p.get('agent', ''),
                'order': order
            })
            order += 1

        # sort by start, end, order
        norm.sort(key=lambda x: (x['start'], x['end'], x['order']))

        # build overlap groups
        groups = []
        for p in norm:
            if not groups:
                groups.append({'start': p['start'], 'end': p['end'], 'patches': [p]})
            else:
                last = groups[-1]
                if p['start'] <= last['end']:
                    last['patches'].append(p)
                    last['end'] = max(last['end'], p['end'])
                else:
                    groups.append({'start': p['start'], 'end': p['end'], 'patches': [p]})

        out = []
        cursor = 1
        for g in groups:
            g_start = g['start']
            g_end = g['end']

            # base before group
            if cursor <= g_start - 1:
                out.extend(base_lines[cursor - 1 : g_start - 1])

            # replacements in arrival order
            replaces = [x for x in g['patches'] if x['action'] == 'replace']
            inserts = [x for x in g['patches'] if x['action'] == 'insert_after']
            for r in replaces:
                out.extend(r['content_lines'])
            for ins in inserts:
                out.extend(ins['content_lines'])

            cursor = g_end + 1

        # remainder
        if cursor <= n:
            out.extend(base_lines[cursor - 1 : n])

        return '\n'.join(out)

    def _diff_to_range_patches(self, base_doc: str, update_doc: str, agent: str = '') -> list:
        """
        Convert a full-document update into base-relative range patches using a diff.
        """
        import difflib
        base_lines = base_doc.split('\n') if base_doc else []
        update_lines = update_doc.split('\n') if update_doc else []
        sm = difflib.SequenceMatcher(a=base_lines, b=update_lines)
        patches = []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == 'equal':
                continue
            start = i1 + 1
            end = i2
            content = '\n'.join(update_lines[j1:j2])
            patches.append({
                'start': start,
                'end': max(start, end),
                'content': content,
                'action': 'replace',
                'agent': agent
            })
        return patches
