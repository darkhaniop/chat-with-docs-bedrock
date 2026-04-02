"""`ingest-chunk`: reading order, sentence segmentation, chunk assembly. Runs once per page over
the page's extracted/OCR'd lines."""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import pysbd

from common.config import Settings
from common.geometry import clip_rect_horizontal
from common.models import Rect

_SEGMENTER = pysbd.Segmenter(language="en", clean=False, char_span=True)

# A page with fewer than 20 characters of text produces no text chunk - only its page vector."
_MIN_PAGE_CHARS_FOR_A_CHUNK = 20


@dataclass(frozen=True)
class Line:
    """A page line from either `extract.py` (PDF-native) or `ocr.py` (Textract) - this module
    doesn't care which produced it, only that both already report canonical-space rects."""

    text: str
    rect: Rect


@dataclass(frozen=True)
class SegmentedSentence:
    text: str
    rects: list[Rect]


@dataclass(frozen=True)
class ChunkDraft:
    ordinal: int
    text: str
    sentences: list[SegmentedSentence]
    token_estimate: int


# -- reading order ------------------------------------------------------------------------------


def _merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def order_lines_reading_order(lines: list[Line]) -> list[Line]:
    """Column bands are the merged x-intervals of every line's rect; a line is assigned to
    whichever band contains its horizontal midpoint. This is a known approximation - a
    full-width header whose x-range bridges two genuine columns will merge them into one band -
    but it matches the fixture corpus and the method the docs specify.
    """
    if not lines:
        return []
    bands = _merge_intervals([(line.rect[0], line.rect[2]) for line in lines])

    def band_index(rect: Rect) -> int:
        midpoint = (rect[0] + rect[2]) / 2
        for index, (band_start, band_end) in enumerate(bands):
            if band_start <= midpoint <= band_end:
                return index
        return min(range(len(bands)), key=lambda i: abs((bands[i][0] + bands[i][1]) / 2 - midpoint))

    grouped: dict[int, list[Line]] = {i: [] for i in range(len(bands))}
    for line in lines:
        grouped[band_index(line.rect)].append(line)

    ordered: list[Line] = []
    for index in range(len(bands)):
        ordered.extend(sorted(grouped[index], key=lambda line: line.rect[1]))
    return ordered


# -- joined text with hyphenation healing and per-character source tracking ---------------------


def _build_reading_text(lines: list[Line]) -> tuple[str, list[tuple[int, int]]]:
    """Returns `(joined_text, source)` where `source[k] == (line_index, offset_in_line_text)`
    for `joined_text[k]`. `offset_in_line_text` is always an index into the *original*
    `lines[line_index].text` (hyphen included), even though a healed hyphen itself is never
    emitted into `joined_text` — `_rects_for_span` divides by the original line length, so the
    clip fraction is computed against the line's true rendered width either way.
    """
    chars: list[str] = []
    source: list[tuple[int, int]] = []
    last_index = len(lines) - 1
    for i, line in enumerate(lines):
        text = line.text
        heal = i < last_index and len(text) > 1 and text.endswith("-")
        emit_length = len(text) - 1 if heal else len(text)
        for offset in range(emit_length):
            chars.append(text[offset])
            source.append((i, offset))
        if i < last_index and not heal:
            chars.append(" ")
            source.append((i, len(text)))
    return "".join(chars), source


def _sentence_char_spans(text: str) -> list[tuple[int, int]]:
    if not text.strip():
        return []
    return [(span.start, span.end) for span in _SEGMENTER.segment(text) if span.end > span.start]


def _hard_split_spans(spans: list[tuple[int, int]], *, max_chars: int) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for start, end in spans:
        cursor = start
        while end - cursor > max_chars:
            result.append((cursor, cursor + max_chars))
            cursor += max_chars
        result.append((cursor, end))
    return result


def _rects_for_span(
    joined_text: str,
    source: list[tuple[int, int]],
    lines: list[Line],
    start: int,
    end: int,
) -> list[Rect]:
    touched = source[start:end]
    if not touched:
        return []
    rects: list[Rect] = []
    for line_index, offsets in itertools.groupby(touched, key=lambda pair: pair[0]):
        offset_values = [offset for _, offset in offsets]
        line = lines[line_index]
        line_length = max(1, len(line.text))
        start_fraction = min(offset_values) / line_length
        end_fraction = min(1.0, (max(offset_values) + 1) / line_length)
        rects.append(
            clip_rect_horizontal(
                line.rect, start_fraction=start_fraction, end_fraction=end_fraction
            )
        )
    return rects


def segment_page_sentences(
    lines: list[Line], *, sentence_max_chars: int
) -> list[SegmentedSentence]:
    """The full pipeline from raw (unordered) page lines to segmented, geometry-mapped
    sentences: reading order -> joined text with hyphenation healing -> sentence boundaries ->
    hard-split -> per-sentence rects."""
    ordered = order_lines_reading_order(lines)
    if not ordered:
        return []
    joined_text, source = _build_reading_text(ordered)
    spans = _hard_split_spans(_sentence_char_spans(joined_text), max_chars=sentence_max_chars)
    sentences: list[SegmentedSentence] = []
    for start, end in spans:
        text = joined_text[start:end].strip()
        if not text:
            continue
        rects = _rects_for_span(joined_text, source, ordered, start, end)
        sentences.append(SegmentedSentence(text=text, rects=rects))
    return sentences


# -- chunk assembly -------------------------------------------------------------------------------


def _token_estimate(text: str, *, chars_per_token: int) -> int:
    return max(1, len(text) // chars_per_token)


def assemble_chunks(sentences: list[SegmentedSentence], settings: Settings) -> list[ChunkDraft]:
    """Target 400-600 tokens, hard cap 900, one sentence of overlap between consecutive chunks
    on the same page. A page with fewer than `_MIN_PAGE_CHARS_FOR_A_CHUNK` characters of
    sentence text produces no chunks at all."""
    total_chars = sum(len(s.text) for s in sentences)
    if total_chars < _MIN_PAGE_CHARS_FOR_A_CHUNK:
        return []

    chars_per_token = settings.chars_per_token_estimate
    target_max = settings.chunk_target_max_tokens
    target_min = settings.chunk_target_min_tokens
    hard_cap = settings.chunk_hard_cap_tokens

    drafts: list[ChunkDraft] = []
    current: list[SegmentedSentence] = []
    current_tokens = 0
    index = 0
    while index < len(sentences):
        sentence = sentences[index]
        sentence_tokens = _token_estimate(sentence.text, chars_per_token=chars_per_token)
        would_be = current_tokens + sentence_tokens
        if current and (
            would_be > hard_cap or (current_tokens >= target_min and would_be > target_max)
        ):
            drafts.append(_finalize_chunk_draft(len(drafts), current, chars_per_token))
            # One-sentence overlap: the next chunk starts with a copy of the last sentence
            # already emitted, then continues forward from the current (not-yet-consumed)
            # sentence — the overlap sentence is never counted twice against `index`.
            current = [current[-1]]
            current_tokens = _token_estimate(current[-1].text, chars_per_token=chars_per_token)
            continue
        current.append(sentence)
        current_tokens = would_be
        index += 1

    if current:
        drafts.append(_finalize_chunk_draft(len(drafts), current, chars_per_token))

    return _split_oversized_drafts(drafts, settings, chars_per_token)


def _finalize_chunk_draft(
    ordinal: int, sentences: list[SegmentedSentence], chars_per_token: int
) -> ChunkDraft:
    text = " ".join(s.text for s in sentences)
    return ChunkDraft(
        ordinal=ordinal,
        text=text,
        sentences=list(sentences),
        token_estimate=_token_estimate(text, chars_per_token=chars_per_token),
    )


def _estimated_item_bytes(draft: ChunkDraft) -> int:
    text_bytes = len(draft.text.encode())
    rects_bytes = sum(
        len(s.text.encode()) + len(s.rects) * 32  # ~32 bytes per (x0,y0,x1,y1) as stored numbers
        for s in draft.sentences
    )
    return text_bytes + rects_bytes


def _split_oversized_drafts(
    drafts: list[ChunkDraft], settings: Settings, chars_per_token: int
) -> list[ChunkDraft]:
    result: list[ChunkDraft] = []
    pending = list(drafts)
    while pending:
        draft = pending.pop(0)
        if (
            _estimated_item_bytes(draft) <= settings.chunk_item_max_bytes
            or len(draft.sentences) <= 1
        ):
            result.append(draft)
            continue
        midpoint = len(draft.sentences) // 2
        first_half = draft.sentences[:midpoint]
        second_half = draft.sentences[midpoint:]
        pending.insert(0, _finalize_chunk_draft(draft.ordinal, second_half, chars_per_token))
        pending.insert(0, _finalize_chunk_draft(draft.ordinal, first_half, chars_per_token))
    for ordinal, draft in enumerate(result):
        if draft.ordinal != ordinal:
            result[ordinal] = ChunkDraft(
                ordinal=ordinal,
                text=draft.text,
                sentences=draft.sentences,
                token_estimate=draft.token_estimate,
            )
    return result
