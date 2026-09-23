# Drum Velocity

Let the drums write the accents into another MIDI part, across a whole song.

## Start here

1. Double-click **Start.cmd** on Windows.
2. Choose the full-song **drum source MIDI** and **target MIDI**.
3. Select the source drum tracks and the target tracks you want to change.
4. Choose an output filename and make the MIDI.
5. Import the result into FL Studio and play it through your velocity-responsive patch.

Both inputs can be the **same full arrangement MIDI**: choose drums on the source side and the synth/bass/chord tracks on the target side. The result contains the entire target arrangement, with only the selected note velocities changed.

Export both files from the **same song start**, including any empty intro. The script matches quarter-note positions across the complete song, even when the files use different MIDI tick resolutions. It does not repeat the opening drum pattern. Tempo differences are not used to stretch either file; both files must describe the same musical timeline.

The window suggests drum tracks from MIDI channel 10 or their names. Check the highlighted choices: custom FL drum mappings may use any channel. The command line uses all source notes unless you explicitly select tracks/channels.

## How it moves

At every target note start, the script looks for a nearby drum hit or the fading influence of a recent hit. Stronger drum velocities create stronger influence. When several drums hit at once, the strongest velocity wins; they are not added together.

- **Follow drums:** louder near hits, softer between them.
- **Duck on drums:** softer near hits, louder between them. This changes note attacks, not the audio level of a held note.
- **Amount:** blends the new velocities with your existing pattern. Try 50-70% to retain some of your original phrasing.
- **Low / high velocity:** the range used at full amount. At lower amounts, blending can leave a note outside this range because it retains some original velocity.
- **Accent fade:** how long each hit influences later notes, in quarter-note beats. `0.25` = a sixteenth note; `0.5` = an eighth; `1` = a quarter. Longer fades spread accents across more notes. A small timing tolerance also catches slightly early/late hits.
- **Drum shift:** positive values delay the drum reference; negative values bring it earlier. `4` shifts it one 4/4 bar. Target note timing never moves.
- **No-hit notes:** normally use the low velocity in Follow mode or high velocity in Duck mode. Choose to keep their originals if you only want edits near drums.

Try selecting just kick/snare tracks if regular hi-hats make the result too uniform. The script reads MIDI notes, so it cannot infer the loudness or sound of the drum samples.

Velocity is set **when a note starts**. Your repeated-note pattern is a good fit. A single sustained chord will not pulse internally; you would need retriggered notes or audio/expression automation for that. This script does not split notes or generate an FL automation clip.

## What is preserved

All target tracks remain in the output. Note pitches, note positions and lengths, note-off messages (including note-on messages with velocity zero), channels, controllers, program changes, tempo, time signatures, and other parsed MIDI messages are preserved. Only positive-velocity note-on messages in the selected target tracks/channels are edited. Output velocities stay between 1 and 127 so no note gets accidentally turned into a note-off.

The script patches the original velocity bytes, preserving the rest of the file exactly, including running status and split SysEx messages. At Amount 0%, the output is byte-identical to the target. Standard MIDI type 0 and type 1 with quarter-note timing are supported. Type 2 and SMPTE-timed files produce a clear error instead of being misaligned.

The source files cannot be overwritten, including when one input is also used as the other. If the drum song ends early, its influence finishes normally and later target notes use the no-hit setting; there is no loop.

## Requirements

Python 3.10 or newer, Tkinter (included with the standard Windows Python installer), and the Mido MIDI library.

Install the dependency with:

```text
python -m pip install -r requirements.txt
```

No audio/MIDI hardware, internet service, API key, or FL Studio plugin is required to process files.

## Command line

From this folder:

```text
python drum_velocity.py drums.mid melody.mid melody_grooved.mid
```

Blend 65% of the drum shape into the original velocities, with a longer fade:

```text
python drum_velocity.py drums.mid melody.mid melody_grooved.mid --amount 0.65 --decay 0.5
```

Inspect a full arrangement to find its track indices and channels:

```text
python drum_velocity.py --inspect song.mid
```

Use channel 10 drums from the same song, modifying only target track index 2:

```text
python drum_velocity.py song.mid song.mid song_grooved.mid --drum-channels 10 --target-tracks 2
```

Duck only around hits and leave unmatched notes as they were:

```text
python drum_velocity.py drums.mid melody.mid melody_ducked.mid --mode duck --unmatched keep
```

Run `python drum_velocity.py --help` for all options. CLI track indices are **zero-based**; MIDI channel numbers are **1 through 16**. Output defaults to `TARGET_drum_velocity.mid` if the third path is omitted. Existing outputs are protected unless `--overwrite` is passed; input files are always protected.

## Validation

Run the included deterministic checks with:

```text
python -m unittest -v test_drum_velocity
```

They cover whole-song alignment, different tick resolutions/tempos, track and channel selection, drum tails, velocity-zero note-offs, chords, unchanged MIDI events, and input/output protection.

MIDI interpretation uses [Mido's documented Standard MIDI File API](https://mido.github.io/mido/files/midi.html); the original target bytes are patched to preserve all content outside the edited velocities.

## License

No application license has been selected for this source release. Public visibility alone does not grant a license to reuse, modify, or redistribute it. Existing third-party notices, where supplied, are retained and apply to their respective components.
