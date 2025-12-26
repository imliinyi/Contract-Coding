import re
import os

# --- Helpers to extract file paths purely from backticks ---
_ALLOWED_EXTS = {'.py', '.js', '.ts', '.tsx', '.jsx', '.html', '.css'}

def _strip_code_fences(text: str) -> str:
    """
    Remove triple‑backtick code fence blocks (e.g., project structure trees, mermaid, etc.)
    so that we do NOT treat their contents as candidate file paths.
    """
    return re.sub(r"```[\s\S]*?```", "", text)

def _token_looks_like_path(token: str) -> bool:
    if not token:
        return False
    token = token.strip()
    # Ignore pure directories or tokens ending with '/'
    if token.endswith('/'):
        return False
    # Allow path segments like "game/main.py" or bare filename "main.py"
    last = token.split('/')[-1].strip()
    return any(last.endswith(ext) for ext in _ALLOWED_EXTS)

def _extract_directory_structure_paths(document_content: str) -> list[str]:
    """
    Extract file paths from directory structure trees within code blocks.
    Handles standard tree formats (using characters like |, -, +, `) and indentation.
    """
    paths = []
    # Find all code blocks
    code_blocks = re.findall(r"```(?:[\w]*\n)?([\s\S]*?)```", document_content)
    
    for block_content in code_blocks:
        # Heuristic: check if it looks like a file tree containing "workspace/" or tree characters
        if not ("workspace/" in block_content or "|--" in block_content or "|__" in block_content or "├──" in block_content):
            continue
            
        lines = block_content.splitlines()
        path_stack = [] # List of (indent_depth, name)
        
        for line in lines:
            # Skip empty lines
            if not line.strip(): continue
            
            # Regex to separate prefix (indentation + tree chars) from name
            # Matches: (prefix)(name)
            # Prefix includes spaces, tabs, and tree drawing characters
            match = re.match(r"^([ \t\|`\+\-\\\u2500-\u257f]*)(.*)$", line)
            if not match: continue
            
            prefix, name = match.groups()
            name = name.strip()
            
            if not name: continue
            
            # Calculate indentation depth based on prefix length
            depth = len(prefix)
            
            # Pop from stack until we find the parent (item with strictly smaller depth)
            while path_stack and path_stack[-1][0] >= depth:
                path_stack.pop()
                
            path_stack.append((depth, name))
            
            # If it looks like a file (has extension), add to paths
            if _token_looks_like_path(name):
                # Construct full path from stack names
                # Remove trailing slashes from directory names in stack
                clean_stack_names = [p[1].rstrip('/') for p in path_stack]
                full_path = "/".join(clean_stack_names)
                paths.append(full_path)
                
    return paths

def _extract_backtick_paths(document_content: str) -> list[str]:
    """Collect all backticked tokens that look like file paths (by extension).
    Also includes files found in directory structure trees within code blocks.
    """
    # 1. Extract from directory structures in code blocks (Highest Priority as per user)
    dir_paths = _extract_directory_structure_paths(document_content)

    # 2. Extract from backticks in text (excluding code blocks to avoid noise)
    doc_no_fences = _strip_code_fences(document_content)
    tokens = re.findall(r"`([^`]+)`", doc_no_fences)
    text_paths: list[str] = []
    for tok in tokens:
        for line in tok.splitlines():
            cand = line.strip()
            if _token_looks_like_path(cand):
                text_paths.append(cand)
    
    # Merge and dedupe preserving order
    all_paths = dir_paths + text_paths
    seen = set()
    uniq = []
    for p in all_paths:
        # Normalize path for deduplication: remove 'workspace/' prefix
        norm_p = p
        while norm_p.startswith('workspace/'):
            norm_p = norm_p.replace('workspace/', '', 1)
            
        if norm_p not in seen:
            seen.add(norm_p)
            uniq.append(p)
    return uniq

def _find_in_workspace(workspace_path: str, candidate: str) -> str | None:
    """Resolve candidate path to actual file under workspace, with normalization."""
    wp = os.path.abspath(workspace_path)
    cand = candidate.strip().replace('\\', '/')
    # Remove any stray whitespace/newlines inside the candidate
    cand = cand.replace('\r', '').replace('\n', '')
    if cand.startswith('./'):
        cand = cand[2:]
    # Strip redundant leading workspace segment if present
    workspace_base = os.path.basename(wp)
    if cand.startswith(f"{workspace_base}/"):
        cand = cand[len(workspace_base)+1:]
    if cand.startswith('workspace/'):
        cand = cand.replace('workspace/', '')
    if cand.startswith('/'):
        cand = cand.lstrip('/')
    
    # Normalize candidate by removing any 'workspace/' prefix again just in case
    # This handles cases where candidate is like "workspace/systems/inventory_system.py"
    # and we want to match it against actual file "systems/inventory_system.py" inside workspace dir
    while cand.startswith('workspace/'):
        cand = cand.replace('workspace/', '', 1)

    # If candidate includes directory, join directly; else search by basename
    if '/' in cand:
        full = os.path.abspath(os.path.join(wp, cand))
        return full if os.path.exists(full) else None
    base = cand
    for root, _, files in os.walk(wp):
        if base in files:
            return os.path.join(root, base)
    return None

def _extract_section(document_content: str, heading_regex: str) -> str:
    m = re.search(heading_regex, document_content, re.MULTILINE)
    if not m:
        return ""
    start = m.end()
    tail = document_content[start:]
    n = re.search(r"^### ", tail, re.MULTILINE)
    end_idx = n.start() if n else len(tail)
    return tail[:end_idx]

def get_spec_files(document_content: str) -> set[str]:
    """
    Robustly extract files that have a Symbolic API Specification.
    Recognizes multiple formats:
    - "**File:** `path`" blocks (anywhere, typically under 2.4)
    - "- **File**: `path`" list items
    - Status bullets like "- **path.py**: DONE" (treated as having a spec entry)
    If the 2.4 section cannot be isolated, falls back to scanning the entire document.
    """
    out: set[str] = set()

    def add_path(p: str):
        if not p:
            return
        q = p.strip()
        if not _token_looks_like_path(q):
            return
        # Normalize workspace prefix
        while q.startswith('workspace/'):
            q = q.replace('workspace/', '', 1)
        out.add(q)

    # Try to isolate 2.4 section, but do not rely solely on it
    sec = _extract_section(document_content, r"^###\s*2\.4.*$")
    search_scopes = [sec] if sec else []
    # Always also scan the entire document to avoid missing unconventional placements
    search_scopes.append(document_content)

    # Patterns (robust to bold markup and colon placement). Python files only (.py)
    # Matches: **File:** `path.py`  OR  **File**: path.py  OR  File: path.py
    file_token_pat = re.compile(r"(?i)(?:\*\*\s*File\s*:?-?\s*\*\*|\bFile\b)\s*:?-?\s*`?([A-Za-z0-9_./\\-]+\.py)" )
    # Matches list form: - **File:** path.py
    list_file_token_pat = re.compile(r"(?i)-\s*(?:\*\*\s*File\s*:?-?\s*\*\*|\bFile\b)\s*:?-?\s*`?([A-Za-z0-9_./\\-]+\.py)")
    # Matches status bullets: - **path.py**: DONE
    status_bullet_pat = re.compile(r"(?i)-\s*\*\*([^*\n]+?\.py)\*\*\s*:\s*(DONE|TODO|IN_PROGRESS|ERROR)")

    for scope in search_scopes:
        if not scope:
            continue
        for m in file_token_pat.finditer(scope):
            add_path(m.group(1))
        for m in list_file_token_pat.finditer(scope):
            add_path(m.group(1))
        for m in status_bullet_pat.finditer(scope):
            add_path(m.group(1))

    return out

def check_missing_specs(document_content: str) -> list[str]:
    dir_paths = _extract_directory_structure_paths(document_content)
    dir_set = set()
    for p in dir_paths:
        q = p
        while q.startswith('workspace/'):
            q = q.replace('workspace/', '', 1)
        dir_set.add(q)
    spec_set = get_spec_files(document_content)
    missing = sorted(x for x in dir_set if x not in spec_set)
    return missing

def audit_file_existence(document_content, workspace_path):
    missing = check_missing_files(document_content, workspace_path)
    if missing:
        print("Missing files:")
        for m in missing:
            print(f"- {m}")
    else:
        print("All files exist.")

def audit_file_versions(document_content, workspace_path):
    """Compare file versions between document and workspace.
    """
    version_mismatches = []

    # 1) Parse "#### <n>. ..." sections under File-Based Sub-Tasks
    section_pattern = re.compile(r"(?s)^####\s*\d+\.\s.*?(?=^####\s*\d+\.\s|^### |\Z)", re.MULTILINE)
    sections = section_pattern.findall(document_content)

    path_to_version: dict[str, int] = {}

    for sec in sections:
        # Find all Paths/Paths entries in this section
        paths = re.findall(r"-\s*\*\*Paths?\*\*:\s*`([^`]+)`", sec)

        # Fallback: use header-backticked filename if no Paths/Paths provided
        if not paths:
            header_match = re.search(r"^####\s*\d+\.\s*(.*)$", sec, re.MULTILINE)
            if header_match:
                header_text = header_match.group(1)
                header_paths = re.findall(r"`([^`]+)`", header_text)
                if header_paths:
                    paths = header_paths

        if not paths:
            continue

        # Find Version in this section
        vm = re.search(r"-\s*\*\*Version\*\*:\s*(\d+)", sec)
        if not vm:
            continue
        doc_ver = int(vm.group(1))
        for p in paths:
            path_to_version[p.strip()] = doc_ver

    # 2) Also support standalone "- **File**: `...`" blocks with Version
    inline_file_pattern = re.compile(r"-\s\*\*File\*\*:\s*`([^`]+)`[\s\S]*?-\s\*\*Version\*\*:\s(\d+)")
    for m in inline_file_pattern.finditer(document_content):
        path_to_version[m.group(1).strip()] = int(m.group(2))

    # 3) Support PRD style "**File:** `...`" blocks (e.g. in test/document.md)
    # Split by "**File:** `path`" to isolate blocks
    prd_split_pattern = re.compile(r"(\*\*File\*\*:\s*`[^`]+`)")
    parts = prd_split_pattern.split(document_content)
    # parts[0] is pre-text, parts[1] is file-line, parts[2] is content, parts[3] is file-line...
    for i in range(1, len(parts), 2):
        file_line = parts[i]
        content = parts[i+1]
        
        path_match = re.search(r"`([^`]+)`", file_line)
        if path_match:
            path = path_match.group(1).strip()
            # Look for Version in the content block
            # Matches: * **Version:** N  or  - **Version:** N
            ver_match = re.search(r"[\*\-]\s*\*\*Version\*\*:\s*(\d+)", content)
            if ver_match:
                path_to_version[path] = int(ver_match.group(1))

    # 4) Compare versions for files that have explicit doc versions
    for p, doc_ver in path_to_version.items():
        resolved = _find_in_workspace(workspace_path, p)
        if not resolved:
            continue  # existence audit will报告缺失
        try:
            with open(resolved, 'r', encoding='utf-8') as f:
                first = f.readline().strip()
                mm = re.match(r"#\s*version:\s*(\d+)", first, re.IGNORECASE)
                if mm:
                    file_ver = int(mm.group(1))
                    if file_ver != doc_ver:
                        version_mismatches.append((p, file_ver, doc_ver))
                else:
                    version_mismatches.append((p, "N/A", doc_ver))
        except Exception:
            version_mismatches.append((p, "read_error", doc_ver))

    if version_mismatches:
        print("\nVersion mismatches:")
        for file, fver, dver in version_mismatches:
            print(f"- {file}: File version ({fver}) does not match document version ({dver})")
    else:
        print("\nAll file versions match the document.")

def get_documented_files(document_content: str) -> set[str]:
    """
    Extract files explicitly defined in Symbolic API Specifications or File-Based Sub-Tasks.
    Uses robust parsing aligned with version audit to find files that have a defined Specification blocks.
    """
    documented_files = set()

    # 1) PRD style "**File:** `...`" blocks with body sections (same split logic as version audit)
    prd_split_pattern = re.compile(r"(\*\*File\*\*:\s*`[^`]+`)")
    parts = prd_split_pattern.split(document_content)
    for i in range(1, len(parts), 2):
        file_line = parts[i]
        path_match = re.search(r"`([^`]+)`", file_line)
        if path_match:
            documented_files.add(path_match.group(1).strip())

    # 2) Also support standalone list item "- **File**: `...`" lines
    inline_file_pattern = re.compile(r"-\s\*\*File\*\*:\s*`([^`]+)`")
    for m in inline_file_pattern.finditer(document_content):
        documented_files.add(m.group(1).strip())

    # 3) Parse "#### <n>. ..." sections and collect any backticked paths within headers/Paths fields
    section_pattern = re.compile(r"(?s)^####\s*\d+\.\s.*?(?=^####\s*\d+\.\s|^### |\Z)", re.MULTILINE)
    sections = section_pattern.findall(document_content)
    for sec in sections:
        # Prefer explicit Paths entries
        paths = re.findall(r"-\s*\*\*Paths?\*\*:\s*`([^`]+)`", sec)
        if not paths:
            header_match = re.search(r"^####\s*\d+\.\s*(.*)$", sec, re.MULTILINE)
            if header_match:
                header_paths = re.findall(r"`([^`]+)`", header_match.group(1))
                paths = header_paths or []
        for p in paths:
            documented_files.add(p.strip())

    # 4) Also include paths extracted from directory structure code blocks (treated as documented per PRD)
    dir_paths = _extract_directory_structure_paths(document_content)
    for p in dir_paths:
        # Normalize 'workspace/' prefix since workspace root is provided separately
        norm = p
        while norm.startswith('workspace/'):
            norm = norm.replace('workspace/', '', 1)
        documented_files.add(norm)

    # 5) Normalize duplicates and return
    return documented_files

def get_workspace_files(workspace_path: str) -> set[str]:
    """
    Get all actual files in workspace (relative paths), excluding hidden/system files.
    """
    wp = os.path.abspath(workspace_path)
    actual_files = set()
    for root, dirs, files in os.walk(wp):
        # Skip hidden dirs
        dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__']
        for f in files:
            if f.startswith('.') or f.endswith('.pyc'):
                continue
            full_path = os.path.join(root, f)
            rel_path = os.path.relpath(full_path, wp)
            actual_files.add(rel_path)
    return actual_files

def check_undocumented_files(document_content: str, workspace_path: str) -> list[str]:
    """
    Return list of files present in workspace but NOT defined in document specs.
    """
    documented = get_documented_files(document_content)
    # Normalize documented paths to match relpath format (no leading ./, no workspace/ prefix if possible)
    # But since get_workspace_files returns relative paths from workspace root, 
    # we need to be careful about matching. 
    # Let's try to normalize documented paths to be relative to workspace root if they look like it.
    
    norm_documented = set()
    for p in documented:
        # Simple normalization: remove 'workspace/' prefix if present
        if p.startswith('workspace/'):
            p = p.replace('workspace/', '', 1)
        norm_documented.add(p)
        
    actual = get_workspace_files(workspace_path)
    
    # Undocumented = Actual - Documented
    undocumented = []
    for f in actual:
        # Check if f is in norm_documented
        # Also handle potential directory prefix mismatches loosely if needed, 
        # but strict match is better for "Symbolic API Specifications".
        if f not in norm_documented:
            undocumented.append(f)
            
    return sorted(undocumented)

def check_missing_files(document_content: str, workspace_path: str) -> list[str]:
    """
    Return list of files defined in document but MISSING in workspace.
    """
    # Use existing extraction logic which covers all backticked paths (including tree)
    # OR use get_documented_files if we only care about Spec-defined files.
    # User requirement: "every file is in the project". 
    # Let's use get_documented_files to be consistent with "Symbolic API Specifications".
    documented = get_documented_files(document_content)
    
    missing = []
    for p in documented:
        if not _find_in_workspace(workspace_path, p):
            missing.append(p)
            
    return sorted(missing)
