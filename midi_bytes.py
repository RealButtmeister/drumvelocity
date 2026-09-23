"""Locate MIDI attack velocities without rewriting any other source bytes.

Only Standard MIDI File event boundaries are interpreted here. In particular,
SysEx and metadata payloads are skipped exactly as encoded, so split F0/F7
messages, running status, and manufacturer data survive velocity edits.
"""

from __future__ import annotations


def note_velocity_offsets(data: bytes) -> list[list[int]]:
    """Return positive note-on velocity byte offsets, grouped by MIDI track.

    Offsets index the entire original byte string. Within a track they follow
    event order, matching mido's positive note-on traversal. The caller should
    validate the musical MIDI representation before using these offsets.
    """
    size = len(data)

    def fail(detail: str) -> ValueError:
        return ValueError(f"Cannot safely edit MIDI bytes: {detail}.")

    def unsigned(start: int, length: int, end: int) -> int:
        if start < 0 or start + length > end:
            raise fail("truncated header or chunk")
        return int.from_bytes(data[start:start + length], "big")

    def vlq(start: int, end: int) -> tuple[int, int]:
        value = 0
        for _ in range(4):
            if start >= end:
                raise fail("truncated variable-length number")
            byte = data[start]
            start += 1
            value = (value << 7) | (byte & 0x7F)
            if byte < 0x80:
                return value, start
        raise fail("variable-length number exceeds four bytes")

    if size < 14 or data[:4] != b"MThd":
        raise fail("missing Standard MIDI File header")
    header_length = unsigned(4, 4, size)
    if header_length < 6 or 8 + header_length > size:
        raise fail("invalid MIDI header length")
    track_count = unsigned(10, 2, size)
    cursor = 8 + header_length
    result: list[list[int]] = []

    for track_index in range(track_count):
        if cursor + 8 > size or data[cursor:cursor + 4] != b"MTrk":
            raise fail(f"missing track chunk {track_index}")
        track_size = unsigned(cursor + 4, 4, size)
        cursor += 8
        end = cursor + track_size
        if end > size:
            raise fail(f"truncated track chunk {track_index}")
        offsets: list[int] = []
        running_status: int | None = None

        while cursor < end:
            _, cursor = vlq(cursor, end)
            if cursor >= end:
                raise fail(f"missing event after delta time in track {track_index}")
            status = data[cursor]
            if status >= 0x80:
                cursor += 1
                if status < 0xF0:
                    running_status = status
            else:
                if running_status is None:
                    raise fail(f"running status has no channel event in track {track_index}")
                status = running_status

            if 0x80 <= status < 0xF0:
                length = 1 if status & 0xF0 in (0xC0, 0xD0) else 2
                if cursor + length > end:
                    raise fail(f"truncated channel event in track {track_index}")
                if any(value >= 0x80 for value in data[cursor:cursor + length]):
                    raise fail(f"invalid channel data byte in track {track_index}")
                if status & 0xF0 == 0x90 and data[cursor + 1] > 0:
                    offsets.append(cursor + 1)
                cursor += length
            elif status == 0xFF:
                if cursor >= end:
                    raise fail(f"missing metadata type in track {track_index}")
                cursor += 1  # The type byte does not change payload framing.
                payload_length, cursor = vlq(cursor, end)
                if cursor + payload_length > end:
                    raise fail(f"truncated metadata payload in track {track_index}")
                cursor += payload_length
                # Match mido: metadata leaves prior channel running status intact.
            elif status in (0xF0, 0xF7):
                payload_length, cursor = vlq(cursor, end)
                if cursor + payload_length > end:
                    raise fail(f"truncated SysEx payload in track {track_index}")
                cursor += payload_length
                running_status = None
            else:
                raise fail(f"unsupported status 0x{status:02X} in track {track_index}")

        result.append(offsets)

    # Like mido, only declared tracks are interpreted. Keeping the original byte
    # array also preserves any trailing file data that the reader did not use.
    return result
