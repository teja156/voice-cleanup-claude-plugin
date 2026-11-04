---
name: clean-voiceover
description: Clean a raw voiceover recording by cutting out the outtakes, against its script or (if the human confirms) without one. Use when someone wants to clean, edit, or tidy a voiceover or narration recording (remove outtakes, retakes, false starts, "um"s, asides), or says "clean this voiceover", "cut the retakes out of my recording", "make a clean VO from these takes". Needs one or more audio files, and a script file if there is one.
argument-hint: "[--script <script.md>] --audio <file> [<file> …] [--out <project folder>]"
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

- **Script** (`.md` or `.txt`): what the speaker was reading. If the human gave none, **ask before going on**, for
  example: "There's no script. I can work out what you meant to say from the recording itself and cut the outtakes
  from that, but a script gives a more reliable result. Continue without a script?" Continue only on a clear yes.
- **Audio** (one or more files): the raw recording. Several files are joined in the order given. If the human gives a
  folder, list its audio files in natural file-name order and confirm the order if it isn't obvious.
- **Project folder** (optional): defaults to `voiceover-cleanup/<script name>/` next to the script (without a script: `voiceover-cleanup/<first audio file name>/` next to the audio).
- **Misheard names** (optional): product or person names the speech model may get wrong (e.g. "Zyntra" heard as "zentra").
- **Breaths:** breath sounds between words inside the kept speech are cut by default. Don't ask; pass "Breaths: keep"
  only if the human said to keep them.

Ask only for what is missing. Check that the files exist before starting the agent.

**First run:** the tool creates a Python environment and downloads the speech model (about 640 MB from Hugging Face) the
first time. Tell the human it needs internet once and takes a few minutes. It also needs `ffmpeg` and Python 3.10+;
if unsure, run `… scripts/vo setup` first (same prefix as above): it checks both and downloads the model.

## 2. Start the agent

Start the `voiceover-cleanup:voiceover-editor` agent with a prompt that gives the absolute paths of the script (or
"No script: the human confirmed working without one"), every audio file (in order), the project folder, any misheard names, "Breaths: keep" if the human asked to keep breaths, and any rule the human stated (for example "keep the
first take, not the last"). Also pass these two lines so it can run the tool:

```
Plugin root: ${CLAUDE_PLUGIN_ROOT}
Plugin data: ${CLAUDE_PLUGIN_DATA}
```

## 3. Review loop

1. Show the agent's report to the human as it is (without a script, point out the inferred script it names: anything
   wrong there is wrong in the cut), including the review page path (`<project>/review.html`) and the
   `vo serve <project>` option for remote machines (the page needs the server for seeking in long files when opened
   over a forwarded port).
2. If the human gives notes, send them to the **same** agent (resume it with SendMessage; don't start a new one), then
   show its new report.
3. After the first report, start the voiceover editor in the background from this session (a background process started
   by the agent may not outlive it), using the prefix above: `vo edit <project>`. It picks a free port and prints
   `VOICEOVER EDITOR: <url>`. Read that line from the command's output and give the human the exact URL, saying it's for
   adding back any words the cut missed. Start it only once: it picks up a re-applied `clean.wav`, so after a revision
   just tell the human to reload the page.
4. When the human approves, tell them the final file is `<project>/clean.wav`. If they rendered in the editor, it's `<project>/clean_edited.wav`.

Don't make cut decisions in this session, and don't edit `work/cuts.json` here: that's the agent's job.
