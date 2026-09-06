"""Program regressions; these checks do not claim live model accuracy."""
import asyncio
import copy

import pytest

from bookanalyst.hierarchy import resolve_anchor
from bookanalyst.models import RunCreate
from bookanalyst.semantics import check_math
from bookanalyst.store import WorkflowError, digest
from bookanalyst.tex import inline, render


@pytest.mark.parametrize("prefix", ["(Theorem 2)", "Theorem 2.", "（定理 2）"])
def test_punctuation_cannot_resolve_duplicate_targets(prefix):
    nodes = [{"id": "a", "kind": "theorem", "number": "2", "title": "", "source_prefix": prefix},
             {"id": "b", "kind": "theorem", "number": "2", "title": "", "source_prefix": "Theorem 2."}]
    with pytest.raises(WorkflowError) as error:
        resolve_anchor({"kind": "theorem", "number": "2", "title": ""}, nodes)
    assert error.value.code == "ANCHOR_AMBIGUITY"


@pytest.mark.parametrize("control", ["\x00", "\x03", "\x0c", "\x1f", "\x7f", "\x85"])
def test_unverified_controls_cannot_be_silently_rendered(control):
    for renderer in (inline, check_math):
        with pytest.raises(WorkflowError) as error:
            renderer("x" + control)
        assert error.value.code == "UNRESOLVED_CONTROL_CHARACTER"


@pytest.mark.parametrize("text,prefix", [("Proof. Done.\x03", "Proof."), ("Proof.\x03 Done.", "Proof.\x03")])
def test_proof_renderer_cannot_hide_unknown_character_in_marker_or_prefix(text, prefix):
    atom = {"atom_id": "a", "type": "text", "text": text, "page_idx": 0, "bbox": [0, 0, 1, 1]}
    structure = {"nodes": [{"id": "p", "parent_id": None, "kind": "proof", "atom_ids": ["a"],
                            "title": "", "number": None, "source_prefix": prefix}]}
    profile = {"rules": {"counters": [], "exceptions": []}, "documentclass": "article"}
    with pytest.raises(WorkflowError) as error:
        render([atom], structure, profile, [{"nodes": [{"atom_id": "a", "kind": "source_ref"}]}])
    assert error.value.code == "UNRESOLVED_CONTROL_CHARACTER"
