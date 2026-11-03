# voiceover-cleanup

**Clean up your voiceover by cutting out the outtakes.** A Claude Code plugin: record yourself reading a script, flub
lines and start them again as often as you like, and get back a clean voiceover with one good take of every sentence.

Recording a voiceover in one go is hard. You stumble, restart the sentence, say "um", mutter "let me try that again",
and pause to read ahead. Cutting all of that out by hand means scrubbing through the whole recording. Instead, give
Claude the **raw recording(s)** and, ideally, the **script** you read from, and you get back:

- **`clean.wav`**: one take of every sentence, in script order, with even pauses between them;
- a **review page** where you can hear every kept line and every cut, each with the reason it was made;
- an **editor** for putting back any word the cut missed.

## How it works

1. **Transcribe.** A speech model runs on your machine and writes down every word you said, with timestamps, including
   the retakes and false starts.
2. **Find the takes.** Every transcribed word is matched to its place in the script. Each time you read through a
   sentence, that run of words is one **take** of it, so a sentence you restarted three times has three or four takes.
3. **Pick one take per sentence.** The default rule is **the last complete take wins**: because you restart a sentence
   when you get it wrong, the last time you read it all the way through is usually the one you meant to keep. A take
   counts as complete when it starts and ends within a couple of words of the sentence's start and end and has at least 70% of its words.
4. **Claude checks every pick.** The rule is only a draft. Claude's `voiceover-editor` agent reads the evidence for
   every sentence and makes the call:
   - It drops **false starts** stuck to the front of a take ("The best — the best way to…").
   - It removes **fillers** in the middle of a take ("um", "uh").
   - It drops **asides** and off-script talk ("okay, one more time").
   - It re-listens when a take looks partial, in case the speech model just missed some words.
   - If a sentence has no usable take, it says so, so you can re-record that line.
5. **Cut and join.** Only the chosen takes are kept, in script order. Cuts land in the pauses between words, not in the
   middle of one, and joins get a 12 ms crossfade, so there are no clicks. The gaps between sentences are filled with
   your own room tone: 0.45 s between sentences, 0.85 s between paragraphs and 1.1 s before a new section.
6. **You review.** Listen, give notes in plain words ("S4.P2.1: use the earlier take"), and the same agent revises the
   cut. When you approve, you're done.

Want a different rule? Tell Claude, for example "prefer my first take unless it has a mistake".

### No script? It still works

A script is the default and gives the most reliable result, but it's optional. If you don't give one, Claude **asks
first** whether to continue without it. If you say yes:

1. The recording is transcribed on its own.
2. Claude reads the whole transcript and works out what you meant to say: each sentence once, worded like your last
   complete reading of it, without the false starts, fillers and asides. It saves this as an **inferred script**
   (`work/inferred_script.md`).
3. From there everything runs as if you had given that script: takes are found, the last complete take wins, Claude
   checks every pick, and you review.

The report names the inferred script and anything Claude was unsure about (for example a remark that might be an aside
or might belong in the voiceover). Read it: anything wrong in the inferred script is wrong in the cut. If something is
off, say so ("keep the line about pricing") and the cut is redone from the corrected script.

**Good to know**

- **Local and private.** Speech recognition runs on your CPU. No audio leaves your machine.
- **Your recording, untouched.** The result is your own audio, cut and joined. No EQ, noise reduction or loudness
  changes. Your script and audio files are never modified.
- **English only.** The speech model is English. With a script, read it mostly as written: ad-libs that aren't in the
  script are treated as asides and dropped.

## Requirements

- Claude Code with plugin support
- Python 3.10 or newer (with `venv`; on Debian/Ubuntu: `apt install python3-venv`)
- `ffmpeg` and `ffprobe` on your `PATH` (`apt install ffmpeg` / `brew install ffmpeg`)
- About 1 GB of disk space, and internet access the **first** time (Python packages and the speech model)
- Linux, macOS, or Windows with Git Bash (the `vo` launcher is a bash script).

## Install

The plugin is published through [teja-claude-marketplace](https://github.com/teja156/teja-claude-marketplace).

**From GitHub:** inside Claude Code, run:

```
/plugin marketplace add teja156/teja-claude-marketplace
/plugin install voiceover-cleanup@teja-claude-marketplace
```

Shell equivalents: `claude plugin marketplace add teja156/teja-claude-marketplace` and
`claude plugin install voiceover-cleanup@teja-claude-marketplace`.

**From a local clone** (for development):

```
git clone https://github.com/teja156/voice-cleanup-claude-plugin.git ~/voiceover-cleanup
```

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

- **Script** (recommended; see [No script?](#no-script-it-still-works)): a Markdown (`.md`) or text (`.txt`) file with
  exactly what you read.
  - Headings (`#`, `##`, …) start sections. A new section gets a longer pause (1.1 s).
  - Blank lines separate paragraphs (0.85 s pause). Sentences in a paragraph get 0.45 s.
  - Not spoken, so ignored: YAML front matter, code blocks, HTML comments, tables, and lines that are only a bracketed
    tag such as `[SHOW: diagram]` or `[MUSIC]`.
- **Audio:** one or more files in any format ffmpeg reads (`.wav`, `.mp3`, `.m4a`, `.mp4`, `.mov`, `.flac`, …). Several
  files are joined in the order you give them, so you can record in parts.
- Record however you like: retakes, false starts, and asides are fine. The **last complete take** of each sentence wins
  by default, so if you stumble, just start the sentence again. Restart from the beginning of the sentence, not
  mid-sentence, and leave a short pause before you do: that makes the takes easy to tell apart.

### 2. Ask Claude

Use the skill:

```
/voiceover-cleanup:clean-voiceover --script ~/vo/intro.md --audio ~/vo/intro_take1.wav ~/vo/intro_take2.wav
```

Or just ask in plain words:

```
Clean my voiceover: the script is ~/vo/intro.md and the recording is ~/vo/intro.m4a
```

Without a script, leave it out (`Clean up the outtakes in ~/vo/intro.m4a`) and confirm when Claude asks.

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

Claude also starts the editor (step 5) and gives you its URL. Open `<project>/review.html` in a browser. It has:

- the clean voiceover, start to end;
- every kept sentence with Claude's reason, playable in the clean cut and in the original recording;
- everything that was dropped (other takes, false starts, fillers, asides), playable, with the reason.

On a remote machine, serve it and forward the port (seeking in long WAV files needs this server):

```
~/voiceover-cleanup/scripts/vo serve <project>        # prints the URL (port 8801, or the next free one)
```

### 4. Revise or approve

- **Changes:** tell Claude in plain words, for example "S4.P2.1: use the earlier take", "the pause after S6.P1.3 is too
  long", "keep the aside at 03:12". Claude passes the notes to the same agent, which updates the cut and reports again.
- **Approved:** the result is `<project>/clean.wav` (48 kHz, 24-bit, mono WAV). Nothing else needs to run.

Sentence ids are `S<section>.P<paragraph>.<sentence>`, for example `S4.P2.1` is the first sentence of the second
paragraph in section 4. The review page shows the id next to every line.

### 5. Fix missing words by hand (optional)

The speech model sometimes misses a word, so the cut leaves it out. Open the editor:

```
~/voiceover-cleanup/scripts/vo edit <project>         # prints the URL (port 8802, or the next free one)
```

When Claude cleans a recording it starts the editor for you and gives you the URL. It shows two timelines, the
**original** recording and the **clean** voiceover, with waveforms and the transcript under each. In the original,
green parts are in the cut, grey parts are other takes, and **suspects** are in pink: unused words right next to a
cut, the likeliest dropped ones (◂ ▸ jumps between them). Select words in the original (drag, or click a word and Shift+click to extend), then
drag the selection down onto the clean timeline where it belongs. Edges snap to pauses (hold Alt to place freely).
Clicking the clean timeline jumps the original to the same spot. Inserted clips can be moved, trimmed at their edges,
or deleted; Space plays the clean edit and Shift+Space the original.

Edits save to `work/edits.json` as you go. **Render** writes `<project>/clean_edited.wav` (and
`work/clean_edited_words.json`). `clean.wav` itself is never changed. If the cut is applied again, the saved edits no
longer match and the editor starts fresh.

You can also run the agent as your main session: `claude --agent voiceover-cleanup:voiceover-editor`.

## What's in a project folder

```
<project>/
├── script.md            copy of your script, or of the inferred one (sentence ids come from this copy)
├── clean.wav            THE RESULT
├── clean_edited.wav     clean.wav plus your hand edits (only if you rendered one in `vo edit`)
├── review.html          review page
└── work/
    ├── source.wav           all input audio, joined, 48 kHz mono (every time in the project refers to this file)
    ├── sources.json         where each input file starts in source.wav
    ├── transcript.raw.json  first transcription pass (never changed)
    ├── transcript.json      working transcript (re-transcribed ranges spliced in)
    ├── transcript.md        no script only: the transcript as timed lines, read to infer the script
    ├── inferred_script.md   no script only: the script Claude worked out from the recording
    ├── alignment.json       each transcribed word matched to a script position (or marked filler / extra)
    ├── audio_facts.json     silences, isolated sounds, loudness, noise floor
    ├── takes.md / .json     the prepared view: candidate takes per sentence, with evidence
    ├── cuts.draft.json      the rule's starting point
    ├── cuts.json            Claude's decisions (what was kept, removed, and dropped, with reasons)
    ├── clean_words.json     where every kept sentence and word landed in clean.wav
    ├── edits.json           the editor's clip list (after `vo edit`)
    └── clean_edited_words.json  word times in clean_edited.wav (after rendering in the editor)
```

## Under the hood

| Step | Who | What |
|---|---|---|
| 0. Infer the script (no script only) | script (`vo transcribe`), then **Claude** | Transcribes the recording and writes `transcript.md`; Claude writes `inferred_script.md` from it, and `vo prepare --reuse-transcript` continues from there without transcribing again. |
| 1. Prepare | script (`vo prepare`) | Converts and joins the audio, transcribes it with Parakeet (word timestamps and confidence), re-transcribes speech the first pass missed, matches every word to the script, and writes `takes.md`: every candidate take of every sentence, the default rule's pick, and evidence (speech blocks, pauses, stray sounds, asides). |
| 2. Decide | **Claude** (`voiceover-editor` agent) | Reads `takes.md` in full and writes `cuts.json`: which take, where it starts and ends, sounds to remove inside it, and a reason for each. Uses `vo probe` and `vo retranscribe` when the evidence is unclear. |
| 3. Apply | script (`vo apply`) | Cuts exactly what `cuts.json` says. Edges snap to the audio's own speech boundaries (not word timestamps, which can be off by a second around pauses), joins get 12 ms crossfades, and pauses are filled with your room tone. |
| 4. Review | you | Listen on the review page, give notes, repeat until approved. |
| 5. Touch up (optional) | you (`vo edit`) | Drag words the cut missed from the original back into the clean voiceover; Render writes `clean_edited.wav`. |

### Command-line tool

Everything Claude runs is available to you too (`scripts/vo --help`):

```
vo setup                                          check ffmpeg, download the speech model if missing
vo prepare  PROJECT --script FILE --audio FILE [FILE …] [--alias heard=script …] [--reuse-transcript]
vo transcribe PROJECT --audio FILE [FILE …]       no script: transcribe only → work/transcript.md
vo probe    PROJECT START END                     speech blocks and words in a time range of the raw audio
vo retranscribe PROJECT START END                 re-transcribe a range on its own, rebuild takes.md
vo draft    PROJECT                               cuts.draft.json from the rule "last complete take wins"
vo validate PROJECT                               check work/cuts.json
vo apply    PROJECT                               work/cuts.json → clean.wav + review.html
vo serve    PROJECT [--port N]                    serve the review page on 127.0.0.1
vo edit     PROJECT [--port N]                    voiceover editor: drag pieces of the original into the clean timeline
```

### Data folder

| What | Where |
|---|---|
| Python venv | `<data>/venv` (rebuilt automatically when `requirements.txt` changes) |
| Speech model | `<data>/models` (override with `VOICEOVER_CLEANUP_MODELS`) |

`<data>` is the plugin's data folder (`~/.claude/plugins/data/voiceover-cleanup-<marketplace>/`) when Claude runs the
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
- With a script, it expects you to read the script mostly as written: ad-libbed passages that aren't in the script are
  treated as asides and dropped.
- Without a script, Claude decides what counts as an aside and what belongs in the voiceover. Check the inferred script.
- Word end times are estimated by the model, so cuts are snapped to the audio's own silences. A very fast read with no
  pauses between sentences gives the snapping less room.

## License

The code in this repo is released under the [MIT License](LICENSE). The speech models are downloaded separately and
keep their own licenses (see above).
