"""Desktop interface for Drum Velocity. Uses only Python's standard library.

Run this file beside drum_velocity.py. The two MIDI files share a full-song
quarter-note timeline; the drum reference is never repeated to fill the target.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from drum_velocity import Settings, inspect_midi, process_files


MIDI_TYPES = [("MIDI files", "*.mid *.midi"), ("All files", "*.*")]


class DrumVelocityApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Drum Velocity — full-song MIDI movement")
        self.root.geometry("1040x900")
        self.root.minsize(860, 760)
        self.root.configure(background="#eef1f5")
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.busy = False
        self.closed = False
        self.jobs: queue.Queue = queue.Queue()
        self.interactive: list[tk.Widget] = []
        self.saved_states: list[tuple[tk.Widget, str]] = []
        self.infos: dict[str, dict] = {}
        self.row_tracks: dict[str, list[dict]] = {}
        self.file_vars = {kind: tk.StringVar() for kind in ("source", "target")}
        self.channel_vars = {kind: tk.StringVar(value="All") for kind in ("source", "target")}
        self.info_vars = {kind: tk.StringVar(value="Choose a MIDI file to see its tracks.") for kind in ("source", "target")}
        self.listboxes: dict[str, tk.Listbox] = {}
        self.channel_boxes: dict[str, ttk.Combobox] = {}
        self.output_var = tk.StringVar()
        self.mode_var = tk.StringVar(value="follow")
        self.amount_var = tk.DoubleVar(value=100)
        self.amount_label = tk.StringVar(value="100%")
        self.minimum_var = tk.StringVar(value="30")
        self.maximum_var = tk.StringVar(value="115")
        self.decay_var = tk.StringVar(value="0.25")
        self.shift_var = tk.StringVar(value="0")
        self.tolerance_var = tk.StringVar(value="0.03125")
        self.keep_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Ready. Choose your drum reference and the MIDI you want to shape.")
        self.last_output: Path | None = None
        self._styles()
        self._build()
        self.root.after(100, self._poll)

    def _styles(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TFrame", background="#eef1f5")
        style.configure("TLabel", background="#eef1f5", foreground="#223044", font=("Segoe UI", 10))
        style.configure("TButton", font=("Segoe UI", 10), padding=(10, 7))
        style.configure("TCheckbutton", background="#eef1f5", font=("Segoe UI", 10))
        style.configure("TRadiobutton", background="#eef1f5", font=("Segoe UI", 10))
        style.configure("TLabelframe", background="#eef1f5", bordercolor="#ccd4df")
        style.configure("TLabelframe.Label", background="#eef1f5", foreground="#223044", font=("Segoe UI Semibold", 11))
        style.configure("Heading.TLabel", font=("Segoe UI Semibold", 24), foreground="#17263c")
        style.configure("Hint.TLabel", font=("Segoe UI", 9), foreground="#526176")
        style.configure("Action.TButton", font=("Segoe UI Semibold", 11), foreground="white", background="#315bdd", padding=(20, 11))
        style.map("Action.TButton", background=[("disabled", "#a3adc2"), ("active", "#244bbf")])

    def _add_control(self, widget):
        self.interactive.append(widget)
        return widget

    def _build(self) -> None:
        viewport = ttk.Frame(self.root)
        viewport.pack(fill="both", expand=True)
        canvas = tk.Canvas(viewport, background="#eef1f5", highlightthickness=0)
        page_scroll = ttk.Scrollbar(viewport, orient="vertical", command=canvas.yview)
        page_scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=page_scroll.set)
        main = ttk.Frame(canvas, padding=22)
        page = canvas.create_window((0, 0), window=main, anchor="nw")
        main.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(page, width=event.width))
        main.columnconfigure(0, weight=1)
        main.rowconfigure(5, weight=1)
        ttk.Label(main, text="Drum Velocity", style="Heading.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(main, text="Let your drum arrangement shape the feel of another MIDI part.", padding=(0, 3, 0, 14)).grid(row=1, column=0, sticky="w")

        files = ttk.Frame(main)
        files.grid(row=2, column=0, sticky="nsew")
        files.columnconfigure((0, 1), weight=1, uniform="files")
        for column, (kind, title) in enumerate((("source", "1  Drum reference"), ("target", "2  MIDI to shape"))):
            panel = ttk.LabelFrame(files, text=title, padding=12)
            panel.grid(row=0, column=column, sticky="nsew", padx=(0, 8) if column == 0 else (8, 0))
            panel.columnconfigure(0, weight=1)
            ttk.Entry(panel, textvariable=self.file_vars[kind], state="readonly").grid(row=0, column=0, sticky="ew", padx=(0, 6))
            self._add_control(ttk.Button(panel, text="Choose…", command=lambda k=kind: self._choose_file(k))).grid(row=0, column=1)
            ttk.Label(panel, textvariable=self.info_vars[kind], style="Hint.TLabel", wraplength=330).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 7))

            box_frame = ttk.Frame(panel)
            box_frame.grid(row=2, column=0, columnspan=2, sticky="ew")
            box_frame.columnconfigure(0, weight=1)
            box = tk.Listbox(box_frame, selectmode=tk.MULTIPLE, exportselection=False, height=5, background="white", foreground="#25334a", selectbackground="#315bdd", selectforeground="white", font=("Segoe UI", 10), relief="flat", highlightthickness=1, highlightbackground="#ccd4df", activestyle="none")
            box.grid(row=0, column=0, sticky="nsew")
            scroll = ttk.Scrollbar(box_frame, orient="vertical", command=box.yview)
            scroll.grid(row=0, column=1, sticky="ns")
            box.configure(yscrollcommand=scroll.set)
            self.listboxes[kind] = box
            self._add_control(box)
            choices = ttk.Frame(panel)
            choices.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))
            self._add_control(ttk.Button(choices, text="All tracks", command=lambda k=kind: self.listboxes[k].select_set(0, tk.END))).pack(side="left")
            self._add_control(ttk.Button(choices, text="Clear", command=lambda k=kind: self.listboxes[k].selection_clear(0, tk.END))).pack(side="left", padx=(5, 0))
            channel = self._add_control(ttk.Combobox(choices, textvariable=self.channel_vars[kind], values=("All",), width=5, state="readonly"))
            channel.pack(side="right")
            ttk.Label(choices, text="Channel", style="Hint.TLabel").pack(side="right", padx=(8, 5))
            self.channel_boxes[kind] = channel
            ttk.Label(panel, text="Click tracks to toggle them. Only selected tracks change." if kind == "target" else "Choose the drums that should drive the movement.", style="Hint.TLabel", wraplength=330).grid(row=4, column=0, columnspan=2, sticky="w", pady=(7, 0))

        controls = ttk.LabelFrame(main, text="3  Shape the movement", padding=12)
        controls.grid(row=3, column=0, sticky="ew", pady=(15, 0))
        controls.columnconfigure(1, weight=1)
        modes = ttk.Frame(controls)
        modes.grid(row=0, column=0, columnspan=4, sticky="w")
        self._add_control(ttk.Radiobutton(modes, text="Follow — drum hits make stronger notes", variable=self.mode_var, value="follow")).pack(side="left")
        self._add_control(ttk.Radiobutton(modes, text="Duck — drum hits make softer notes", variable=self.mode_var, value="duck")).pack(side="left", padx=(24, 0))

        ttk.Label(controls, text="Amount").grid(row=1, column=0, sticky="w", pady=(12, 0))
        slider = self._add_control(ttk.Scale(controls, from_=0, to=100, variable=self.amount_var, command=lambda v: self.amount_label.set(f"{float(v):.0f}%")))
        slider.grid(row=1, column=1, columnspan=2, sticky="ew", padx=12, pady=(12, 0))
        ttk.Label(controls, textvariable=self.amount_label, width=5).grid(row=1, column=3, sticky="e", pady=(12, 0))

        numbers = ttk.Frame(controls)
        numbers.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        fields = (("Soft limit", self.minimum_var, 1, 127, 1), ("Loud limit", self.maximum_var, 1, 127, 1), ("Accent fade (beats)", self.decay_var, 0, 16, 0.0625), ("Drum shift (beats)", self.shift_var, -64, 64, 0.0625), ("Snap tolerance", self.tolerance_var, 0, 1, 0.015625))
        for col, (label, var, start, end, step) in enumerate(fields):
            numbers.columnconfigure(col, weight=1)
            field = ttk.Frame(numbers)
            field.grid(row=0, column=col, sticky="ew", padx=(0, 14) if col < 4 else 0)
            ttk.Label(field, text=label, style="Hint.TLabel").pack(anchor="w")
            self._add_control(ttk.Spinbox(field, textvariable=var, from_=start, to=end, increment=step, width=10)).pack(fill="x", pady=(3, 0))
        self._add_control(ttk.Checkbutton(controls, text="Keep original velocity where there is no drum influence", variable=self.keep_var)).grid(row=3, column=0, columnspan=4, sticky="w", pady=(11, 0))
        ttk.Label(controls, text="Accent fade smooths the drop after each hit: 0.25 beats = a sixteenth note. Positive shift delays the drums.", style="Hint.TLabel").grid(row=4, column=0, columnspan=4, sticky="w", pady=(6, 0))

        output = ttk.Frame(main)
        output.grid(row=4, column=0, sticky="ew", pady=(14, 0))
        output.columnconfigure(1, weight=1)
        ttk.Label(output, text="Save as").grid(row=0, column=0, sticky="w", padx=(0, 10))
        self._add_control(ttk.Entry(output, textvariable=self.output_var)).grid(row=0, column=1, sticky="ew")
        self._add_control(ttk.Button(output, text="Browse…", command=self._choose_output)).grid(row=0, column=2, padx=(8, 0))

        details = ttk.Frame(main)
        details.grid(row=5, column=0, sticky="nsew", pady=(12, 0))
        details.columnconfigure(0, weight=1)
        details.rowconfigure(1, weight=1)
        ttk.Label(details, text="Full song, start to finish. No looping. Pitches, note lengths and timing stay in place.", style="Hint.TLabel").grid(row=0, column=0, sticky="w")
        self.result_text = tk.Text(details, height=4, wrap="word", font=("Segoe UI", 10), background="#e5eaf2", foreground="#33445e", relief="flat", padx=10, pady=8, state="disabled")
        self.result_text.grid(row=1, column=0, sticky="nsew", pady=(7, 0))
        self._set_result("Velocity is applied when each note starts. A held note does not pump between drum hits.\nBoth MIDI files must share the same song start; use Drum shift to adjust an offset.")

        footer = ttk.Frame(main)
        footer.grid(row=6, column=0, sticky="ew", pady=(12, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_var, style="Hint.TLabel", wraplength=640).grid(row=0, column=0, sticky="w")
        self._add_control(ttk.Button(footer, text="Make MIDI", style="Action.TButton", command=self._generate)).grid(row=0, column=1, padx=(12, 0))
        self.progress = ttk.Progressbar(main, mode="indeterminate", length=100)
        self.progress.grid(row=7, column=0, sticky="ew", pady=(10, 0))

    def _set_result(self, value: str) -> None:
        self.result_text.configure(state="normal")
        self.result_text.delete("1.0", tk.END)
        self.result_text.insert("1.0", value)
        self.result_text.configure(state="disabled")

    def _choose_file(self, kind: str) -> None:
        path = filedialog.askopenfilename(parent=self.root, title="Choose drum reference MIDI" if kind == "source" else "Choose MIDI to shape", filetypes=MIDI_TYPES)
        if path:
            self._start_job(("inspect", kind, path), lambda: inspect_midi(path), "Reading the full MIDI arrangement…")

    def _install_file(self, kind: str, path: str, info: dict) -> None:
        self.file_vars[kind].set(path)
        self.infos[kind] = info
        tracks = [track for track in info["tracks"] if track["note_count"] > 0]
        self.row_tracks[kind] = tracks
        box = self.listboxes[kind]
        box.delete(0, tk.END)
        for track in tracks:
            channels = ",".join(str(channel) for channel in track["channels"])
            name = str(track.get("name") or "Untitled track").replace("\n", " ").replace("\r", " ")
            box.insert(tk.END, f"Track {track['index']} · {name} · {track['note_count']:,} notes · ch {channels}")
        all_channels = sorted({ch for track in tracks for ch in track["channels"]})
        self.channel_boxes[kind].configure(values=("All", *(str(ch) for ch in all_channels)))
        self.channel_vars[kind].set("All")
        chosen = list(range(len(tracks)))
        hint = ""
        if kind == "source":
            drum_rows = [i for i, track in enumerate(tracks) if track.get("drum_note_count", 0) > 0]
            named_rows = [i for i, track in enumerate(tracks) if any(word in str(track.get("name", "")).lower() for word in ("drum", "kick", "snare", "hat", "percussion"))]
            if drum_rows:
                chosen = drum_rows
                self.channel_vars[kind].set("10")
                hint = " Channel 10 drums selected."
            elif named_rows:
                chosen = named_rows
                hint = " Drum names selected; check your tracks."
            elif tracks:
                hint = " No drum channel found; choose the drum tracks."
        for row in chosen:
            box.selection_set(row)
        self.info_vars[kind].set(f"{info['note_count']:,} notes · {info['length_beats']:g} beats · {len(tracks)} note tracks.{hint}")
        if kind == "target":
            self.output_var.set(str(self._next_output(Path(path))))
        warnings = info.get("warnings", [])
        self._set_result(f"Loaded {Path(path).name}." + ("\n" + "\n".join(warnings) if warnings else "\nTrack numbers match the MIDI's zero-based track indexes."))
        self.status_var.set("Ready. Choose the tracks you want and make your MIDI.")

    def _next_output(self, target: Path) -> Path:
        candidate = target.with_name(target.stem + "_drum_velocity.mid")
        counter = 2
        input_paths = {self._normalized(v.get()) for v in self.file_vars.values() if v.get()}
        while candidate.exists() or self._normalized(str(candidate)) in input_paths:
            candidate = target.with_name(target.stem + f"_drum_velocity_{counter}.mid")
            counter += 1
        return candidate

    @staticmethod
    def _normalized(path: str) -> str:
        return os.path.normcase(str(Path(path).expanduser().resolve()))

    def _choose_output(self) -> None:
        existing = self.output_var.get().strip()
        options = {"parent": self.root, "title": "Save shaped MIDI", "defaultextension": ".mid", "filetypes": MIDI_TYPES, "confirmoverwrite": False}
        if existing:
            options.update(initialdir=str(Path(existing).parent), initialfile=Path(existing).name)
        path = filedialog.asksaveasfilename(**options)
        if path:
            self.output_var.set(path)

    def _settings(self) -> Settings:
        tracks = {}
        channels = {}
        for kind in ("source", "target"):
            if kind not in self.infos:
                raise ValueError("Choose both a drum reference and a MIDI file to shape first.")
            selected = self.listboxes[kind].curselection()
            if not selected:
                raise ValueError("Select at least one drum reference track." if kind == "source" else "Select at least one track to shape.")
            tracks[kind] = tuple(self.row_tracks[kind][i]["index"] for i in selected)
            channel = self.channel_vars[kind].get()
            channels[kind] = None if channel == "All" else (int(channel),)
            if channels[kind] is not None and not any(channels[kind][0] in self.row_tracks[kind][i]["channels"] for i in selected):
                raise ValueError(f"None of the selected {'drum reference' if kind == 'source' else 'target'} tracks contain channel {channel}.")
        try:
            minimum = int(self.minimum_var.get())
            maximum = int(self.maximum_var.get())
        except ValueError as error:
            raise ValueError("Soft and loud limits must be whole numbers from 1 to 127.") from error
        if not 1 <= minimum <= maximum <= 127:
            raise ValueError("Use limits from 1 to 127, with the soft limit at or below the loud limit.")
        try:
            decay = float(self.decay_var.get())
            shift = float(self.shift_var.get())
            tolerance = float(self.tolerance_var.get())
            amount = float(self.amount_var.get()) / 100.0
        except (ValueError, tk.TclError) as error:
            raise ValueError("Accent fade, drum shift and snap tolerance must be numbers in quarter-note beats.") from error
        if not all(math.isfinite(value) for value in (decay, shift, tolerance, amount)):
            raise ValueError("Settings must be finite numbers.")
        if decay < 0 or tolerance < 0:
            raise ValueError("Accent fade and snap tolerance cannot be negative. Drum shift can be negative.")
        return Settings(mode=self.mode_var.get(), amount=amount, minimum=minimum, maximum=maximum, decay_beats=decay, tolerance_beats=tolerance, shift_beats=shift, source_tracks=tracks["source"], source_channels=channels["source"], target_tracks=tracks["target"], target_channels=channels["target"], unmatched="keep" if self.keep_var.get() else "floor")

    def _generate(self) -> None:
        try:
            settings = self._settings()
            raw_output = self.output_var.get().strip()
            if not raw_output:
                raise ValueError("Choose where to save the new MIDI file.")
            output = Path(raw_output).expanduser().resolve()
            if output.suffix.lower() not in (".mid", ".midi"):
                raise ValueError("The output filename must end in .mid or .midi.")
            if self._normalized(str(output)) in {self._normalized(v.get()) for v in self.file_vars.values()}:
                raise ValueError("Choose a different output filename. Your input MIDI files must stay intact.")
            if not output.parent.is_dir():
                raise ValueError("The output folder does not exist. Choose an existing folder.")
            if output.is_dir():
                raise ValueError("The output path points to a folder. Choose a MIDI filename.")
            overwrite = output.exists()
            if overwrite and not messagebox.askyesno("Replace existing output?", f"This output file already exists:\n\n{output}\n\nReplace it with the new shaped MIDI?", parent=self.root, default="no"):
                return
            source, target = self.file_vars["source"].get(), self.file_vars["target"].get()
        except (ValueError, OSError) as error:
            messagebox.showerror("Check your settings", str(error), parent=self.root)
            return
        self._start_job(("process", str(output)), lambda: process_files(source, target, output, settings=settings, overwrite=overwrite), "Shaping the entire song…")

    def _start_job(self, context: tuple, function, status: str) -> None:
        if self.busy:
            return
        self.busy = True
        self.saved_states = []
        for widget in self.interactive:
            old_state = str(widget.cget("state"))
            self.saved_states.append((widget, old_state))
            widget.configure(state="disabled")
        self.status_var.set(status)
        self.progress.start(12)

        def worker() -> None:
            try:
                result = function()
                self.jobs.put((context, result, None))
            except Exception as error:
                self.jobs.put((context, None, error))

        threading.Thread(target=worker, name="drum-velocity-worker", daemon=True).start()

    def _poll(self) -> None:
        if self.closed:
            return
        try:
            context, result, error = self.jobs.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.progress.stop()
            for widget, previous in self.saved_states:
                widget.configure(state=previous)
            self.saved_states.clear()
            if error is not None:
                self.status_var.set("Could not finish. Check the message and try again.")
                message = self._friendly_error(error)
                self._set_result(message)
                messagebox.showerror("Drum Velocity", message, parent=self.root)
            elif context[0] == "inspect":
                self._install_file(context[1], context[2], result)
            else:
                self.last_output = Path(context[1])
                lines = [f"Saved {self.last_output.name}", f"Changed {result['changed_notes']:,} of {result['target_notes']:,} selected notes. {result['matched_notes']:,} notes received drum influence from {result['source_hits']:,} source hits."]
                lines.extend(result.get("warnings", []))
                self._set_result("\n".join(lines))
                self.status_var.set(f"Done — {result['length_beats']:g} beats processed. Import the new MIDI into your project.")
        self.root.after(100, self._poll)

    @staticmethod
    def _friendly_error(error: Exception) -> str:
        if isinstance(error, FileNotFoundError):
            return f"A file or folder could not be found. It may have moved since you selected it.\n\n{error}"
        if isinstance(error, PermissionError):
            return f"The file could not be read or saved. Close any app locking it, or choose another output folder.\n\n{error}"
        return str(error) or f"The MIDI could not be processed ({type(error).__name__})."

    def _close(self) -> None:
        if self.busy:
            messagebox.showinfo("Work in progress", "Please wait until the current MIDI operation finishes before closing.", parent=self.root)
            return
        self.closed = True
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    DrumVelocityApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
