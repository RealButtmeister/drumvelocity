"""Deterministic, full-song regression tests; no downloaded MIDI needed.

Run from this folder with: python -m unittest -v test_drum_velocity
"""

import math
from pathlib import Path
import struct
import tempfile
import unittest

import mido

from drum_velocity import Settings, inspect_midi, process_files
from midi_bytes import note_velocity_offsets


def note(pitch=60, velocity=90, channel=0):
    return mido.Message("note_on", note=pitch, velocity=velocity, channel=channel)


def off(pitch=60, velocity=64, channel=0):
    return mido.Message("note_off", note=pitch, velocity=velocity, channel=channel)


def write_midi(path, specs, ppq=480, midi_type=1):
    """Specs are tracks of (absolute quarter-note beat, message) pairs."""
    midi = mido.MidiFile(type=midi_type, ticks_per_beat=ppq)
    for events in specs:
        track = mido.MidiTrack()
        previous = 0
        # Python's stable sort retains event order at identical timestamps.
        for beat, message in sorted(events, key=lambda item: item[0]):
            absolute = round(beat * ppq)
            track.append(message.copy(time=absolute - previous))
            previous = absolute
        midi.tracks.append(track)
    midi.save(path)
    return path


def positive_notes(path):
    found = []
    midi = mido.MidiFile(path)
    for track_index, track in enumerate(midi.tracks):
        tick = 0
        for message in track:
            tick += message.time
            if message.type == "note_on" and message.velocity > 0:
                found.append((track_index, tick, message.channel,
                              message.note, message.velocity))
    return found


def all_messages(path, hide_positive_velocity=False):
    midi = mido.MidiFile(path)
    tracks = []
    for track in midi.tracks:
        messages = []
        for message in track:
            fields = message.dict()
            if (hide_positive_velocity and message.type == "note_on"
                    and message.velocity > 0):
                fields["velocity"] = "EDITABLE"
            messages.append(fields)
        tracks.append(messages)
    return midi.type, midi.ticks_per_beat, tracks


def mapped(intensity, old=90, *, minimum=30, maximum=115,
           mode="follow", amount=1):
    if mode == "duck":
        intensity = 1 - intensity
    desired = minimum + (maximum - minimum) * intensity
    return max(1, min(127, math.floor(old + amount * (desired - old) + .5)))


def raw_midi(*tracks, header_extra=b"", trailing=b""):
    return (b"MThd" + struct.pack(">IHHH", 6 + len(header_extra),
                                 0 if len(tracks) == 1 else 1, len(tracks), 480)
            + header_extra
            + b"".join(b"MTrk" + struct.pack(">I", len(track)) + track
                       for track in tracks)
            + trailing)


class DrumVelocityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.drums = self.folder / "drums.mid"
        self.target = self.folder / "target.mid"
        self.output = self.folder / "result.mid"

    def run_mapping(self, settings=None):
        return process_files(self.drums, self.target, self.output,
                             settings=settings or Settings())

    def velocities(self):
        return [entry[-1] for entry in positive_notes(self.output)]

    def test_full_song_128_bars_uses_local_hits_without_looping(self):
        # A distinct event after bar 100 catches accidental first-pattern reuse.
        write_midi(self.drums, [[
            (0, note(36, 127, 9)), (.125, off(36, channel=9)),
            (400, note(38, 64, 9)), (400.125, off(38, channel=9)),
            (512, mido.MetaMessage("end_of_track")),
        ]])
        positions = [0, 4, 100, 399, 400, 404, 508]
        events = []
        for beat in positions:
            events.extend([(beat, note()), (beat + .125, off())])
        events.append((512, mido.MetaMessage("end_of_track")))
        write_midi(self.target, [events])
        stats = self.run_mapping()
        self.assertEqual(self.velocities(), [115, 30, 30, 30,
                                             mapped(64 / 127), 30, 30])
        self.assertEqual(stats["target_notes"], 7)
        self.assertEqual(stats["changed_notes"], 7)
        self.assertEqual(stats["matched_notes"], 2)
        self.assertEqual(stats["source_hits"], 2)
        self.assertAlmostEqual(stats["length_beats"], 512)

    def test_different_ppq_and_tempo_maps_align_by_beats(self):
        write_midi(self.drums, [[
            (0, mido.MetaMessage("set_tempo", tempo=1_000_000)),
            (1, note(36, 127, 9)), (1.125, off(36, channel=9)),
            (100, mido.MetaMessage("set_tempo", tempo=250_000)),
            (257.5, note(38, 64, 9)), (257.75, off(38, channel=9)),
        ]], ppq=96)
        write_midi(self.target, [[
            (0, mido.MetaMessage("set_tempo", tempo=400_000)),
            (1, note()), (1.125, off()),
            (20, mido.MetaMessage("set_tempo", tempo=800_000)),
            (257.5, note(62)), (257.75, off(62)),
        ]], ppq=960)
        self.run_mapping()
        self.assertEqual(self.velocities(), [115, mapped(64 / 127)])
        self.assertEqual(all_messages(self.target, True),
                         all_messages(self.output, True))

    def test_only_positive_note_on_velocities_change(self):
        write_midi(self.drums, [[(1, note(36, 127, 9))]])
        write_midi(self.target, [[
            (0, mido.MetaMessage("track_name", name="Keep me")),
            (0, mido.MetaMessage("time_signature", numerator=7, denominator=8)),
            (0, mido.MetaMessage("key_signature", key="Dm")),
            (0, mido.MetaMessage("set_tempo", tempo=461538)),
            (.25, mido.Message("program_change", program=17, channel=2)),
            (.5, mido.Message("control_change", control=64, value=127, channel=2)),
            (.75, mido.Message("pitchwheel", pitch=-1234, channel=2)),
            (1, note(60, 75, 2)),
            (1, mido.Message("sysex", data=(1, 2, 3, 4))),
            (1.25, note(60, 0, 2)),
            (2, note(62, 90, 2)),
            (2.25, off(62, 41, 2)),
            (3, mido.MetaMessage("marker", text="Later section")),
            (4, mido.MetaMessage("end_of_track")),
        ]])
        before_drums = self.drums.read_bytes()
        before_target = self.target.read_bytes()
        self.run_mapping()
        self.assertEqual(self.velocities(), [115, 30])
        self.assertEqual(all_messages(self.target, True),
                         all_messages(self.output, True))
        self.assertEqual(self.drums.read_bytes(), before_drums)
        self.assertEqual(self.target.read_bytes(), before_target)

    def test_amount_zero_preserves_every_message(self):
        write_midi(self.drums, [[(0, note(36, 127, 9))]])
        write_midi(self.target, [
            [(0, mido.MetaMessage("set_tempo", tempo=650000)),
             (8, mido.MetaMessage("end_of_track"))],
            [(0, note(60, 43)), (0, note(64, 112)),
             (.5, note(60, 0)), (.5, off(64, 17)),
             (5, note(67, 1)), (6, off(67))],
        ])
        stats = self.run_mapping(Settings(amount=0))
        self.assertEqual(all_messages(self.target), all_messages(self.output))
        self.assertEqual(stats["changed_notes"], 0)

    def test_split_sysex_stays_byte_identical_when_amount_zero(self):
        write_midi(self.drums, [[(0, note(36, 127, 9))]])
        # One SysEx transmission continued ten ticks later with an F7 event.
        original = raw_midi(bytes.fromhex(
            "00 f0 02 01 02 0a f7 02 03 f7 00 90 3c 5a "
            "78 80 3c 40 00 ff 2f 00"))
        self.target.write_bytes(original)
        self.run_mapping(Settings(amount=0))
        self.assertEqual(self.output.read_bytes(), original)

    def test_split_sysex_edit_changes_only_positive_velocity_byte(self):
        write_midi(self.drums, [[(0, note(36, 127, 9))]])
        original = raw_midi(bytes.fromhex(
            "00 f0 02 01 02 0a f7 02 03 f7 00 90 3c 5a "
            "78 80 3c 40 00 ff 2f 00"))
        self.target.write_bytes(original)
        self.run_mapping()
        expected = bytearray(original)
        expected[22 + 13] = 115
        self.assertEqual(self.output.read_bytes(), bytes(expected))

    def test_running_status_and_metadata_bytes_survive_velocity_edits(self):
        write_midi(self.drums, [[(0, note(36, 127, 9))]])
        # Includes running note-on across metadata and a velocity-zero note-off.
        original = raw_midi(bytes.fromhex(
            "00 90 3c 50 00 40 60 00 ff 7f 03 90 01 7f "
            "00 43 70 00 47 00 00 c0 05 00 06 00 d0 20 "
            "00 21 00 90 4a 55 00 ff 2f 00"))
        self.target.write_bytes(original)
        self.run_mapping()
        expected = bytearray(original)
        for offset in (3, 6, 16, 33):
            expected[22 + offset] = 115
        self.assertEqual(self.output.read_bytes(), bytes(expected))

    def test_chord_members_receive_same_velocity_at_full_amount(self):
        write_midi(self.drums, [[(3, note(38, 85, 9))]])
        write_midi(self.target, [[
            (3, note(60, 2)), (3, note(64, 127)), (3, note(67, 63)),
            (4, off(60)), (4, off(64)), (4, off(67)),
        ]])
        self.run_mapping()
        self.assertEqual(self.velocities(), [mapped(85 / 127)] * 3)

    def test_strongest_overlapping_drum_influence_wins(self):
        write_midi(self.drums, [[
            (1, note(36, 30, 9)), (1, note(38, 100, 9)),
            (1, note(42, 70, 9)),
        ]])
        write_midi(self.target, [[(1, note())]])
        stats = self.run_mapping()
        self.assertEqual(self.velocities(), [mapped(100 / 127)])
        self.assertEqual(stats["source_hits"], 3)
        self.assertEqual(stats["matched_notes"], 1)

    def test_strong_earlier_tail_can_outweigh_a_quiet_new_hit(self):
        write_midi(self.drums, [[
            (1, note(36, 127, 9)), (1.125, note(42, 32, 9)),
        ]])
        write_midi(self.target, [[(1.15, note())]])
        self.run_mapping(Settings(tolerance_beats=.025, decay_beats=.25))
        self.assertEqual(self.velocities(), [mapped(.5)])

    def test_tolerance_decay_and_future_cutoff(self):
        write_midi(self.drums, [[(1, note(36, 127, 9))]], ppq=480)
        positions = [.95, .975, 1, 1.025, 1.15, 1.265, 1.3]
        write_midi(self.target, [[(beat, note()) for beat in positions]], ppq=480)
        self.run_mapping(Settings(tolerance_beats=.025, decay_beats=.25))
        # The penultimate onset is outside t-decay, but inside the real tail
        # window t-decay-tolerance. It must retain a small positive influence.
        quantized = round(1.265 * 480) / 480
        self.assertEqual(self.velocities(), [
            30, 115, 115, 115, mapped(.5),
            mapped(1 - (quantized - 1 - .025) / .25), 30,
        ])

    def test_shift_moves_drum_timeline_forward_in_beats(self):
        write_midi(self.drums, [[(1, note(36, 127, 9))]])
        write_midi(self.target, [[(1, note()), (1.5, note(62)), (2, note(64))]])
        self.run_mapping(Settings(shift_beats=.5))
        self.assertEqual(self.velocities(), [30, 115, 30])

    def test_duck_and_amount_blending(self):
        write_midi(self.drums, [[(0, note(36, 127, 9)),
                                  (1, note(38, 64, 9))]])
        write_midi(self.target, [[(0, note(60, 90)),
                                   (1, note(62, 50)), (2, note(64, 80))]])
        self.run_mapping(Settings(mode="duck", amount=.5))
        self.assertEqual(self.velocities(), [
            mapped(1, old=90, mode="duck", amount=.5),
            mapped(64 / 127, old=50, mode="duck", amount=.5),
            mapped(0, old=80, mode="duck", amount=.5),
        ])

    def test_unmatched_keep_preserves_existing_dynamics(self):
        write_midi(self.drums, [[(1, note(36, 127, 9))]])
        write_midi(self.target, [[(0, note(60, 43)),
                                   (1, note(62, 45)), (2, note(64, 47))]])
        self.run_mapping(Settings(unmatched="keep"))
        self.assertEqual(self.velocities(), [43, 115, 47])

    def test_track_and_one_based_channel_filters(self):
        write_midi(self.drums, [
            [(0, note(36, 127, 9))],
            [(0, note(36, 64, 9)), (0, note(60, 127, 0)),
             (1, note(38, 32, 9))],
        ])
        write_midi(self.target, [
            [(0, note(60, 40, 1))],
            [(0, note(62, 41, 0)), (0, note(64, 42, 1)),
             (1, note(65, 43, 1))],
        ])
        self.run_mapping(Settings(source_tracks=(1,), source_channels=(10,),
                                  target_tracks=(1,), target_channels=(2,)))
        self.assertEqual(self.velocities(), [40, 41, mapped(64 / 127),
                                             mapped(32 / 127)])

    def test_combined_song_can_be_both_inputs_with_distinct_track_filters(self):
        write_midi(self.target, [
            [(0, mido.MetaMessage("track_name", name="Drums")),
             (0, note(36, 127, 9)), (4, note(38, 64, 9))],
            [(0, mido.MetaMessage("track_name", name="Synth")),
             (0, note(60, 75)), (2, note(62, 85)), (4, note(64, 95))],
        ])
        original = self.target.read_bytes()
        process_files(self.target, self.target, self.output,
                      settings=Settings(source_tracks=(0,), target_tracks=(1,)))
        self.assertEqual(self.velocities(), [127, 64, 115, 30, mapped(64 / 127)])
        self.assertEqual(self.target.read_bytes(), original)

    def test_note_on_zero_is_not_a_source_hit(self):
        write_midi(self.drums, [[(0, note(36, 127, 9)),
                                  (1, note(36, 0, 9))]])
        write_midi(self.target, [[(1, note())]])
        stats = self.run_mapping()
        self.assertEqual(self.velocities(), [30])
        self.assertEqual(stats["source_hits"], 1)
        self.assertEqual(stats["matched_notes"], 0)

    def test_inspection_reports_track_channels_notes_and_song_end(self):
        write_midi(self.target, [
            [(0, mido.MetaMessage("track_name", name="Conductor")),
             (512, mido.MetaMessage("end_of_track"))],
            [(0, mido.MetaMessage("track_name", name="Drums + synth")),
             (1, note(36, 90, 9)), (2, note(36, 0, 9)),
             (10, note(60, 70, 2)), (11, off(60, channel=2))],
        ], ppq=960)
        info = inspect_midi(self.target)
        self.assertEqual(info["type"], 1)
        self.assertEqual(info["ticks_per_beat"], 960)
        self.assertEqual(info["note_count"], 2)
        self.assertAlmostEqual(info["length_beats"], 512)
        self.assertEqual(info["tracks"][0]["index"], 0)
        self.assertEqual(info["tracks"][0]["name"], "Conductor")
        self.assertEqual(info["tracks"][0]["note_count"], 0)
        self.assertEqual(info["tracks"][1]["channels"], [3, 10])
        self.assertEqual(info["tracks"][1]["note_count"], 2)
        self.assertEqual(info["tracks"][1]["drum_note_count"], 1)
        self.assertIn("warnings", info)

    def test_existing_output_requires_overwrite(self):
        write_midi(self.drums, [[(0, note(36, 127, 9))]])
        write_midi(self.target, [[(0, note())]])
        sentinel = b"User's previous file must survive"
        self.output.write_bytes(sentinel)
        with self.assertRaises((ValueError, FileExistsError)):
            self.run_mapping()
        self.assertEqual(self.output.read_bytes(), sentinel)
        process_files(self.drums, self.target, self.output, overwrite=True)
        self.assertEqual(self.velocities(), [115])

    def test_input_paths_are_protected_even_with_overwrite(self):
        write_midi(self.drums, [[(0, note(36, 127, 9))]])
        write_midi(self.target, [[(0, note())]])
        before = {path: path.read_bytes() for path in (self.drums, self.target)}
        for output in (self.drums, self.target):
            with self.subTest(output=output.name):
                with self.assertRaises((ValueError, FileExistsError)):
                    process_files(self.drums, self.target, output, overwrite=True)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    def test_type_two_is_rejected_without_creating_output(self):
        write_midi(self.drums, [[(0, note(36, 127, 9))]], midi_type=2)
        write_midi(self.target, [[(0, note())]])
        with self.assertRaises(ValueError):
            self.run_mapping()
        self.assertFalse(self.output.exists())

    def test_smpte_time_division_is_rejected_without_creating_output(self):
        # -24 frames per second, 40 ticks per frame, encoded as signed short.
        write_midi(self.drums, [[(0, note(36, 127, 9))]])
        data = bytearray(self.drums.read_bytes())
        data[12:14] = bytes((0xE8, 40))
        self.drums.write_bytes(data)
        write_midi(self.target, [[(0, note())]])
        with self.assertRaises(ValueError):
            self.run_mapping()
        self.assertFalse(self.output.exists())


class MidiByteOffsetsTests(unittest.TestCase):
    def test_finds_only_positive_note_velocities_with_running_status(self):
        track = bytes.fromhex(
            "00 90 3c 50 00 40 60 00 ff 7f 03 90 01 7f "
            "00 43 70 00 47 00 00 c0 05 00 06 00 d0 20 "
            "00 21 00 90 4a 55 00 ff 2f 00")
        self.assertEqual(note_velocity_offsets(raw_midi(track)),
                         [[22 + index for index in (3, 6, 16, 33)]])

    def test_track_boundaries_extended_header_and_trailing_bytes(self):
        track1 = bytes.fromhex("00 99 24 7f 00 ff 2f 00")
        track2 = bytes.fromhex("81 00 92 3c 40 00 ff 2f 00")
        data = raw_midi(track1, track2, header_extra=b"extra", trailing=b"footer")
        self.assertEqual(note_velocity_offsets(data), [[27 + 3], [43 + 4]])

    def test_payload_contents_are_never_interpreted_as_events(self):
        track = bytes.fromhex(
            "00 f0 03 01 02 03 00 f7 04 90 3c 7f f7 "
            "00 ff 7f 04 90 3c 7f 00 00 90 3c 55 00 ff 2f 00")
        self.assertEqual(note_velocity_offsets(raw_midi(track)), [[22 + 24]])

    def test_large_metadata_payload_length_and_delta_vlq(self):
        track = (b"\x00\xff\x7f\x81\x00" + b"\x00" * 128
                 + b"\x81\x80\x00\x90\x3c\x55\x00\xff\x2f\x00")
        self.assertEqual(note_velocity_offsets(raw_midi(track)), [[22 + 138]])

    def test_malformed_track_events_raise_clear_value_error(self):
        cases = {
            "missing status": "00",
            "truncated delta": "81",
            "overlong delta": "81 80 80 80 00 90 3c 55",
            "missing running status": "00 3c 55",
            "truncated note": "00 90 3c",
            "invalid data byte": "00 90 3c 80",
            "unsupported realtime": "00 f8",
            "missing meta type": "00 ff",
            "truncated meta length": "00 ff 7f 81",
            "truncated meta payload": "00 ff 7f 02 01",
            "truncated sysex payload": "00 f0 02 01",
            "running status after sysex": "00 90 3c 55 00 f0 01 f7 00 40 60",
        }
        for label, encoded in cases.items():
            with self.subTest(label=label):
                with self.assertRaisesRegex(ValueError, "Cannot safely edit MIDI bytes"):
                    note_velocity_offsets(raw_midi(bytes.fromhex(encoded)))

    def test_malformed_headers_and_chunks_raise_clear_value_error(self):
        valid = raw_midi(bytes.fromhex("00 90 3c 55 00 ff 2f 00"))
        cases = [b"", b"not a midi header", valid[:10],
                 valid[:4] + b"\x00\x00\x00\x05" + valid[8:],
                 valid[:14] + b"oops" + valid[18:], valid[:-1]]
        for data in cases:
            with self.subTest(data=data):
                with self.assertRaisesRegex(ValueError, "Cannot safely edit MIDI bytes"):
                    note_velocity_offsets(data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
