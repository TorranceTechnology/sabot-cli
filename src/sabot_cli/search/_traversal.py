"""Breadth-first reachability over the link-valued properties of a vault."""

from collections import defaultdict
from collections.abc import Mapping

from sabot_cli.search._model import Direction, EdgeSpec, Hop, Reached, UnknownNoteError
from sabot_cli.vault import Vault


def traverse(
    vault: Vault,
    edges: EdgeSpec,
    start: str,
    direction: Direction,
    depth: int,
    follow: frozenset[str] | None,
) -> tuple[Reached, ...]:
    """The notes reachable from `start` within `depth` hops, each once at its shortest distance.

    `follow` limits the edges by the names they carry in the direction of travel: an edge name
    going out, its inverse name coming in. None follows every edge. The start note is not
    listed and a link to an id with no note is listed with `note` None. When several shortest
    paths lead to a note, `via` is the one whose hops are smallest in (edge name, id) order,
    hop by hop. Results are sorted by distance, then the name of the last hop's edge, then id.
    Raises `UnknownNoteError` when `start` is not a note id.
    """
    if start not in vault.notes:
        raise UnknownNoteError(start)
    adjacency = _adjacency(vault, edges, direction, follow)
    paths: dict[str, tuple[Hop, ...]] = {start: ()}
    frontier = [start]
    for _ in range(depth):
        discovered: list[str] = []
        for node in frontier:
            for hop in adjacency.get(node, ()):
                if hop.target not in paths:
                    paths[hop.target] = (*paths[node], hop)
                    discovered.append(hop.target)
        if not discovered:
            break
        frontier = discovered
    reached = [
        Reached(id=node, note=vault.notes.get(node), distance=len(path), via=path)
        for node, path in paths.items()
        if node != start
    ]
    return tuple(sorted(reached, key=lambda item: (item.distance, item.via[-1].edge, item.id)))


def _adjacency(
    vault: Vault, edges: EdgeSpec, direction: Direction, follow: frozenset[str] | None
) -> Mapping[str, list[Hop]]:
    """Every hop leaving each id, sorted by (edge name, target id) and free of repeats."""
    hops: dict[str, set[Hop]] = defaultdict(set)
    for note in vault.notes.values():
        for link in note.links:
            if link.target is None or link.property not in edges.names:
                continue
            if direction != "in":
                hops[note.id].add(Hop(note.id, link.property, link.target))
            inverse = edges.inverse_of.get(link.property)
            if direction != "out" and inverse is not None:
                hops[link.target].add(Hop(link.target, inverse, note.id))
    return {
        node: sorted(hop for hop in node_hops if follow is None or hop.edge in follow)
        for node, node_hops in hops.items()
    }
