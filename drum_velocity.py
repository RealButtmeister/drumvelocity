"""Transfer a full song's drum accents to the note velocities of another MIDI.

Run without arguments for the file-picker window, or run --help for the CLI.
Requires Python 3.10+ and mido: python -m pip install mido
"""

from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
import json
from io import BytesIO
import math
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

try:
    import mido
except ImportError:
    raise SystemExit("Missing MIDI library. Run: python -m pip install mido")

from midi_bytes import note_velocity_offsets


@dataclass(frozen=True)
class Settings:
    mode: str = "follow"
    amount: float = 1.0
    minimum: int = 30
    maximum: int = 115
    decay_beats: float = 0.25
    tolerance_beats: float = 0.03125
    shift_beats: float = 0.0
    source_tracks: tuple[int, ...] | None = None
    source_channels: tuple[int, ...] | None = None
    target_tracks: tuple[int, ...] | None = None
    target_channels: tuple[int, ...] | None = None
    unmatched: str = "floor"

    def validate(self) -> None:
        if self.mode not in ("follow", "duck"):
            raise ValueError("Mode must be 'follow' or 'duck'.")
        if self.unmatched not in ("floor", "keep"):
            raise ValueError("Unmatched behavior must be 'floor' or 'keep'.")
        for name in ("amount", "decay_beats", "tolerance_beats", "shift_beats"):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be a finite number.")
        if not 0 <= self.amount <= 1:
            raise ValueError("Amount must be between 0 and 1.")
        if not (isinstance(self.minimum, int) and isinstance(self.maximum, int)
                and 1 <= self.minimum <= self.maximum <= 127):
            raise ValueError("Velocity limits must be integers: 1 <= minimum <= maximum <= 127.")
        if self.decay_beats < 0 or self.tolerance_beats < 0:
            raise ValueError("Decay and timing tolerance cannot be negative.")
        for name in ("source_channels", "target_channels"):
            values = getattr(self, name)
            if values is not None and (not values or any(
                    not isinstance(c, int) or not 1 <= c <= 16 for c in values)):
                raise ValueError(f"{name} must contain channel numbers from 1 to 16.")
        for name in ("source_tracks", "target_tracks"):
            values = getattr(self, name)
            if values is not None and (not values or any(
                    not isinstance(t, int) or t < 0 for t in values)):
                raise ValueError(f"{name} must contain nonnegative track indices.")


def _read_midi(path: str | Path, data: bytes | bytearray | None = None) -> mido.MidiFile:
    path = Path(path)
    try:
        midi = mido.MidiFile(file=BytesIO(data)) if data is not None else mido.MidiFile(str(path))
    except (OSError, EOFError, ValueError, KeyError) as exc:
        raise ValueError(f"Cannot read MIDI '{path.name}': {exc or 'incomplete or invalid MIDI data'}") from exc
    if midi.type not in (0, 1):
        raise ValueError(f"'{path.name}' is type {midi.type} MIDI. Export a type 0 or type 1 song MIDI.")
    if midi.ticks_per_beat <= 0:
        raise ValueError(f"'{path.name}' uses SMPTE timing. Export MIDI with ticks-per-quarter-note timing.")
    return midi


def _is_attack(message: Any) -> bool:
    return message.type == "note_on" and message.velocity > 0


def _duration(midi: mido.MidiFile) -> float:
    return max((sum(m.time for m in t) for t in midi.tracks), default=0) / midi.ticks_per_beat


def inspect_midi(path: str | Path) -> dict[str, Any]:
    """Return track choices without changing the MIDI. Channels are 1-based."""
    midi = _read_midi(path)
    tracks = []
    for index, track in enumerate(midi.tracks):
        attacks = [msg for msg in track if _is_attack(msg)]
        tracks.append({
            "index": index,
            "name": track.name or f"Track {index + 1}",
            "note_count": len(attacks),
            "channels": sorted({msg.channel + 1 for msg in attacks}),
            "drum_note_count": sum(msg.channel == 9 for msg in attacks),
        })
    note_count = sum(t["note_count"] for t in tracks)
    return {
        "type": midi.type,
        "ticks_per_beat": midi.ticks_per_beat,
        "length_beats": _duration(midi),
        "note_count": note_count,
        "tracks": tracks,
        "warnings": [] if note_count else ["This file contains no positive-velocity note starts."],
    }


def _validate_tracks(selection: tuple[int, ...] | None, midi: mido.MidiFile, label: str) -> None:
    if selection is not None and any(i >= len(midi.tracks) for i in selection):
        raise ValueError(f"{label} track selection includes a track that does not exist.")


def _selected(index: int, message: Any, tracks: tuple[int, ...] | None,
              channels: tuple[int, ...] | None) -> bool:
    return ((tracks is None or index in tracks)
            and (channels is None or message.channel + 1 in channels))


class DrumEnvelope:
    """A short, velocity-weighted tail after each hit, sampled at target attacks."""

    def __init__(self, midi: mido.MidiFile, settings: Settings):
        self.settings = settings
        self.source_hits = 0
        # Simultaneous drums contribute their strongest velocity, not their sum.
        # Group before converting PPQ so dense chords cannot inflate the envelope.
        by_tick: dict[int, int] = {}
        for index, track in enumerate(midi.tracks):
            tick = 0
            for message in track:
                tick += message.time
                if _is_attack(message) and _selected(
                        index, message, settings.source_tracks, settings.source_channels):
                    self.source_hits += 1
                    by_tick[tick] = max(by_tick.get(tick, 0), message.velocity)
        if not self.source_hits:
            raise ValueError("No drum note starts found. Check the source track and channel selection.")
        pairs = sorted(by_tick.items())
        self.times = [tick / midi.ticks_per_beat + settings.shift_beats for tick, _ in pairs]
        self.levels = [velocity / 127 for _, velocity in pairs]

    def at(self, beat: float) -> float:
        settings = self.settings
        tolerance = settings.tolerance_beats
        decay = settings.decay_beats
        # Work in quarter-note positions, independent of each file's PPQ/tempo.
        # A small tolerance catches humanized hits on either side of the attack.
        epsilon = 1e-9
        first = bisect_left(self.times, beat - tolerance - decay - epsilon)
        last = bisect_right(self.times, beat + tolerance + epsilon)
        strongest = 0.0
        for i in range(first, last):
            age = beat - self.times[i]
            if age <= tolerance + epsilon:
                value = self.levels[i]
            elif decay > 0:
                value = self.levels[i] * max(0.0, 1 - (age - tolerance) / decay)
            else:
                continue
            strongest = max(strongest, value)
        return strongest


def _same_file(a: Path, b: Path) -> bool:
    if a.resolve() == b.resolve():
        return True
    try:
        return a.samefile(b)
    except OSError:
        return False


def _save(data: bytes | bytearray, path: Path, overwrite: bool) -> None:
    if not path.parent.is_dir():
        raise ValueError(f"Output folder does not exist: {path.parent}")
    if path.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {path}. Choose a new filename or use --overwrite.")
    # Write completely before publishing the new output.
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".drum-velocity-", suffix=".mid",
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary, path)
        elif os.name == "nt":
            # On Windows rename fails if the destination appeared meanwhile.
            os.rename(temporary, path)
        else:
            # Hard-link publication gives the same no-clobber behavior on POSIX.
            os.link(temporary, path)
            temporary.unlink()
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def process_files(drums_path: str | Path, target_path: str | Path,
                  output_path: str | Path, settings: Settings = Settings(),
                  overwrite: bool = False) -> dict[str, Any]:
    """Write a new full-length target MIDI, changing selected attack velocities only.

    Both files share song beat zero. No pattern repetition or note insertion occurs.
    Track indices are zero-based; channel numbers are one-based (drums usually 10).
    """
    settings.validate()
    drums_path, target_path, output_path = map(Path, (drums_path, target_path, output_path))
    if any(_same_file(output_path, p) for p in (drums_path, target_path)):
        raise ValueError("Output must be a new file, separate from both input MIDI files.")
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {output_path}. Choose a new filename.")
    drums = _read_midi(drums_path)
    target_bytes = bytearray(target_path.read_bytes())
    target = _read_midi(target_path, target_bytes)
    offsets = note_velocity_offsets(target_bytes)
    if len(offsets) != len(target.tracks):
        raise ValueError("Target MIDI track structure could not be verified.")
    _validate_tracks(settings.source_tracks, drums, "Drum source")
    _validate_tracks(settings.target_tracks, target, "Target")
    envelope = DrumEnvelope(drums, settings)
    target_notes = changed_notes = matched_notes = 0
    cache: dict[int, float] = {}
    for index, track in enumerate(target.tracks):
        tick = 0
        note_index = 0
        for message in track:
            tick += message.time
            if not _is_attack(message):
                continue
            if note_index >= len(offsets[index]):
                raise ValueError("Target MIDI note structure could not be verified.")
            velocity_offset = offsets[index][note_index]
            note_index += 1
            if target_bytes[velocity_offset] != message.velocity:
                raise ValueError("Target MIDI velocity structure could not be verified.")
            if not _selected(
                    index, message, settings.target_tracks, settings.target_channels):
                continue
            target_notes += 1
            if tick not in cache:
                cache[tick] = envelope.at(tick / target.ticks_per_beat)
            intensity = cache[tick]
            if intensity > 0:
                matched_notes += 1
            elif settings.unmatched == "keep":
                continue
            shaped = intensity if settings.mode == "follow" else 1 - intensity
            mapped = settings.minimum + (settings.maximum - settings.minimum) * shaped
            mixed = message.velocity + settings.amount * (mapped - message.velocity)
            new_velocity = max(1, min(127, math.floor(mixed + 0.5)))
            if new_velocity != message.velocity:
                changed_notes += 1
                target_bytes[velocity_offset] = new_velocity
        if note_index != len(offsets[index]):
            raise ValueError("Target MIDI note structure could not be verified.")
    if target_notes == 0:
        raise ValueError("No target note starts found. Check the target track and channel selection.")
    warnings = []
    if not matched_notes:
        warnings.append("No target note starts fall near the selected drums. Check the tracks, song alignment, or decay.")
    if _duration(drums) + settings.shift_beats < _duration(target) - 1e-9:
        warnings.append("The drum file is shorter than the target. It does not loop; later notes use the no-hit setting.")
    # Patch original velocity bytes only. Re-serializing through a MIDI library
    # can alter running status or SysEx packet boundaries in otherwise valid files.
    _save(target_bytes, output_path, overwrite)
    return {
        "target_notes": target_notes,
        "changed_notes": changed_notes,
        "matched_notes": matched_notes,
        "source_hits": envelope.source_hits,
        "length_beats": _duration(target),
        "warnings": warnings,
    }


def _numbers(text: str) -> tuple[int, ...]:
    try:
        values = tuple(int(part.strip()) for part in text.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use comma-separated whole numbers, such as 1,2,3.") from exc
    if not values:
        raise argparse.ArgumentTypeError("Select at least one number.")
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Use a full song's drums to shape another MIDI's velocities. Files align by quarter-note position, with no looping.",
        epilog="No arguments opens the file-picker window. Track indices are zero-based; MIDI channels are 1 through 16.")
    parser.add_argument("drums", nargs="?", help="Drum source MIDI (may contain a full arrangement)")
    parser.add_argument("target", nargs="?", help="Target MIDI whose velocities will change")
    parser.add_argument("output", nargs="?", help="New MIDI path; default: TARGET_drum_velocity.mid")
    parser.add_argument("--inspect", metavar="MIDI", help="List track names, indices, and channels")
    parser.add_argument("--gui", action="store_true", help="Open the file-picker window")
    parser.add_argument("--mode", choices=("follow", "duck"), default="follow")
    parser.add_argument("--amount", type=float, default=1.0, help="Blend 0..1 with original velocities (default 1)")
    parser.add_argument("--min", dest="minimum", type=int, default=30, help="Low velocity (default 30)")
    parser.add_argument("--max", dest="maximum", type=int, default=115, help="High velocity (default 115)")
    parser.add_argument("--decay", type=float, default=0.25, help="Drum influence tail in quarter-note beats (default .25)")
    parser.add_argument("--tolerance", type=float, default=0.03125, help="Alignment tolerance in beats (default .03125)")
    parser.add_argument("--shift", type=float, default=0.0, help="Shift drum reference in beats; positive delays it")
    parser.add_argument("--drum-tracks", type=_numbers, help="Source track indices, e.g. 1,2 (default all)")
    parser.add_argument("--drum-channels", type=_numbers, help="Source channels, e.g. 10 (default all)")
    parser.add_argument("--target-tracks", type=_numbers, help="Target track indices (default all)")
    parser.add_argument("--target-channels", type=_numbers, help="Target channels (default all)")
    parser.add_argument("--unmatched", choices=("floor", "keep"), default="floor",
                        help="No-hit notes: floor uses low in Follow/high in Duck; keep preserves originals")
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing output, never either input")
    args = parser.parse_args(argv)
    try:
        if args.inspect:
            print(json.dumps(inspect_midi(args.inspect), indent=2))
            return 0
        if args.gui or not args.drums and not args.target:
            from drum_velocity_gui import main as gui_main
            gui_main()
            return 0
        if not args.drums or not args.target:
            parser.error("Provide both drum source and target MIDI paths.")
        output = args.output or str(Path(args.target).with_name(Path(args.target).stem + "_drum_velocity.mid"))
        settings = Settings(
            mode=args.mode, amount=args.amount, minimum=args.minimum, maximum=args.maximum,
            decay_beats=args.decay, tolerance_beats=args.tolerance, shift_beats=args.shift,
            source_tracks=args.drum_tracks, source_channels=args.drum_channels,
            target_tracks=args.target_tracks, target_channels=args.target_channels,
            unmatched=args.unmatched,
        )
        report = process_files(args.drums, args.target, output, settings, args.overwrite)
        print(f"Saved: {Path(output).resolve()}")
        print(json.dumps(report, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
