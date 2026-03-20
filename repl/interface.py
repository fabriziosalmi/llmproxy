import cmd
import asyncio
import threading
from typing import Any

from .agentic_shell import AgenticShell

class LLMProxyREPL(cmd.Cmd):
    intro = 'Welcome to the Agentic LLM Proxy shell. Type help or ? to list commands.\n'
    prompt = '(llmproxy) '

    def __init__(self, store, agents, assistant=None):
        super().__init__()
        self.store = store
        self.agents = agents
        self.ai_shell = AgenticShell(assistant) if assistant else None

    def do_ai(self, arg):
        """AI-powered debugging. Translates NL to bash/python commands."""
        if not self.ai_shell:
            print("Error: AI Assistant not available for this REPL.")
            return
            
        print(f"AI Debugger (input: '{arg}')...")
        # Run async handler in a safe way for cmd.Cmd
        result = asyncio.run(self.ai_shell.handle_input(arg))
        print(f"\n--- AI RESULT ---\n{result}\n-----------------")

    def do_status(self, arg):
        """Show the status of the system and endpoints."""
        try:
            pool = asyncio.run(self.store.get_pool())
            print(f"Verified Endpoints: {len(pool)}")
            for e in pool:
                print(f"- {e.url} [{e.status.name}] {e.latency_ms or 0:.2f}ms")
        except Exception as e:
            print(f"Error fetching status: {e}")

    def do_agents(self, arg):
        """List active agents and their states."""
        for name, agent in self.agents.items():
            print(f"Agent: {name} | Running: True")

    def do_exit(self, arg):
        """Exit the REPL."""
        print("Exiting REPL...")
        return True

    def do_EOF(self, arg):
        return True

def start_repl(store, agents, assistant=None):
    repl = LLMProxyREPL(store, agents, assistant)
    # Run REPL in a separate thread because it's blocking
    thread = threading.Thread(target=repl.cmdloop)
    thread.daemon = True
    thread.start()
