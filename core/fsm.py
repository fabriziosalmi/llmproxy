from typing import Dict, Any, Callable, Optional, List
import logging

logger = logging.getLogger(__name__)

class State:
    def __init__(self, name: str, on_enter: Optional[Callable] = None, on_exit: Optional[Callable] = None):
        self.name = name
        self.on_enter = on_enter
        self.on_exit = on_exit
        self.sub_machine: Optional['StateMachine'] = None

    def set_sub_machine(self, machine: 'StateMachine'):
        self.sub_machine = machine

class StateMachine:
    def __init__(self, name: str, initial_state: str):
        self.name = name
        self.states: Dict[str, State] = {}
        self.current_state_name = initial_state
        self.transitions: Dict[str, Dict[str, str]] = {} # {from_state: {event: to_state}}

    def add_state(self, state: State):
        self.states[state.name] = state

    def add_transition(self, from_state: str, event: str, to_state: str):
        if from_state not in self.transitions:
            self.transitions[from_state] = {}
        self.transitions[from_state][event] = to_state

    async def trigger(self, event: str, *args, **kwargs):
        """Triggers a transition based on an event."""
        # 1. Check if current state has a sub-machine and can handle the event
        current_state = self.states.get(self.current_state_name)
        if current_state and current_state.sub_machine:
            try:
                await current_state.sub_machine.trigger(event, *args, **kwargs)
                return # Event handled by sub-machine
            except Exception:
                pass # Parent machine will try to handle it

        # 2. Check parent machine transition
        if self.current_state_name in self.transitions and event in self.transitions[self.current_state_name]:
            new_state_name = self.transitions[self.current_state_name][event]
            await self.transition_to(new_state_name)
        else:
            raise Exception(f"No transition for event '{event}' in state '{self.current_state_name}' of machine '{self.name}'")

    async def transition_to(self, new_state_name: str):
        old_state = self.states.get(self.current_state_name)
        new_state = self.states.get(new_state_name)

        if not new_state:
            raise Exception(f"State '{new_state_name}' does not exist in machine '{self.name}'")

        if old_state and old_state.on_exit:
            await old_state.on_exit()

        logger.info(f"FSM [{self.name}]: {self.current_state_name} -> {new_state_name}")
        self.current_state_name = new_state_name

        if new_state.on_enter:
            await new_state.on_enter()

    def get_full_state(self) -> str:
        """Returns the full recursive state string."""
        current_state = self.states.get(self.current_state_name)
        if current_state and current_state.sub_machine:
            return f"{self.current_state_name}.{current_state.sub_machine.get_full_state()}"
        return self.current_state_name
