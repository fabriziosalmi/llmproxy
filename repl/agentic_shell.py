import subprocess
import logging
import os
import sys
from typing import Dict, Any, Optional
from core.local_assistant import LocalAssistant

logger = logging.getLogger(__name__)

class AgenticShell:
    """An AI-powered shell for debugging and managing the LLMProxy."""
    
    def __init__(self, assistant: LocalAssistant):
        self.assistant = assistant
        self.history = []

    async def handle_input(self, user_input: str) -> str:
        """Processes user input, translating natural language to commands if needed."""
        self.history.append(user_input)
        
        # 1. Check if it's a direct shell command (starts with !)
        if user_input.startswith("!"):
            return self._run_system_command(user_input[1:])
        
        # 2. Check if it's direct python (starts with >)
        if user_input.startswith(">"):
            return self._run_python(user_input[1:])

        # 3. AI Translation (Natural Language)
        translation_prompt = f"""
        Translate this natural language debugging request into a single bash command OR a python snippet.
        Request: {user_input}
        
        Context: You are inside the 'llmproxy' project directory.
        Respond ONLY with the code/command, prefixed with 'SHELL: ' for bash or 'PYTHON: ' for python.
        If you don't know, respond with 'ERROR: I don't know how to do that.'
        """
        
        suggestion = await self.assistant.generate(translation_prompt)
        
        if suggestion.startswith("SHELL: "):
            cmd = suggestion.replace("SHELL: ", "").strip()
            logger.info(f"AI suggests SHELL: {cmd}")
            return self._run_system_command(cmd)
        elif suggestion.startswith("PYTHON: "):
            code = suggestion.replace("PYTHON: ", "").strip()
            logger.info(f"AI suggests PYTHON: {code}")
            return self._run_python(code)
        else:
            return f"AI: {suggestion}"

    def _run_system_command(self, cmd: str) -> str:
        """Executes a system command with basic safety."""
        # Safety: Block destructive commands for demo
        blocked = ["rm -rf /", "mkfs", "dd"]
        if any(b in cmd for b in blocked):
            return "Error: Command blocked for safety."
            
        try:
            result = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, text=True)
            return result
        except subprocess.CalledProcessError as e:
            return f"Error ({e.returncode}): {e.output}"
        except Exception as e:
            return f"Exception: {str(e)}"

    def _run_python(self, code: str) -> str:
        """Executes python code in the current environment."""
        try:
            # We use a limited globals/locals for safety
            local_vars = {}
            exec(code, {}, local_vars)
            return str(local_vars.get('result', 'Executed successfully (no result variable set)'))
        except Exception as e:
            return f"Python Error: {e}"
