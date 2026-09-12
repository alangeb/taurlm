"""Direct unit coverage for rlm.pygraph_core (AST knowledge graph).

Pins REAL behavior of build_graph + Graph query methods against a small
4-file fixture package. Every assertion below was derived by actually
running the fixture, not from docstrings -- see NOTE comments where the
actual behavior differs from the docstring wording.

Fixture node ids follow the code format "<file>:<qualified_name>".
Layout (chain.py calls alpha via `from caller import alpha` so the call
edge resolves; a *dotted* call like caller.alpha() would NOT resolve --
see test_dotted_call_is_not_fabricated below):
    base.py    shared()  [god node: 3 callers], alone() [0 callers]
    caller.py  alpha(), beta()  -> both call shared()  (cross-file callers)
    chain.py   from caller import alpha; gamma() -> alpha()  (import chain + caller of alpha)
    lonely.py  unused() -> shared()  (another cross-file caller)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import pytest

from rlm.pygraph_core import build_graph, query_graph


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    root = tmp_path_factory.mktemp("pygraph_fixture")
    (root / "base.py").write_text(
        "def shared(x):\n    return x + 1\n\n"
        "def alone():\n    return 42\n"
    )
    (root / "caller.py").write_text(
        "from base import shared\n\n"
        "def alpha():\n    return shared(2)\n\n"
        "def beta():\n    return shared(3)\n"
    )
    (root / "chain.py").write_text(
        "from caller import alpha\n\n"
        "def gamma():\n    return alpha()\n"
    )
    (root / "lonely.py").write_text(
        "from base import shared\n\n"
        "def unused():\n    return shared(9)\n"
    )
    return build_graph(str(root))


def test_symbols_present(graph):
    ids = {n.id for n in graph.nodes}
    for expected in [
        "base.py:shared",
        "base.py:alone",
        "caller.py:alpha",
        "caller.py:beta",
        "chain.py:gamma",
        "lonely.py:unused",
    ]:
        assert expected in ids, f"missing {expected} in {sorted(ids)}"
    shared = graph.node_by_id("base.py:shared")
    assert shared.kind == "function"
    assert shared.file == "base.py"


def test_callers_shared_cross_file(graph):
    # shared is defined in base.py but called from two other files.
    assert sorted(graph.callers("base.py:shared")) == [
        "caller.py:alpha",
        "caller.py:beta",
        "lonely.py:unused",
    ]


def test_callers_unknown_returns_empty_list_not_crash(graph):
    # Graceful empty list, NOT IndexError/KeyError.
    assert graph.callers("base.py:ghost_symbol") == []
    assert graph.callers("nope.py:whatever") == []


def test_callers_of_uncalled_symbol_is_empty(graph):
    assert graph.callers("base.py:alone") == []


def test_impact_unknown_is_empty_set_not_crash(graph):
    # NOTE: impact() returns a SET (not a list); unknown -> empty set.
    result = graph.impact("does.py:not_here")
    assert isinstance(result, set)
    assert result == set()


def test_impact_includes_direct_callers(graph):
    impacted = graph.impact("base.py:shared")
    assert {"caller.py:alpha", "caller.py:beta", "lonely.py:unused"} <= impacted


def test_impact_of_uncalled_symbol_is_empty(graph):
    assert graph.impact("base.py:alone") == set()


def test_god_nodes_ranks_shared_first(graph):
    top = graph.god_nodes(5)
    assert top[0][0] == "base.py:shared"
    # fan-in count equals number of callers (verified against callers()).
    assert top[0][1] == len(graph.callers("base.py:shared")) == 3


def test_god_nodes_respects_k(graph):
    assert len(graph.god_nodes(1)) == 1
    assert graph.god_nodes(1)[0][0] == "base.py:shared"
    # Only two nodes have callers (shared=3, alpha=1); k larger than the
    # number of caller-having nodes still returns just those two.
    assert len(graph.god_nodes(2)) == 2
    assert graph.god_nodes(100) == graph.god_nodes(2)
    ids = [nid for nid, _ in graph.god_nodes(100)]
    # NOTE: god_nodes only lists nodes WITH callers, so 'alone' (fan-in 0)
    # never appears regardless of k.
    assert "base.py:alone" not in ids
    # k is respected monotonically.
    assert len(graph.god_nodes(1)) <= len(graph.god_nodes(2))


def test_node_by_name_found_and_absent(graph):
    # Real symbol -> non-empty.
    assert graph.node_by_name("shared") != []
    # NOTE footgun (warned about by the code-review skill): node_by_name
    # does a SUBSTRING match, so a substring can match several nodes.
    assert graph.node_by_name("zzz_absent") == []


def test_import_edges_resolved(graph):
    # chain.py imports caller -> import edge between the two __module__ nodes.
    assert "caller.py:__module__" in graph.neighbors("chain.py:__module__")
    assert "caller.py:__module__" in graph.importees("chain.py:__module__")


def test_dotted_call_is_not_fabricated(tmp_path):
    # P7-CT-18: a dotted call caller.alpha() must NOT fabricate an edge
    # to a symbol named 'alpha' (receiver type is unknown). No call edge.
    (tmp_path / "alpha_mod.py").write_text("def alpha():\n    return 1\n")
    (tmp_path / "caller_dotted.py").write_text(
        "import alpha_mod\n\ndef gamma():\n    return alpha_mod.alpha()\n"
    )
    g = build_graph(str(tmp_path))
    assert g.callers("alpha_mod.py:alpha") == []


def test_shortest_path_connected_and_unknown(graph):
    # shared <- alpha -> gamma via call edges => path exists (undirected BFS).
    path = graph.shortest_path("shared", "gamma")
    assert path and path[0].endswith(":shared") and path[-1].endswith(":gamma")
    # Unknown endpoint -> graceful empty list.
    assert graph.shortest_path("nope_zzz", "shared") == []


def test_query_graph_text_wrapper(graph):
    # query_graph is the human-facing wrapper; pin the wrappers stay wired.
    assert "shared" in query_graph(graph, "god 1")
    callers_txt = query_graph(graph, "callers shared")
    assert "called by" in callers_txt
    assert "caller.py:alpha" in callers_txt
    assert "No nodes matching" in query_graph(graph, "callers zzz_absent")


def test_module_level_call_attributed_to_module_scope(tmp_path):
    # Documented: a call at module level (outside any def) is recorded
    # under "<file>:__module__", not dropped.
    (tmp_path / "mod.py").write_text("def shared(x):\n    return x\n")
    (tmp_path / "top.py").write_text(
        "from mod import shared\n\nresult = shared(1)\n"
    )
    g = build_graph(str(tmp_path))
    assert g.callers("mod.py:shared") == ["top.py:__module__"]
