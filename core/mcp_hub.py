import logging
import json
import os
import subprocess
from typing import Dict, Any, List, Callable, Optional

logger = logging.getLogger(__name__)

class MCPHub:
    """A Model Context Protocol Hub for exposing local tools to proxied LLMs."""
    
    def __init__(self):
        self.tools: Dict[str, Dict[str, Any]] = {}
        self._register_default_tools()

    def register_tool(self, name: str, description: str, parameters: Dict[str, Any], func: Callable):
        """Registers a new tool in the hub."""
        self.tools[name] = {
            "definition": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters
                }
            },
            "handler": func
        }
        logger.info(f"MCPHub: Registered tool '{name}'")

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Returns the tool definitions in OpenAI-compatible format."""
        return [t["definition"] for t in self.tools.values()]

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        """Executes a tool and returns the result as a string."""
        if name not in self.tools:
            return f"Error: Tool '{name}' not found."
        
        try:
            handler = self.tools[name]["handler"]
            result = await handler(**arguments)
            return json.dumps(result) if not isinstance(result, str) else result
        except Exception as e:
            logger.error(f"MCPHub: Error calling tool '{name}': {e}")
            return f"Error executing tool '{name}': {str(e)}"

    def _register_default_tools(self):
        """Registers a set of useful default tools."""
        
        # 1. File Reader
        async def read_local_file(path: str):
            if not os.path.exists(path): return f"Error: File not found at {path}"
            # Security: Prevent reading outside project dir (simplified)
            if ".." in path or path.startswith("/"): return "Error: Access denied."
            with open(path, "r") as f:
                return f.read(5000) # Cap at 5k chars

        self.register_tool(
            "read_local_file",
            "Reads a local file from the project directory.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to the file."}
                },
                "required": ["path"]
            },
            read_local_file
        )

        # 2. Directory Lister
        async def list_local_dir(path: str = "."):
            if not os.path.exists(path): return f"Error: Directory not found at {path}"
            return os.listdir(path)

        self.register_tool(
            "list_local_dir",
            "Lists contents of a local directory.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to the directory.", "default": "."}
                }
            },
            list_local_dir
        )

        # 3. System Info
        async def get_system_info():
            import platform
            return {
                "system": platform.system(),
                "node": platform.node(),
                "release": platform.release(),
                "cwd": os.getcwd()
            }

        self.register_tool(
            "get_system_info",
            "Returns basic information about the local host environment.",
            {"type": "object", "properties": {}},
            get_system_info
        )

        # 4. SQL Query (Local DB)
        async def query_local_db(query: str):
            import sqlite3
            try:
                conn = sqlite3.connect("endpoints.db")
                cursor = conn.cursor()
                cursor.execute(query)
                rows = cursor.fetchall()
                columns = [description[0] for description in cursor.description]
                conn.close()
                return [dict(zip(columns, row)) for row in rows][:20] # Cap at 20 rows
            except Exception as e:
                return f"Database Error: {e}"

        self.register_tool(
            "query_local_db",
            "Executes a read-only SQL query on the local endpoints database.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The SQL select query to execute."}
                },
                "required": ["query"]
            },
            query_local_db
        )
