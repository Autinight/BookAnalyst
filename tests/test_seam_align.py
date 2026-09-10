"""Seam boundaries tolerate short-range copying drift, nothing more."""

import pytest

from bookanalyst.documents import apply_seam
from bookanalyst.store import WorkflowError

LEFT = (
    "Indeed, considering the parametrized surface\n"
    "\\[\n"
    "f(t,s)=\\exp_p tv(s),\\qquad t\\in[0,\\delta],\\quad s\\in(-\\varepsilon,\\varepsilon),\n"
    "\\]"
)
RIGHT = (
    "where $\\delta$ is chosen so small that $\\exp_p tv(s)$ is defined.\n\n"
    "\\BAHeading{batch-0027-conjugate-points}\\label{section:3:ch5}\n"
    "Now we turn to the relationship.\n"
)
TAIL = LEFT[-40:]
HEAD = RIGHT[:30]


def seam(**kwargs):
    return dict(left_suffix="", right_prefix="", replacement="") | kwargs


def test_exact_boundaries_pass_unchanged():
    assert apply_seam(LEFT, RIGHT, seam(left_suffix=TAIL, replacement=TAIL)) == (LEFT, RIGHT)
    assert apply_seam(LEFT, RIGHT, seam(right_prefix=HEAD, replacement=HEAD)) == (LEFT + HEAD, RIGHT[len(HEAD):])


def test_trailing_extra_characters_are_trimmed():
    patch = seam(left_suffix=TAIL + "XYZ", replacement=TAIL)
    assert apply_seam(LEFT, RIGHT, patch) == (LEFT, RIGHT)


def test_short_tail_is_extended_to_the_true_end():
    patch = seam(left_suffix=TAIL[:-2], replacement=TAIL)
    assert apply_seam(LEFT, RIGHT, patch) == (LEFT, RIGHT)


def test_anchor_far_from_the_end_is_rejected():
    far = LEFT[8:28]
    with pytest.raises(WorkflowError) as error:
        apply_seam(LEFT, RIGHT, seam(left_suffix=far, replacement=far))
    assert error.value.code == "STALE_PATCH"
    assert "left_suffix" in error.value.message
    assert far[:10] in error.value.message


def test_absent_snippet_is_rejected():
    with pytest.raises(WorkflowError) as error:
        apply_seam(LEFT, RIGHT, seam(left_suffix="absent snippet here", replacement="absent snippet here"))
    assert error.value.code == "STALE_PATCH"


def test_right_boundary_missing_head_is_completed():
    patch = seam(right_prefix=RIGHT[2:30], replacement=HEAD)
    assert apply_seam(LEFT, RIGHT, patch) == (LEFT + HEAD, RIGHT[len(HEAD):])


def test_empty_seam_is_a_noop():
    assert apply_seam(LEFT, RIGHT, seam()) == (LEFT, RIGHT)
    with pytest.raises(WorkflowError) as error:
        apply_seam(LEFT, RIGHT, seam(replacement="added"))
    assert error.value.code == "INVALID_PATCH"


def test_marker_conservation_survives_alignment():
    left = "text \\label{lemma:2.7}\n"
    with pytest.raises(WorkflowError) as error:
        apply_seam(left, "right", seam(left_suffix="\\label{lemma:2.7}\n", replacement="kept"))
    assert error.value.code == "ANCHOR_CHANGE"
