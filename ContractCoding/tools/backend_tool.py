import os
import re
from typing import Dict, Optional, Tuple

from ContractCoding.tools.process_tool import start_process


def find_backend_entry() -> Dict[str, Optional[str]]:
    """
    Discover a likely backend entrypoint and framework, returning a suggested command and working directory.

    Heuristics:
    - Look for common filenames under typical training/workspace paths
    - Inspect content to detect FastAPI or Flask and app variable name
    - Produce a uvicorn or python command and a working directory

    Returns keys: {"command", "working_directory", "framework", "module", "app_variable"}
    """
    candidates = [
        os.path.join('test', 'workspace', 'main.py'),
        os.path.join('test', 'workspace', 'backend.py'),
        os.path.join('test', 'backend', 'app.py'),
        'main.py', 'app.py', 'api.py', 'server.py'
    ]

    for p in candidates:
        if os.path.exists(p):
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    content = f.read()
                wd = os.path.dirname(p) if os.path.dirname(p) else '.'
                module = os.path.splitext(os.path.basename(p))[0]
                # Detect FastAPI
                if 'FastAPI' in content:
                    m = re.search(r"(\w+)\s*=\s*FastAPI\(", content)
                    app_var = m.group(1) if m else 'app'
                    cmd = f"uvicorn {module}:{app_var} --port 5000 --reload"
                    return {
                        "command": cmd,
                        "working_directory": wd,
                        "framework": "FastAPI",
                        "module": module,
                        "app_variable": app_var,
                    }
                # Detect Flask
                if 'Flask(' in content:
                    cmd = f"python {os.path.basename(p)}"
                    return {
                        "command": cmd,
                        "working_directory": wd,
                        "framework": "Flask",
                        "module": module,
                        "app_variable": "app",
                    }
                # Generic uvicorn fallback
                cmd = f"uvicorn {module}:app --port 5000 --reload"
                return {
                    "command": cmd,
                    "working_directory": wd,
                    "framework": None,
                    "module": module,
                    "app_variable": "app",
                }
            except Exception:
                continue

    return {
        "command": None,
        "working_directory": None,
        "framework": None,
        "module": None,
        "app_variable": None,
    }


def start_backend_auto() -> Dict[str, str]:
    """
    Attempt to discover the backend entrypoint and start it on port 5000.
    Returns a result dict including status, message, and the expected server URL.
    """
    info = find_backend_entry()
    cmd = info.get("command")
    wd = info.get("working_directory")
    if not cmd:
        return {"status": "error", "message": "No backend entrypoint discovered", "server_url": "http://localhost:5000"}

    res = start_process(command=cmd, working_directory=wd)
    if isinstance(res, dict) and res.get("status") == "success":
        return {
            "status": "success",
            "message": f"Started backend: {cmd} in {wd}",
            "server_url": "http://localhost:5000",
            "framework": info.get("framework") or "unknown",
        }
    return {"status": "error", "message": str(res), "server_url": "http://localhost:5000"}


def start_static_preview(search_paths: Optional[list] = None, port: int = 8000) -> Dict[str, str]:
    """
    Start a static preview server (python -m http.server) in a directory containing index.html.
    If search_paths is provided, will check them in order; otherwise try common locations.
    Returns a dict with status and preview_url.
    """
    candidates = search_paths or [
        os.path.join('test', 'static', 'index.html'),
        os.path.join('test', 'workspace', 'frontend', 'index.html'),
        'index.html'
    ]
    for p in candidates:
        if os.path.exists(p):
            wd = os.path.dirname(p) if os.path.dirname(p) else '.'
            res = start_process(command=f"python -m http.server {port}", working_directory=wd)
            if isinstance(res, dict) and res.get("status") == "success":
                return {"status": "success", "preview_url": f"http://localhost:{port}", "directory": wd}
            else:
                return {"status": "error", "message": str(res), "preview_url": ""}

    return {"status": "error", "message": "No index.html found", "preview_url": ""}


# OpenAI Schemas
find_backend_entry.openai_schema = {
    "type": "function",
    "function": {
        "name": "find_backend_entry",
        "description": "Discover a likely backend entrypoint and framework, returning a suggested command and working directory.",
        "parameters": {"type": "object", "properties": {}, "required": []}
    }
}

start_backend_auto.openai_schema = {
    "type": "function",
    "function": {
        "name": "start_backend_auto",
        "description": "Discover and start the backend service on port 5000 using uvicorn or python app.py.",
        "parameters": {"type": "object", "properties": {}, "required": []}
    }
}

start_static_preview.openai_schema = {
    "type": "function",
    "function": {
        "name": "start_static_preview",
        "description": "Start a static preview server (python -m http.server) in a directory containing index.html.",
        "parameters": {
            "type": "object",
            "properties": {
                "search_paths": {"type": "array", "items": {"type": "string"}, "description": "Candidate index.html paths to try in order."},
                "port": {"type": "integer", "description": "Port for http.server", "default": 8000}
            },
            "required": []
        }
    }
}
