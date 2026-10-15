# voiceover-cleanup

A Claude Code plugin that turns a raw voiceover recording into a clean voiceover that follows its script.

You record yourself reading a script. You restart sentences, say "um", say "let me try that again", and leave long
pauses. You give Claude the **script** and the **raw audio file(s)**. You get back **`clean.wav`**: one good take of every
sentence, in script order, with even pauses, plus a **review page** where you can hear every decision and why it was made.

- **Claude makes every editing decision.** The scripts only do mechanical work: transcribe, line up the words with the
  script, and cut the audio. Claude's `voiceover-editor` agent reads the evidence and picks each take.
- **You approve.** Give notes in plain words ("S4.P2.1: use the earlier take") and the same agent revises the cut.
- **Local.** Speech recognition runs on your CPU. No audio leaves your machine.
- **No processing.** The result is your recording, cut and joined with short crossfades and room-tone pauses. No EQ,
  noise reduction, or loudness change.

## Requirements

- Claude Code with plugin support
- Python 3.10 or newer (with `venv`; on Debian/Ubuntu: `apt install python3-venv`)
- `ffmpeg` and `ffprobe` on your `PATH` (`apt install ffmpeg` / `brew install ffmpeg`)
- About 1 GB of disk space, and internet access the **first** time (Python packages and the speech model)
- Linux or macOS. Windows is untested (use WSL).

## Install

The repo is both a plugin and a one-plugin marketplace.

**From a local clone:**

```
git clone <repo-url> ~/voiceover-cleanup
```

Then, inside Claude Code:

```
/plugin marketplace add ~/voiceover-cleanup
/plugin install voiceover-cleanup@voiceover-cleanup
```

**From GitHub** (once the repo is pushed):

```
/plugin marketplace add <owner>/voiceover-cleanup
/plugin install voiceover-cleanup@voiceover-cleanup
```

Shell equivalents: `claude plugin marketplace add <path-or-owner/repo>` and
`claude plugin install voiceover-cleanup@voiceover-cleanup`.

**Try it without installing:** `claude --plugin-dir ~/voiceover-cleanup`

Restart Claude Code (or run `/reload-plugins`) after installing. Check with `/plugin`: `voiceover-cleanup` should be listed.

### First run

The first run sets things up by itself:

1. It creates a Python virtual environment and installs the pinned packages from `requirements.txt`.
2. It downloads the speech model the first time it is needed (see [Speech model](#speech-model)).

Both go to the plugin's data folder, so plugin updates keep them. To do this ahead of time, ask Claude to
"run the voiceover-cleanup setup", or run it yourself:

```
VOICEOVER_CLEANUP_HOME=~/.local/share/voiceover-cleanup ~/voiceover-cleanup/scripts/vo setup
```

## Use

### 1. Prepare your files

- **Script:** a Markdown (`.md`) or text (`.txt`) file with exactly what you read.
  - Headings (`#`, `##`, …) start sections. A new section gets a longer pause (1.1 s).
  - Blank lines separate paragraphs (0.85 s pause). Sentences in a paragraph get 0.45 s.
  - Not spoken, so ignored: YAML front matter, code blocks, HTML comments, tables, and lines that are only a bracketed
    tag such as `[SHOW: diagram]` or `[MUSIC]`.
- **Audio:** one or more files in any format ffmpeg reads (`.wav`, `.mp3`, `.m4a`, `.mp4`, `.mov`, `.flac`, …). Several
  files are joined in the order you give them, so you can record in parts.
- Record however you like: retakes, false starts, and asides are fine. The **last complete take** of each sentence wins
  by default, so if you stumble, just start the sentence again.

### 2. Ask Claude

Use the skill:

```
/voiceover-cleanup:clean-voiceover --script ~/vo/intro.md --audio ~/vo/intro_take1.wav ~/vo/intro_take2.wav
```

Or just ask in plain words:

```
Clean my voiceover: the script is ~/vo/intro.md and the recording is ~/vo/intro.m4a
```

Optional things to tell Claude:

- **Where to put the project** (default: `voiceover-cleanup/<script name>/` next to the script).
- **Names the speech model may mishear**, for example "Zyntra is often heard as zentra". They are passed as
  `--alias zentra=zyntra` so the transcript still lines up with the script.
- **A different take rule**, for example "prefer my first take unless it has a mistake".

Claude starts the `voiceover-editor` agent. It transcribes the audio (about 9× faster than real time on a CPU, so
2–3 minutes for an 18-minute recording), decides every cut, builds `clean.wav`, and reports (illustrative numbers):

```
CLEAN VOICEOVER — intro
File: ~/vo/voiceover-cleanup/intro/clean.wav
Length: 4.2 min (from 6.0 min raw) · kept 48/48 sentences · dropped: 9 takes, 6 false starts, 2 fillers, 3 asides
Judgment calls (different from the rule draft):
- S1.P2.2: started at the second speech block; the first was a false start
Missing / needs re-recording: none
```

### 3. Review

Open `<project>/review.html` in a browser. It has:

- the clean voiceover, start to end;
- every kept sentence with Claude's reason, playable in the clean cut and in the original recording;
- everything that was dropped (other takes, false starts, fillers, asides), playable, with the reason.

On a remote machine, serve it and forward the port (seeking in long WAV files needs this server):

```
~/voiceover-cleanup/scripts/vo serve <project>        # http://127.0.0.1:8801/review.html
```

### 4. Revise or approve

- **Changes:** tell Claude in plain words, for example "S4.P2.1: use the earlier take", "the pause after S6.P1.3 is too
  long", "keep the aside at 03:12". Claude passes the notes to the same agent, which updates the cut and reports again.
- **Approved:** the result is `<project>/clean.wav` (48 kHz, 24-bit, mono WAV). Nothing else needs to run.

Sentence ids are `S<section>.P<paragraph>.<sentence>`, for example `S4.P2.1` is the first sentence of the second
paragraph in section 4. The review page shows the id next to every line.

You can also run the agent as your main session: `claude --agent voiceover-cleanup:voiceover-editor`.

## What's in a project folder

```
<project>/
├── script.md            copy of your script (sentence ids come from this copy)
├── clean.wav            THE RESULT
├── review.html          review page
└── work/
    ├── source.wav           all input audio, joined, 48 kHz mono (every time in the project refers to this file)
    ├── sources.json         where each input file starts in source.wav
    ├── transcript.raw.json  first transcription pass (never changed)
    ├── transcript.json      working transcript (re-transcribed ranges spliced in)
    ├── alignment.json       each transcribed word matched to a script position (or marked filler / extra)
    ├── audio_facts.json     silences, isolated sounds, loudness, noise floor
    ├── takes.md / .json     the prepared view: candidate takes per sentence, with evidence
    ├── cuts.draft.json      the rule's starting point
    ├── cuts.json            Claude's decisions (what was kept, removed, and dropped, with reasons)
    └── clean_words.json     where every kept sentence and word landed in clean.wav
```

## How it works

| Step | Who | What |
|---|---|---|
| 1. Prepare | script (`vo prepare`) | Converts and joins the audio, transcribes it with Parakeet (word timestamps and confidence), re-transcribes speech the first pass missed, matches every word to the script, and writes `takes.md`: every candidate take of every sentence, the default rule's pick, and evidence (speech blocks, pauses, stray sounds, asides). |
| 2. Decide | **Claude** (`voiceover-editor` agent) | Reads `takes.md` in full and writes `cuts.json`: which take, where it starts and ends, sounds to remove inside it, and a reason for each. Uses `vo probe` and `vo retranscribe` when the evidence is unclear. |
| 3. Apply | script (`vo apply`) | Cuts exactly what `cuts.json` says. Edges snap to the audio's own speech boundaries (not word timestamps, which can be off by a second around pauses), joins get 12 ms crossfades, and pauses are filled with your room tone. |
| 4. Review | you | Listen on the review page, give notes, repeat until approved. |

### Command-line tool

Everything Claude runs is available to you too (`scripts/vo --help`):

```
vo setup                                          check ffmpeg, download the speech model if missing
vo prepare  PROJECT --script FILE --audio FILE [FILE …] [--alias heard=script …]
vo probe    PROJECT START END                     speech blocks and words in a time range of the raw audio
vo retranscribe PROJECT START END                 re-transcribe a range on its own, rebuild takes.md
vo draft    PROJECT                               cuts.draft.json from the rule "last complete take wins"
vo validate PROJECT                               check work/cuts.json
vo apply    PROJECT                               work/cuts.json → clean.wav + review.html
vo serve    PROJECT [--port N]                    serve the review page on 127.0.0.1
```

### Data folder

| What | Where |
|---|---|
| Python venv | `<data>/venv` (rebuilt automatically when `requirements.txt` changes) |
| Speech model | `<data>/models` (override with `VOICEOVER_CLEANUP_MODELS`) |

`<data>` is the plugin's data folder (`~/.claude/plugins/data/voiceover-cleanup-voiceover-cleanup/`) when Claude runs the
tool, and `$VOICEOVER_CLEANUP_HOME` or `~/.local/share/voiceover-cleanup` when you run `scripts/vo` yourself. Set
`VOICEOVER_CLEANUP_HOME` to share one copy between both. Uninstalling the plugin deletes its data folder unless you pass
`--keep-data`.

Set `VOICEOVER_CLEANUP_PYTHON` to choose a specific Python interpreter for the venv.

## Speech model

The model is **not included** in this repo. The first time it is needed, it is downloaded from Hugging Face into the
models folder above, at pinned revisions, and loaded from there with no network access afterwards:

| Model | Hugging Face repo | Size | License |
|---|---|---|---|
| NVIDIA Parakeet TDT 0.6B v2 (English), ONNX int8 export | [`istupakov/parakeet-tdt-0.6b-v2-onnx`](https://huggingface.co/istupakov/parakeet-tdt-0.6b-v2-onnx) | ~630 MB | CC-BY-4.0 (NVIDIA) |
| Silero VAD (splits audio at pauses) | [`istupakov/silero-vad-onnx`](https://huggingface.co/istupakov/silero-vad-onnx) | ~6 MB | MIT (Silero) |

Check each model card for its current license terms before use. Parakeet is English only. It was chosen because it keeps
the repeated takes and false starts in a raw recording; Whisper-style models tend to skip them, which hides exactly the
evidence the cut needs.

## Troubleshooting

| Problem | Fix |
|---|---|
| `Python 3.10 or newer is required` | Install a newer Python, or set `VOICEOVER_CLEANUP_PYTHON=/path/to/python3.11`. |
| `could not create a venv` | `apt install python3-venv` (Debian/Ubuntu). |
| `ffmpeg not found on PATH` | Install ffmpeg. |
| Model download fails | Check internet access to `huggingface.co` (proxy settings: `HTTPS_PROXY`). The download is retried on the next run. |
| A product name is never matched | Tell Claude how it's misheard (look for it in `work/takes.md`), e.g. `--alias zentra=zyntra`, and prepare again. |
| A sentence is missing from the result | The agent lists it under "Missing / needs re-recording". Record that sentence, add the file as an extra `--audio`, and ask Claude to prepare again. |
| Seeking in the review page jumps to 0:00 | Open it through `vo serve` instead of a plain file server. |
| Start over | Delete the project folder (your script and audio are never changed). |

## Limits

- English only (the speech model).
- Expects you to read the script mostly as written. Ad-libbed passages that aren't in the script are treated as asides
  and dropped.
- Word end times are estimated by the model, so cuts are snapped to the audio's own silences. A very fast read with no
  pauses between sentences gives the snapping less room.

## License

The code in this repo: add a license before sharing it outside your organization. The speech models are downloaded
separately and keep their own licenses (see above).
