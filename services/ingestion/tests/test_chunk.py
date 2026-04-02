from __future__ import annotations

import pytest

from common.config import Settings
from ingestion.chunk import (
    Line,
    SegmentedSentence,
    _build_reading_text,
    assemble_chunks,
    order_lines_reading_order,
    segment_page_sentences,
)


@pytest.fixture
def settings() -> Settings:
    return Settings()


# -- reading order --------------------------------------------------------------------------


def test_order_lines_reading_order_reads_left_column_then_right_column() -> None:
    # Interleaved input order (as a naive top-to-bottom sort across the whole page would
    # produce for a two-column layout) must come out grouped by column, left before right.
    lines = [
        Line(text="right-1", rect=(322.0, 110.0, 500.0, 120.0)),
        Line(text="left-1", rect=(72.0, 110.0, 260.0, 120.0)),
        Line(text="left-2", rect=(72.0, 130.0, 260.0, 140.0)),
        Line(text="right-2", rect=(322.0, 130.0, 500.0, 140.0)),
    ]

    ordered = order_lines_reading_order(lines)

    assert [line.text for line in ordered] == ["left-1", "left-2", "right-1", "right-2"]


def test_order_lines_reading_order_sorts_single_column_top_to_bottom() -> None:
    lines = [
        Line(text="third", rect=(72.0, 300.0, 500.0, 310.0)),
        Line(text="first", rect=(72.0, 100.0, 500.0, 110.0)),
        Line(text="second", rect=(72.0, 200.0, 500.0, 210.0)),
    ]

    ordered = order_lines_reading_order(lines)

    assert [line.text for line in ordered] == ["first", "second", "third"]


def test_order_lines_reading_order_handles_empty_input() -> None:
    assert order_lines_reading_order([]) == []


# -- hyphenation healing ------------------------------------------------------------------------


def test_build_reading_text_heals_a_hyphenated_line_break() -> None:
    lines = [
        Line(text="This is compre-", rect=(0.0, 0.0, 100.0, 10.0)),
        Line(text="hensive text.", rect=(0.0, 12.0, 100.0, 22.0)),
    ]

    joined, source = _build_reading_text(lines)

    assert joined == "This is comprehensive text."
    assert len(source) == len(joined)


def test_build_reading_text_joins_normal_lines_with_a_space() -> None:
    lines = [
        Line(text="First line.", rect=(0.0, 0.0, 100.0, 10.0)),
        Line(text="Second line.", rect=(0.0, 12.0, 100.0, 22.0)),
    ]

    joined, _ = _build_reading_text(lines)

    assert joined == "First line. Second line."


def test_build_reading_text_does_not_heal_a_trailing_hyphen_on_the_last_line() -> None:
    lines = [Line(text="Ends with a hyphen-", rect=(0.0, 0.0, 100.0, 10.0))]

    joined, _ = _build_reading_text(lines)

    assert joined == "Ends with a hyphen-"


# -- segmentation + rect clipping ----------------------------------------------------------------


def test_segment_page_sentences_splits_on_sentence_boundaries() -> None:
    lines = [
        Line(
            text="The facility achieved 94% uptime in Q3. This was attributable to cooling.",
            rect=(72.0, 140.0, 500.0, 155.0),
        )
    ]

    sentences = segment_page_sentences(lines, sentence_max_chars=1000)

    assert [s.text for s in sentences] == [
        "The facility achieved 94% uptime in Q3.",
        "This was attributable to cooling.",
    ]


def test_segment_page_sentences_never_produces_empty_sentences() -> None:
    lines = [Line(text="One. Two.  Three.", rect=(72.0, 140.0, 500.0, 155.0))]
    sentences = segment_page_sentences(lines, sentence_max_chars=1000)
    assert all(s.text.strip() for s in sentences)


def test_segment_page_sentences_returns_empty_for_no_lines() -> None:
    assert segment_page_sentences([], sentence_max_chars=1000) == []


def test_segment_page_sentences_clips_mid_line_sentence_narrower_than_the_full_line() -> None:
    line_rect = (72.0, 140.0, 539.71, 155.11)
    lines = [
        Line(
            text="The facility achieved 94% uptime in Q3. This was attributable to cooling.",
            rect=line_rect,
        )
    ]

    sentences = segment_page_sentences(lines, sentence_max_chars=1000)

    first, second = sentences[0], sentences[1]
    assert len(first.rects) == 1
    assert len(second.rects) == 1
    assert first.rects[0][0] == pytest.approx(line_rect[0])  # starts at the line's left edge
    assert first.rects[0][2] < line_rect[2]  # ends short of the full line
    assert second.rects[0][2] == pytest.approx(line_rect[2])  # ends at the line's right edge
    # Adjacent sentences' clipped rects must not overlap by more than 2pt
    # (docs/08-testing.md#geometry-tests).
    overlap = first.rects[0][2] - second.rects[0][0]
    assert overlap <= 2.0


def test_segment_page_sentences_covers_multiple_lines_with_one_rect_each() -> None:
    lines = [
        Line(text="This sentence spans", rect=(72.0, 100.0, 300.0, 110.0)),
        Line(text="two full lines.", rect=(72.0, 112.0, 250.0, 122.0)),
    ]

    sentences = segment_page_sentences(lines, sentence_max_chars=1000)

    assert len(sentences) == 1
    assert sentences[0].text == "This sentence spans two full lines."
    assert len(sentences[0].rects) == 2
    # Both lines are fully covered by this one sentence, so no horizontal clipping.
    assert sentences[0].rects[0] == pytest.approx(lines[0].rect)
    assert sentences[0].rects[1] == pytest.approx(lines[1].rect)


def test_segment_page_sentences_hard_splits_a_very_long_sentence() -> None:
    long_text = "word " * 400  # 2000 chars, no sentence-ending punctuation at all
    lines = [Line(text=long_text.strip(), rect=(72.0, 140.0, 500.0, 155.0))]

    sentences = segment_page_sentences(lines, sentence_max_chars=1000)

    assert len(sentences) >= 2
    assert all(len(s.text) <= 1000 for s in sentences)
    # Reassembling every piece must reproduce the original content (nothing dropped).
    assert "".join(s.text for s in sentences).replace(" ", "") == long_text.strip().replace(" ", "")


def test_segment_page_sentences_never_returns_a_rect_outside_the_line_it_came_from() -> None:
    lines = [
        Line(text="Short first line.", rect=(72.0, 100.0, 200.0, 110.0)),
        Line(text="A somewhat longer second line here.", rect=(72.0, 112.0, 400.0, 122.0)),
    ]
    for sentence in segment_page_sentences(lines, sentence_max_chars=1000):
        for rect in sentence.rects:
            matching_line = next(line for line in lines if line.rect[1] <= rect[1] <= line.rect[3])
            assert (
                matching_line.rect[0] - 0.01 <= rect[0] <= rect[2] <= matching_line.rect[2] + 0.01
            )


# -- chunk assembly -------------------------------------------------------------------------------


def _sentence(text: str) -> SegmentedSentence:
    return SegmentedSentence(text=text, rects=[(0.0, 0.0, 10.0, 10.0)])


def test_assemble_chunks_returns_nothing_for_a_thin_page(settings: Settings) -> None:
    assert assemble_chunks([_sentence("Too short.")], settings) == []


def test_assemble_chunks_targets_400_to_600_tokens(settings: Settings) -> None:
    # 40 sentences of ~50 chars (~12 tokens each at 4 chars/token) -> plenty to form several
    # full-sized chunks.
    sentences = [
        _sentence("This is a moderately sized filler sentence number " + str(i)) for i in range(200)
    ]

    drafts = assemble_chunks(sentences, settings)

    assert len(drafts) >= 2
    # Every chunk except (possibly) the last should be within shouting distance of the target
    # range; the hard cap must never be exceeded.
    for draft in drafts:
        assert draft.token_estimate <= settings.chunk_hard_cap_tokens
    for draft in drafts[:-1]:
        assert draft.token_estimate >= settings.chunk_target_min_tokens


def test_assemble_chunks_overlaps_one_sentence_between_consecutive_chunks(
    settings: Settings,
) -> None:
    sentences = [
        _sentence("Filler sentence number " + str(i) + " with some extra words here.")
        for i in range(150)
    ]

    drafts = assemble_chunks(sentences, settings)

    assert len(drafts) >= 2
    for previous, current in zip(drafts, drafts[1:], strict=False):
        assert previous.sentences[-1].text == current.sentences[0].text


def test_assemble_chunks_ordinals_are_sequential_from_zero(settings: Settings) -> None:
    sentences = [
        _sentence("Filler sentence number " + str(i) + " with extra words padding it out.")
        for i in range(150)
    ]
    drafts = assemble_chunks(sentences, settings)
    assert [d.ordinal for d in drafts] == list(range(len(drafts)))


def test_assemble_chunks_splits_a_chunk_that_would_exceed_the_byte_cap(settings: Settings) -> None:
    tiny_settings = settings.model_copy(update={"chunk_item_max_bytes": 200})
    sentences = [_sentence("Sentence " + str(i) + " padded out a little more.") for i in range(10)]

    drafts = assemble_chunks(sentences, tiny_settings)

    assert len(drafts) > 1
    for draft in drafts:
        text_bytes = len(draft.text.encode())
        assert text_bytes <= tiny_settings.chunk_item_max_bytes + 100  # small slack for rects
    assert [d.ordinal for d in drafts] == list(range(len(drafts)))


def test_chunk_draft_text_is_sentences_joined_by_a_space(settings: Settings) -> None:
    sentences = [
        _sentence("First sentence padded to be long enough for realism here today.")
        for _ in range(30)
    ]
    drafts = assemble_chunks(sentences, settings)
    assert drafts[0].text == " ".join(s.text for s in drafts[0].sentences)
