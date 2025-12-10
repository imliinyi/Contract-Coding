import collections.abc
import logging
import json
import threading
from typing import Any, Dict

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
            if not isinstance(content, str):
                try:
                    content = json.dumps(content, ensure_ascii=False)
                except Exception:
                    content = str(content)

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

            if action_type == "update":
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
                            patches = self._diff_to_range_patches(base_doc, content, agent=agent_name)
                            merged_doc = self._apply_range_patches(base_doc, patches)
                            self._document = merged_doc
                            self._version += 1
                            self._history[self._version] = self._document
                            self.logger.info("Update applied via base-relative range merge.")
                        except Exception as e:
                            # Fallback: append update with a conflict marker to avoid losing changes
                            hdr = f"\n\n<!-- update_conflict from {agent_name or 'unknown_agent'} base_v{base_version} vs cur_v{self._version} -->\n"
                            self._document = (self._document or "") + hdr + (content or "")
                            self._version += 1
                            self._history[self._version] = self._document
                            self.logger.error(f"Immediate range merge failed, appended content instead: {e}")

            with open("document.txt", "w", encoding="utf-8") as f:
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
                if action.get('type') != 'update':
                    continue
                full_updates.append(action)

            # Normalize full document updates into base-relative range patches
            for fu in full_updates:
                try:
                    fu_content = fu.get('content', '')
                    patches = self._diff_to_range_patches(base_doc, fu_content, agent=fu.get('agent_name', ''))
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
                # Fallback: sequential layered merge
                work_doc = base_doc
                for fu in full_updates:
                    work_doc = self._apply_layered_patch(base_doc=base_doc, update_doc=fu.get('content', ''), current_doc=work_doc)

            # Commit once
            self._document = work_doc
            self._version += 1
            self._history[self._version] = self._document
            with open("document.txt", "w", encoding="utf-8") as f:
                f.write(self._document)
            # Reset aggregation context
            self._aggregate_mode = False
            self._queued_actions = []

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
