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


@pytest.mark.parametrize("suffix", [False, True])
@pytest.mark.parametrize("source_has_spaces", [False, True])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_line_end_spaces_align_using_original_source_offsets(suffix, source_has_spaces, newline):
    # seam-0004 copied two cases rows with extra spaces after the TeX line break.
    exact = (
        "and\n\\[\n\\bar{J}(r)=\\begin{cases}\n"
        "r^{m-1} & K=0,\\\\\n"
        "\\sinh^{m-1}(r) & K<0.\\\\\n"
        "\\end{cases}\n\\]\n"
    ).replace("\n", newline)
    spaced = exact.replace("\\\\" + newline, "\\\\ \t" + newline)
    source, anchor = (spaced, exact) if source_has_spaces else (exact, spaced)
    if suffix:
        left, right = "untouched left\n" + source, "untouched right"
        patch = seam(left_suffix=anchor, replacement=exact)
        expected = ("untouched left\n" + exact, right)
    else:
        left, right = "untouched left\n", source + "untouched right"
        patch = seam(right_prefix=anchor, replacement=exact)
        expected = (left + exact, "untouched right")
    assert apply_seam(left, right, patch) == expected


@pytest.mark.parametrize("suffix", [False, True])
@pytest.mark.parametrize("original,copied", [
    ("two words", "twowords"),
    ("x + y", "x - y"),
    ("first\n\nsecond", "first\nsecond"),
    (r"\label{lemma:2.7}", r"\label{lemma:2.8}"),
])
def test_whitespace_fallback_rejects_internal_content_changes(suffix, original, copied):
    source = "unchanged beginning\n" + original + "\nunchanged ending"
    anchor = "unchanged beginning \n" + copied + "\nunchanged ending"
    patch = seam(**{"left_suffix" if suffix else "right_prefix": anchor}, replacement=anchor)
    with pytest.raises(WorkflowError) as error:
        apply_seam(source if suffix else "left", "right" if suffix else source, patch)
    assert error.value.code == "STALE_PATCH"


def test_marker_conservation_survives_whitespace_fallback():
    source = "first line\n\\label{lemma:2.7}\nlast line\n"
    anchor = source.replace("line\n", "line \n")
    with pytest.raises(WorkflowError) as error:
        apply_seam("left", source, seam(right_prefix=anchor, replacement="kept"))
    assert error.value.code == "ANCHOR_CHANGE"


@pytest.mark.parametrize("suffix", [False, True])
def test_mismatch_feedback_shows_actual_difference_beyond_edge_preview(suffix):
    source = "same start " * 20 + "ORIGINAL" + " same end" * 20
    anchor = source.replace("ORIGINAL", "CHANGED")
    patch = seam(**{"left_suffix" if suffix else "right_prefix": anchor}, replacement=anchor)
    with pytest.raises(WorkflowError) as error:
        apply_seam(source if suffix else "left", "right" if suffix else source, patch)
    assert "ORIGINAL" in error.value.message
    assert "CHANGED" in error.value.message
    assert "replacement" in error.value.message


def test_feedback_distinguishes_newline_from_latex_command():
    source = "Choose a collection\n\\[\nu_{x_i}(x) = 1\n\\]"
    anchor = source.replace("\\[\nu", r"\[\nu")
    with pytest.raises(WorkflowError) as error:
        apply_seam("left", source, seam(right_prefix=anchor, replacement=anchor))
    message = error.value.message
    assert "内部内容不一致" in message
    assert f"原文：偏移 {source.index(chr(10), source.index('['))}" in message
    assert "字符 〈换行 U+000A〉" in message
    assert "字符 〈反斜杠 U+005C〉" in message
    assert "〈反斜杠〉[〈换行〉u_" in message
    assert "〈反斜杠〉[〈反斜杠〉nu_" in message


@pytest.mark.parametrize("suffix", [False, True])
def test_feedback_skips_tolerated_spaces_and_reports_original_positions(suffix):
    source = "😀 first\nleft x + y right\nlast\n"
    anchor = source.replace("first\n", "first \t\n").replace("+", "-").replace("last\n", "last \t\n")
    with pytest.raises(WorkflowError) as error:
        apply_seam(source if suffix else "left", "right" if suffix else source,
                   seam(**{"left_suffix" if suffix else "right_prefix": anchor}, replacement=anchor))
    message = error.value.message
    assert f"原文：偏移 {source.index('+')}，第 2 行第 8 列，字符 〈+ U+002B〉" in message
    assert f"候选：偏移 {anchor.index('-')}，第 2 行第 8 列，字符 〈- U+002D〉" in message


@pytest.mark.parametrize("suffix", [False, True])
def test_feedback_identifies_existing_anchor_away_from_edge(suffix):
    anchor = "exact anchor text"
    source = anchor + "x" * 12 if suffix else "x" * 12 + anchor
    with pytest.raises(WorkflowError) as error:
        apply_seam(source if suffix else "left", "right" if suffix else source,
                   seam(**{"left_suffix" if suffix else "right_prefix": anchor}, replacement=anchor))
    assert "片段内容存在，但未对齐" in error.value.message
    assert "距该边缘 12 个字符" in error.value.message


@pytest.mark.parametrize("suffix", [False, True])
def test_feedback_reports_exhausted_source_without_inventing_character(suffix):
    source = "short"
    anchor = "extra" + source if suffix else source + "extra"
    with pytest.raises(WorkflowError) as error:
        apply_seam(source if suffix else "left", "right" if suffix else source,
                   seam(**{"left_suffix" if suffix else "right_prefix": anchor}, replacement=anchor))
    assert ("〈字符串起点之前〉" if suffix else "〈字符串结束〉") in error.value.message
