"""Interval blocks of direct children on one compatible time axis."""
from dataclasses import dataclass, field
from fractions import Fraction


@dataclass
class Block:
    start: int
    end: int
    members: list[str] = field(default_factory=list)


def shortened(length: int, percent: Fraction) -> int:
    # Integer nanoseconds, round half up once at the selected span.
    value = length * (100 - percent) / 100
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


class Intervals:
    def __init__(self, bounds, children, traversal):
        self.bounds = bounds
        self.traversal = traversal
        self.blocks = {}
        self.uncovered = {}
        self.parent_block = {}
        for node in traversal:
            groups = []
            for child in sorted(children[node], key=lambda c: (*bounds[c], c)):
                start, end = bounds[child]
                if not groups or start >= groups[-1].end:
                    groups.append(Block(start, end))
                group = groups[-1]
                group.end = max(end, group.end)
                group.members.append(child)
            self.blocks[node] = groups
            self.uncovered[node] = self.duration(node) - sum(g.end - g.start for g in groups)
            # Hold the best sibling end once to rank any single change in O(depth).
            for g in groups:
                best = sorted(((bounds[c][1], c) for c in g.members), reverse=True)[:2]
                for c in g.members:
                    other_end = best[0][0] if best[0][1] != c else (best[1][0] if len(best) > 1 else g.start)
                    self.parent_block[c] = (node, g.end, other_end)

    def duration(self, node):
        start, end = self.bounds[node]
        return end - start

    def single_gain(self, node, percent):
        saving = self.duration(node) - shortened(self.duration(node), percent)
        while node in self.parent_block and saving:
            parent, block_end, sibling_end = self.parent_block[node]
            child_end = self.bounds[node][1] - saving
            saving = block_end - max(child_end, sibling_end)
            node = parent
        return saving

    def joint(self, changes):
        projected = {}
        limiters = {}
        inherited = {}
        for node in self.traversal:
            parent = self.parent_block.get(node)
            inherited[node] = changes.get(node, inherited.get(parent[0]) if parent else None)
        for node in reversed(self.traversal):
            total = self.uncovered[node]
            winners = []
            for group in self.blocks[node]:
                ends = [(self.bounds[c][0] + projected[c], c) for c in group.members]
                latest = max(end for end, _ in ends)
                total += latest - group.start
                winners.extend(c for end, c in ends if end == latest)
            reduction = inherited[node]
            if reduction is None:
                projected[node] = total
                limiters[node] = winners
            else:
                # Selecting a whole branch scales its original duration, including its descendants.
                projected[node] = shortened(self.duration(node), reduction)
                limiters[node] = []
        root = self.traversal[0]
        limiting = set()
        stack = list(limiters[root])
        while stack:
            node = stack.pop()
            limiting.add(node)
            stack.extend(limiters[node])
        return projected, limiting
