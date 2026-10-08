"""Clip-relative subtitle chunks shared by the renderers."""


def subtitle_chunks(segments, start, end, words_per_chunk=4):
    """Return (start, end, text) chunks clipped to the requested media range."""
    if not isinstance(words_per_chunk, int) or isinstance(words_per_chunk, bool) or words_per_chunk < 1:
        raise ValueError("words_per_chunk must be a positive integer.")
    chunks = []
    for segment in segments:
        segment_start, segment_end = segment["start"], segment["end"]
        if segment_end <= start or segment_start >= end:
            continue
        relative_start = max(segment_start, start) - start
        relative_end = min(segment_end, end) - start
        words = segment["text"].split()
        count = len(words)
        for index in range(0, count, words_per_chunk):
            stop = min(index + words_per_chunk, count)
            chunks.append((
                relative_start + (relative_end - relative_start) * index / count,
                relative_start + (relative_end - relative_start) * stop / count,
                " ".join(words[index:stop]),
            ))
    return chunks
