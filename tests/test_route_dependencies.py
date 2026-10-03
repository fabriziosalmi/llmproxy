"""Each route module uses only the orchestrator members it declares.

proxy/routes/deps.py names, per route module, exactly which orchestrator members
it needs (a Protocol built from small capabilities). mypy checks the body against
that surface and checks the real ProxyOrchestrator provides it; this test closes
the remaining gap, the ones mypy cannot see: a route reading ``agent.<x>`` for an
x its Protocol does not declare fails here, so a new dependency arrives as a diff
in deps.py, in review, instead of as one more attribute in a closure.
"""

import ast
import inspect
import pathlib

import pytest

from proxy.routes import deps

ROUTES = pathlib.Path(deps.__file__).parent
MODULES = sorted(
    p for p in ROUTES.glob("*.py") if p.name not in ("__init__.py", "deps.py")
)


def _declared_members(protocol: type) -> set[str]:
    members: set[str] = set()
    for klass in protocol.__mro__:
        if klass.__module__ != deps.__name__ or klass is object:
            continue
        members.update(getattr(klass, "__annotations__", {}))
        members.update(
            name
            for name, value in vars(klass).items()
            if inspect.isfunction(value) and not name.startswith("__")
        )
    return members


def _router_protocol_name(tree: ast.Module) -> str | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "create_router":
            ann = node.args.args[0].annotation
            return ann.id if isinstance(ann, ast.Name) else None
    return None


def _agent_attributes(tree: ast.Module) -> set[str]:
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "agent"
    }


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.stem)
def test_a_route_module_uses_only_what_its_protocol_declares(path):
    tree = ast.parse(path.read_text())
    name = _router_protocol_name(tree)
    assert name, f"{path.name}: create_router's parameter must be annotated with a Protocol from deps.py"
    protocol = getattr(deps, name, None)
    assert protocol is not None, f"{path.name}: {name} is not defined in deps.py"

    undeclared = _agent_attributes(tree) - _declared_members(protocol)

    assert not undeclared, (
        f"{path.name} reads agent.{sorted(undeclared)} which {name} does not "
        "declare: add the capability to deps.py (and its base list) or stop using it"
    )


def test_every_route_module_has_a_protocol_and_every_protocol_a_module():
    names = {_router_protocol_name(ast.parse(p.read_text())) for p in MODULES}
    protocols = {
        n for n, v in vars(deps).items()
        if inspect.isclass(v) and n.endswith("Agent") and v.__module__ == deps.__name__
    }
    assert names == protocols


def test_no_protocol_declares_a_member_no_route_uses():
    """Dead capabilities are how a declared surface rots into 'everything'."""
    for path in MODULES:
        tree = ast.parse(path.read_text())
        protocol = getattr(deps, _router_protocol_name(tree))
        unused = _declared_members(protocol) - _agent_attributes(tree)
        assert not unused, (
            f"{protocol.__name__} declares {sorted(unused)} that {path.name} never uses"
        )


def test_the_declared_surface_is_much_smaller_than_the_orchestrator():
    """The point of the exercise, as a number: no single module needs most of it."""
    sizes = {
        p.stem: len(_declared_members(getattr(deps, _router_protocol_name(ast.parse(p.read_text())))))
        for p in MODULES
    }
    assert sizes["models"] == 1 and sizes["completions"] <= 4
    assert max(sizes.values()) <= 24
