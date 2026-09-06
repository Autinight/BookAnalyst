import asyncio
import copy
from collections import Counter

import pytest

from bookanalyst.hierarchy import merge_local, resolve_anchor
from bookanalyst.models import RunCreate
from bookanalyst.semantics import validate_structure
from bookanalyst.store import WorkflowError, digest

BOOK_ID = "elliptic-pde-second-order"


def test_local_continuation_cannot_target_a_nonadjacent_node():
    nodes = [
        {"id": "first", "parent_id": None, "kind": "paragraph", "atom_ids": ["a"], "title": "", "number": None},
        {"id": "last", "parent_id": None, "kind": "paragraph", "atom_ids": ["b"], "title": "", "number": None}]
    candidate = {"findings": [], "nodes": [], "toc": [], "references": [], "open_path": [],
                 "continuations": [{"node_id": "first", "atom_ids": ["c"], "evidence": "Unjustified distant continuation"}]}
    with pytest.raises(WorkflowError) as error:
        merge_local(candidate, [{"atom_id": "c"}], nodes, [])
    assert error.value.code == "INVALID_CONTINUATION"
    assert nodes[0]["atom_ids"] == ["a"]


def test_structure_cannot_reopen_an_already_closed_proof():
    nodes = [
        {"id": "chapter", "parent_id": None, "kind": "chapter", "atom_ids": ["a"], "title": "Chapter", "number": "1"},
        {"id": "proof", "parent_id": "chapter", "kind": "proof", "atom_ids": ["b"], "title": "", "number": None},
        {"id": "after", "parent_id": "chapter", "kind": "paragraph", "atom_ids": ["c"], "title": "", "number": None},
        {"id": "wrong", "parent_id": "proof", "kind": "paragraph", "atom_ids": ["d"], "title": "", "number": None}]
    plan = {"nodes": nodes, "findings": [], "toc": [], "toc_mode": "body_reconstructed", "references": []}
    with pytest.raises(WorkflowError) as error:
        validate_structure(plan, [{"atom_id": aid} for aid in "abcd"])
    assert error.value.code == "READING_ORDER"


def test_ambiguous_book_anchor_blocks_instead_of_choosing_first():
    nodes = [{"id": "one", "kind": "equation", "number": "1", "title": ""},
             {"id": "two", "kind": "equation", "number": "1", "title": ""}]
    with pytest.raises(WorkflowError) as error:
        resolve_anchor({"kind": "equation", "number": "1", "title": ""}, nodes)
    assert error.value.code == "ANCHOR_AMBIGUITY"
