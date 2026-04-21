from __future__ import annotations

import contextlib
import io

from ContractCoding.memory.audit import audit_file_existence, audit_file_versions


class ContractAuditRunner:
    def __init__(self, workspace_dir: str):
        self.workspace_dir = workspace_dir

    def run(self, document_content: str) -> str:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            audit_file_existence(document_content, self.workspace_dir)
            audit_file_versions(document_content, self.workspace_dir)
        return buffer.getvalue().strip()
