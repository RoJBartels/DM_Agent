from dm_agent.knowledge.extract import (
    Attribute,
    ExtractedEdge,
    ExtractedNode,
    Extraction,
    _slugify,
    dedup,
    node_props,
)


def _node(id_, name, prose="", attrs=None):
    return ExtractedNode(
        id=id_, type="Character", name=name, prose=prose,
        attributes=[Attribute(key=k, value=v) for k, v in (attrs or {}).items()],
    )


def test_slugify():
    assert _slugify("Duke Aldric") == "duke-aldric"
    assert _slugify("  The Order of the Drowned Lantern ") == "the-order-of-the-drowned-lantern"
    assert _slugify("!!!") == "node"


def test_node_props_flattens_attributes():
    n = _node("duke-aldric", "Duke Aldric", attrs={"title": "Duke", "age": "old"})
    assert node_props(n) == {"title": "Duke", "age": "old"}


def test_dedup_merges_same_name_and_remaps_edges():
    ex = Extraction(
        nodes=[
            _node("duke-aldric", "Duke Aldric", prose="short"),
            _node("aldric", "Duke Aldric", prose="a much longer description of the duke"),
            _node("vane-hall", "Vane Hall"),
        ],
        edges=[ExtractedEdge(src="aldric", dst="vane-hall", type="LOCATED_IN")],
    )
    merged = dedup(ex)
    # the two Aldric nodes collapse to one
    ids = {n.id for n in merged.nodes}
    assert ids == {"duke-aldric", "vane-hall"}
    # the surviving node keeps the longer prose
    duke = next(n for n in merged.nodes if n.id == "duke-aldric")
    assert duke.prose == "a much longer description of the duke"
    # the edge endpoint was remapped from the merged-away slug onto the survivor
    assert merged.edges == [ExtractedEdge(src="duke-aldric", dst="vane-hall", type="LOCATED_IN")]


def test_dedup_drops_self_loops_and_dupe_edges():
    ex = Extraction(
        nodes=[_node("a", "Alpha"), _node("a2", "Alpha"), _node("b", "Beta")],
        edges=[
            ExtractedEdge(src="a", dst="a2", type="ENEMY_OF"),  # becomes a self-loop -> dropped
            ExtractedEdge(src="a", dst="b", type="ENEMY_OF"),
            ExtractedEdge(src="a2", dst="b", type="ENEMY_OF"),  # duplicate after remap -> dropped
        ],
    )
    merged = dedup(ex)
    assert merged.edges == [ExtractedEdge(src="a", dst="b", type="ENEMY_OF")]
