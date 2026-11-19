"""Checks on edges: well-formedness, direction, symmetry and the pairs that cannot coexist."""

from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence

from sabot_cli.rules._checks._context import Edge, LintContext
from sabot_cli.rules._checks._model import Report
from sabot_cli.rules.schema import ANY

_CONFLICTS_WITH = "conflicts-with"
_RESTATES = "restates"
_OVERRIDES = "overrides"
_EXCEPTION_TO = "exception-to"
_OPEN_QUESTION = "open-question"


def _check_link_malformed(ctx: LintContext) -> Iterator[Report]:
    for note in ctx.notes:
        for link in note.links:
            if link.target is None:
                yield ctx.report(
                    note, f"{link.property} holds {link.raw!r}, which is not a quoted wikilink"
                )


def _check_link_dangling(ctx: LintContext) -> Iterator[Report]:
    for note in ctx.notes:
        for link in note.links:
            if link.target is not None and link.target not in ctx.vault.notes:
                yield ctx.report(
                    note, f"{link.property} points at the missing note {link.target!r}"
                )


def _check_edge_domain(ctx: LintContext) -> Iterator[Report]:
    for edge in ctx.authored_edges:
        allowed = ctx.schema.edge_types[edge.name].domain
        names = ctx.type_names(edge.source)
        if ANY not in allowed and names and not names & set(allowed):
            yield ctx.report(
                edge.source,
                f"{edge.name} cannot leave a {sorted(names)[0]} note; "
                f"it leaves: {', '.join(allowed)}",
            )


def _check_edge_range(ctx: LintContext) -> Iterator[Report]:
    for edge in ctx.authored_edges:
        allowed = ctx.schema.edge_types[edge.name].range
        names = ctx.target_type_names(edge.target)
        if ANY not in allowed and names and not names & set(allowed):
            yield ctx.report(
                edge.source,
                f"{edge.name} cannot reach {edge.target}, a {sorted(names)[0]} note; "
                f"it reaches: {', '.join(allowed)}",
            )


def _check_inverse_authored(ctx: LintContext) -> Iterator[Report]:
    edge_of_inverse = {
        edge.inverse: edge.name
        for edge in ctx.schema.edge_types.values()
        if edge.inverse not in ctx.schema.edge_types
    }
    for note in ctx.notes:
        for link in note.links:
            if link.property in edge_of_inverse:
                yield ctx.report(
                    note,
                    f"{link.property} is computed, never authored; write "
                    f"{edge_of_inverse[link.property]} on the other note",
                )


def _check_symmetric_duplicated(ctx: LintContext) -> Iterator[Report]:
    authored = {(edge.name, edge.source.id, edge.target) for edge in ctx.authored_edges}
    for edge in ctx.authored_edges:
        if (
            ctx.schema.edge_types[edge.name].symmetric
            and edge.source.id != edge.target
            and (edge.name, edge.target, edge.source.id) in authored
        ):
            yield ctx.report(
                edge.source,
                f"{edge.name} is authored on both ends with {edge.target}; keep it on one",
            )


def _check_precedes_cycle(ctx: LintContext) -> Iterator[Report]:
    for name in sorted(ctx.schema.edge_types):
        if not ctx.schema.edge_types[name].acyclic:
            continue
        graph: dict[str, list[str]] = defaultdict(list)
        for edge in ctx.authored_edges:
            if edge.name == name and edge.target in ctx.vault.notes:
                graph[edge.source.id].append(edge.target)
        for members in _cycles(graph):
            for member in members:
                yield ctx.report(
                    ctx.vault.notes[member], f"{name} forms a cycle through {', '.join(members)}"
                )


def _cycles(graph: Mapping[str, Sequence[str]]) -> list[tuple[str, ...]]:
    """The strongly connected components that contain a cycle, members sorted, Tarjan's method.

    The walk is iterative so a long chain cannot exhaust the interpreter's recursion limit.
    """
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    found: list[tuple[str, ...]] = []
    counter = 0
    for root in sorted(graph):
        if root in index:
            continue
        work: list[tuple[str, int]] = [(root, 0)]
        while work:
            node, child = work.pop()
            if child == 0:
                index[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            neighbours = sorted(graph.get(node, ()))
            descended = False
            for position in range(child, len(neighbours)):
                neighbour = neighbours[position]
                if neighbour not in index:
                    work.append((node, position + 1))
                    work.append((neighbour, 0))
                    descended = True
                    break
                if neighbour in on_stack:
                    low[node] = min(low[node], index[neighbour])
            if descended:
                continue
            if low[node] == index[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1 or node in graph.get(node, ()):
                    found.append(tuple(sorted(component)))
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
    return sorted(found)


def _spans_cited(ctx: LintContext, note_id: str) -> frozenset[tuple[str, str]]:
    return frozenset(
        (citation.ref.source, citation.ref.locator)
        for citation in ctx.citations(ctx.vault.notes[note_id])
        if citation.ref is not None
    )


def _pairs(edges: Sequence[Edge], name: str) -> set[frozenset[str]]:
    return {frozenset((edge.source.id, edge.target)) for edge in edges if edge.name == name}


def _check_conflict_without_question(ctx: LintContext) -> Iterator[Report]:
    questions = [
        _spans_cited(ctx, note.id) for note in ctx.notes if ctx.known_type(note) == _OPEN_QUESTION
    ]
    for pair in sorted(_pairs(ctx.authored_edges, _CONFLICTS_WITH), key=sorted):
        if any(note_id not in ctx.vault.notes for note_id in pair):
            continue
        cited = [_spans_cited(ctx, note_id) for note_id in sorted(pair)]
        if any(all(question & spans for spans in cited) for question in questions):
            continue
        for note_id in sorted(pair):
            others = ", ".join(sorted(pair - {note_id})) or note_id
            yield ctx.report(
                ctx.vault.notes[note_id],
                f"{_CONFLICTS_WITH} {others} is recorded by no open question citing both spans",
            )


def _check_restates_conflicts_pair(ctx: LintContext) -> Iterator[Report]:
    both = _pairs(ctx.authored_edges, _RESTATES) & _pairs(ctx.authored_edges, _CONFLICTS_WITH)
    for pair in sorted(both, key=sorted):
        for note_id in sorted(pair):
            others = ", ".join(sorted(pair - {note_id})) or note_id
            yield ctx.report(
                ctx.vault.notes[note_id],
                f"{_RESTATES} and {_CONFLICTS_WITH} both join this note to {others}",
            )


def _check_overrides_and_exception(ctx: LintContext) -> Iterator[Report]:
    both = _pairs(ctx.authored_edges, _OVERRIDES) & _pairs(ctx.authored_edges, _EXCEPTION_TO)
    for pair in sorted(both, key=sorted):
        authors = {
            edge.source.id
            for edge in ctx.authored_edges
            if edge.name in (_OVERRIDES, _EXCEPTION_TO)
            and frozenset((edge.source.id, edge.target)) == pair
        }
        for author in sorted(authors):
            others = ", ".join(sorted(pair - {author})) or author
            yield ctx.report(
                ctx.vault.notes[author],
                f"{_OVERRIDES} and {_EXCEPTION_TO} both join this note to {others}",
            )
