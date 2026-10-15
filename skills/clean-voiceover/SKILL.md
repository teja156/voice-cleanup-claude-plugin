---
name: clean-voiceover
description: Clean a raw voiceover recording against its script. Use when someone wants to clean, edit, or tidy a voiceover or narration recording (remove retakes, false starts, "um"s, asides), or says "clean this voiceover", "cut the retakes out of my recording", "make a clean VO from these takes". Needs a script file and one or more audio files.
argument-hint: "--script <script.md> --audio <file> [<file> …] [--out <project folder>]"
allowed-tools: Bash(VOICEOVER_CLEANUP_HOME="${CLAUDE_PLUGIN_DATA}" "${CLAUDE_PLUGIN_ROOT}/scripts/vo" *)
---

# Clean a voiceover

Arguments (may be empty): $ARGUMENTS

The work is done by the **`voiceover-cleanup:voiceover-editor` agent**. This session gathers the inputs, starts the
agent, shows its report, and passes the human's notes back to the same agent until the human approves.

The command-line tool, for anything you run yourself in this session:

```
VOICEOVER_CLEANUP_HOME="${CLAUDE_PLUGIN_DATA}" "${CLAUDE_PLUGIN_ROOT}/scripts/vo" <command> …
```

## 1. Gather the inputs

- **Script** (`.md` or `.txt`): what the speaker was reading.
- **Audio** (one or more files): the raw recording. Several files are joined in the order given. If the human gives a
  folder, list its audio files in natural file-name order and confirm the order if it isn't obvious.
- **Project folder** (optional): defaults to `voiceover-cleanup/<script name>/` next to the script.
- **Misheard names** (optional): product or person names the speech model may get wrong (e.g. "Zyntra" heard as "zentra").

Ask only for what is missing. Check that the files exist before starting the agent.

**First run:** the tool creates a Python environment and downloads the speech model (about 640 MB from Hugging Face) the
first time. Tell the human it needs internet once and takes a few minutes. It also needs `ffmpeg` and Python 3.10+;
if unsure, run `… scripts/vo setup` first (same prefix as above): it checks both and downloads the model.

## 2. Start the agent

Start the `voiceover-cleanup:voiceover-editor` agent with a prompt that gives the absolute paths of the script, every
audio file (in order), the project folder, any misheard names, and any rule the human stated (for example "keep the
first take, not the last"). Also pass these two lines so it can run the tool:

```
Plugin root: ${CLAUDE_PLUGIN_ROOT}
Plugin data: ${CLAUDE_PLUGIN_DATA}
```

## 3. Review loop

1. Show the agent's report to the human as it is, including the review page path (`<project>/review.html`) and the
   `vo serve <project>` option for remote machines (the page needs the server for seeking in long files when opened
   over a forwarded port).
2. If the human gives notes, send them to the **same** agent (resume it with SendMessage; don't start a new one), then
   show its new report.
3. When the human approves, tell them the final file is `<project>/clean.wav`. Nothing else needs to run.

Don't make cut decisions in this session, and don't edit `work/cuts.json` here: that's the agent's job.
