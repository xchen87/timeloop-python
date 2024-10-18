import copy
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from util import fzs

UNEVEN_FUSION_ALLOWED = False


class Loop:
    def __init__(self, rank_id: str, bound: int, is_spatial: bool):
        self.rank_id = rank_id
        self.bound = bound
        self.is_spatial = is_spatial

    def __eq__(self, other):
        return (
            self.rank_id == other.rank_id
            and self.bound == other.bound
            and self.is_spatial == other.is_spatial
        )

    def __lt__(self, other):
        return (
            self.rank_id < other.rank_id
            or self.bound < other.bound
            or self.is_spatial < other.is_spatial
        )

    def __hash__(self):
        return hash((self.rank_id, self.bound, self.is_spatial))

    def subtiles(self, other: "Loop") -> bool:
        if self.rank_id != other.rank_id:
            return False
        return other.bound % self.bound == 0

    def __repr__(self):
        return ("S-" if self.is_spatial else "") + f"{self.rank_id}-{self.bound}"

    def __str__(self):
        return ("S-" if self.is_spatial else "") + f"{self.rank_id}-{self.bound}"


@dataclass(frozen=True)
class TensorTiling:
    backer: str
    loops_above_backer: tuple[Loop, ...]

    def __eq__(self, other):
        return (
            self.backer == other.backer
            and self.loops_above_backer == other.loops_above_backer
        )

    def __lt__(self, other):
        return (
            self.backer < other.backer
            or self.loops_above_backer < other.loops_above_backer
        )

    def __hash__(self):
        return hash((self.backer, self.loops_above_backer))

    def co_tiled_with(self, other: "TensortensorTiling") -> bool:
        return any(not f.is_spatial for f in self.loops_above_backer)

    def __repr__(self):
        return f"{self.backer} {' '.join(str(l) for l in self.loops_above_backer)}"


@dataclass(frozen=True)
class OpCompatibility:
    # Fusion information
    tiling: dict[str, TensorTiling]
    einsum_id: str

    @cached_property
    def tiling_tupled(self):
        return tuple(self.tiling[t] for t in sorted(self.tensors))

    @cached_property
    def tensors(self):
        return fzs(self.tiling.keys())

    def iter_matched_tilings(self, other: "OpCompatibility") -> tuple[tuple[str, str]]:
        for t in self.tensors & other.tensors:
            yield t, self.tiling[t], other.tiling[t]

    def compatible_with(self, other: "OpCompatibility") -> bool:
        if any(t1 != t2 for _, t1, t2 in self.iter_matched_tilings(other)):
            return False
        return True

    def co_tiled_with(self, other: "OpCompatibility") -> bool:
        return any(
            t1.co_tiled_with(t2) for _, t1, t2 in self.iter_matched_tilings(other)
        )

    @staticmethod
    def get_co_tiled(
        compats: set["OpCompatibility"], live_tensors: set[str] = fzs()
    ) -> set["OpCompatibility"]:
        # Live are:
        # - Has a live tensor
        # - Co-tiled with a live Einsum
        live = set()
        to_check = [c for c in compats if c.tensors & live_tensors]
        while to_check:
            c = to_check.pop()
            live.add(c)
            to_check.extend(
                c2
                for c2 in compats
                if c2 not in live
                and c2 not in to_check
                and any(
                    t1.co_tiled_with(t2) for _, t1, t2 in c.iter_matched_tilings(c2)
                )
            )
        return live

    @staticmethod
    def vertical_combine(
        compats: set["OpCompatibility"], live_tensors: set[str] = fzs()
    ) -> set["OpCompatibility"]:
        tiling = {}
        for c in compats:
            for t, tt in c.tiling.items():
                if t in tiling:
                    assert tt == tiling[t], "Mismatched tilings"
                tiling[t] = tt
        return OpCompatibility(einsum_id="", tiling=tiling)

    def __eq__(self, other):
        return (
            self.einsum_id == other.einsum_id
            and self.tiling_tupled == other.tiling_tupled
        )

    def __lt__(self, other):
        return self.tiling_tupled < other.tiling_tupled

    def __str__(self):
        f = []
        for t in sorted(self.tensors):
            f.append(f"{t}({self.tiling[t]})")
        return self.einsum_id + ":" + ",".join(f)

    def __repr__(self):
        return f"OpCompatibility({self.einsum_id}, {self.tiling})"

    def __hash__(self):
        return hash((self.einsum_id, self.tiling_tupled))

    def drop_dead(self, live_tensors: set[str]) -> "OpCompatibility":
        return OpCompatibility(
            einsum_id=self.einsum_id,
            tiling={t: self.tiling[t] for t in self.tensors & live_tensors},
        )


@dataclass(frozen=True)
class SharedResource:
    resource_id: str
    loops_below: tuple[Loop, ...]
    data: frozenset[tuple[str, float]]


import unittest


class TestOpCompatibility(unittest.TestCase):
    def test_compatible_with(self):
        loopnests = [
            "A1 B2 C3 D4",
            "A1",
            "A1 B2 C3 D8",
            "A1 B2 C6",
            "A1 B2 C5",
            "B2 A1",
            "Q1 A1 B2 C3 D4",
            "",
            "A1 B2 C3 D4",
        ]

        if UNEVEN_FUSION_ALLOWED:
            compatibilities = [
                (1, 1, 1, 0, 0, 0, 0, 1, 1),
                (1, 1, 1, 1, 1, 0, 0, 1, 1),
                (1, 1, 1, 0, 0, 0, 0, 1, 1),
                (0, 1, 0, 1, 0, 0, 0, 1, 0),
                (0, 1, 0, 0, 1, 0, 0, 1, 0),
                (0, 0, 0, 0, 0, 1, 0, 1, 0),
                (0, 0, 0, 0, 0, 0, 1, 1, 0),
                (1, 1, 1, 1, 1, 1, 1, 1, 1),
                (1, 1, 1, 0, 0, 0, 0, 1, 1),
            ]
        else:  # No uneven fusion
            compatibilities = [
                (1, 0, 0, 0, 0, 0, 0, 0, 1),
                (0, 1, 0, 0, 0, 0, 0, 0, 0),
                (0, 0, 1, 0, 0, 0, 0, 0, 0),
                (0, 0, 0, 1, 0, 0, 0, 0, 0),
                (0, 0, 0, 0, 1, 0, 0, 0, 0),
                (0, 0, 0, 0, 0, 1, 0, 0, 0),
                (0, 0, 0, 0, 0, 0, 1, 0, 0),
                (0, 0, 0, 0, 0, 0, 0, 1, 0),
                (1, 0, 0, 0, 0, 0, 0, 0, 1),
            ]

        comps = []
        for i, l in enumerate(loopnests):
            comps.append(
                OpCompatibility(
                    einsum_id=l,
                    tiling={
                        "T1": TensorTiling(
                            "GLB",
                            tuple(
                                Loop(r[0], int(r[1]), False) for r in l.split(" ") if r
                            ),
                        )
                    },
                )
            )

        for i, c1 in enumerate(comps):
            for j, c2 in enumerate(comps):
                e = bool(compatibilities[i][j])
                self.assertEqual(
                    c1.compatible_with(c2),
                    e,
                    f"{c1.einsum_id} <-> {c2.einsum_id} compatible got {not e}, expected {e}",
                )

    # def test_get_tiled_partitions(self):
    #     loopnests = [
    #         "A1 B2 C3 D4",
    #         "A1",
    #         "",
    #         "A1 B2 C3 D4",
    #     ]
    #     comps = []
    #     for i, l in enumerate(loopnests):
    #         comps.append(
    #             OpCompatibility(
    #                 einsum_id=l,
    #                 # fused_tensors=fzs(["T1"]),
    #                 # fused_loops=tuple(
    #                 #     Loop(r[0], int(r[1]), False) for r in l.split(" ") if r
    #                 # ),
    #                 tiling={
    #                     "T1": TensorTiling(
    #                         "GLB",
    #                         tuple(
    #                             Loop(r[0], int(r[1]), False) for r in l.split(" ") if r
    #                         ),
    #                     )
    #                 },
    #                 ranks=fzs("ABCD"),
    #                 tensors=fzs(["T1"]),
    #                 neighbors=fzs(),
    #             )
    #         )

    #     partitions = OpCompatibility.get_tiled_partitions(set(comps))
    #     self.assertEqual(len(partitions), 3)


if __name__ == "__main__":
    l = Loop("A", 1, False)
    hash(l)
    unittest.main()
