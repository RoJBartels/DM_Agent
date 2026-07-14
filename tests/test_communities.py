from dm_agent.knowledge.communities import detect_communities


def test_empty_graph():
    assert detect_communities([], []) == {}


def test_two_disconnected_triangles_split_into_two_communities():
    nodes = ["a", "b", "c", "x", "y", "z"]
    edges = [
        ("a", "b"), ("b", "c"), ("c", "a"),  # triangle 1
        ("x", "y"), ("y", "z"), ("z", "x"),  # triangle 2
    ]
    comm = detect_communities(nodes, edges)
    assert set(comm) == set(nodes)
    # the two triangles land in different communities
    assert len({comm["a"], comm["b"], comm["c"]}) == 1
    assert len({comm["x"], comm["y"], comm["z"]}) == 1
    assert comm["a"] != comm["x"]


def test_isolated_node_gets_a_community():
    comm = detect_communities(["lonely"], [])
    assert set(comm) == {"lonely"}
    assert isinstance(comm["lonely"], int)
