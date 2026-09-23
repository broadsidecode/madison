# Set up Madison with Tesseract

This page is for the agent following the setup prompt on the [front page](README.md). Do the setup work for the user and keep the conversation simple.

## Ask two questions

1. Would you like a demo, a review of an existing Tesseract project, or a new edit from your own footage?
2. Where should the project and its review files live?

Infer answers already supplied. Inspect the operating system, architecture and installed tools yourself. Ask for a project or footage location only when needed. For a new edit, establish the desired result before making editorial changes.

## Prepare the tools

* Work locally with filesystem and process access. If your host cannot do that, explain the limitation and help the user continue in a suitable local agent. Do not substitute a hosted rendering service.
* Obtain https://github.com/broadsidecode/madison into a new folder, or use the user's existing checkout. Read AGENTS.md, command help and the [manual setup guide](docs/manual-setup.md). Preserve existing work. GitHub login is not required to download the public source archive.
* Detect an installed Tesseract engine and any Tesseract skills or plugin. Reuse a suitable installation. Otherwise follow Mirage's [official repository](https://github.com/mirage-hq/Tesseract) and [installation guide](https://github.com/mirage-hq/Tesseract/blob/main/skills/tesseract-video/references/installation.md). Use the official skills for the agent when available. Do not assume a marketplace plugin is available on every host. Skills provide instructions; the CLI engine is installed separately.
* Before downloading or installing Tesseract, present its [terms](https://github.com/mirage-hq/Tesseract/blob/main/TERMS.md) and let the user confirm agreement and eligibility. The terms include conditions for certain commercial businesses and commissioned work. Madison's MIT license does not replace those terms. Explain agent usage costs separately.
* Match the CLI version to the pin shipped with the official instructions. Verify the official checksum, host and architecture before installing. Do not silently replace an existing mismatched engine or guess a compatible version. The optional CapCut adapter in this release requires Tesseract 0.1.0.
* Tesseract supports macOS on Apple Silicon or Intel, and 64 bit Windows 10 or later on AMD64. Linux, WSL and cloud execution are not supported engine hosts. Madison alone can run on Linux if the user chooses that limited path.
* Follow the host's normal permission flow. Let the user handle required system security approval; never bypass Gatekeeper, SmartScreen or organizational controls.
* Check Python 3.10 or later for Madison. Install missing prerequisites through their official instructions. FFmpeg and ffprobe are optional for an existing review bundle but needed for movie preparation. Madison needs no runtime Python packages or Node.js. An optional skills installation method may have its own prerequisites.

## Prepare the review

Start the bundled synthetic demo to check Madison independently of the engine. Report viewer setup and engine setup separately. A working Madison demo does not prove that Tesseract is installed or can render.

For an existing Tesseract project, work from a copy and inspect its native editable data through the installed official CLI. For new footage, use the official Tesseract workflow after agreeing on the edit.

Madison can bind a .tsrct project for limited editing, but it still needs a rendered preview and matching lane metadata using the [bundle format](docs/manifest.md). Derive layer IDs, timing, source ranges, visibility and identities from the same native project revision as the render. Respect nested parent timing and retimes; do not present guessed timing as exact. Use the actual render duration and frame rate, and the rendered mix for the waveform. Follow the [editing guide](docs/editing.md) when the user wants to make edits in Madison.

The preparation command creates one full movie clip if no lane metadata is supplied. Explain that limitation when using it. Only call the result a detailed timeline review when matching lane data has been supplied and checked. CapCut sync is a separate optional local connection; ordinary reviews do not watch or import editor projects.

Keep generated reviews outside this repository and preserve source projects and media. Check picture and audio timing. Tesseract 0.1.0 has shown native audio source offset differences; a completed render does not prove an accurate soundtrack. Say so if audio cannot be auditioned.

Serve the bundle on an available port bound to 127.0.0.1. Respect the host's port registry, leave unrelated services running, and do not expose the viewer to the network. Open the full local URL through the host's permitted browser workflow.

## Show the workflow

Check playback, clip selection and seeking, zoom, divider resizing, range marking, looping and copied feedback. Inspect the rendered screen when browser tools are available. Otherwise report browser verification as pending and give the user a short manual check.

Explain the loop: the agent prepares a review; the user watches and makes simple draft edits in Madison or copies feedback; the agent handles complex revisions. Applying basic edits creates a new Tesseract project version, but the rendered movie and soundtrack remain pending until a fresh export. Madison refreshes the timeline in place when new review data arrives. Notes remain in the current browser session; copy any unsent feedback before closing it.

Finish with the working URL, project and review locations, simple reopening and shutdown instructions, what you checked, and any remaining limitation. Keep command dumps out of ordinary conversation.

## Keep CapCut optional

The normal review should show **Connect CapCut** with setup guidance. It is not an active import. Only use the [experimental importer and sync](docs/experimental-importer.md) when asked. Bind the exact saved CapCut project and named timeline once on the local server, then verify the page says **Sync from CapCut**. Explain that **Reload viewer** updates the page, while **Sync from CapCut** reads the saved edit and first shows a change preview. Import into a new destination, preserve the source and older review, and never enable lossy conversion without the user's explicit choice. Do not imply that editable transfers work in both directions.

Use only the demo and files the user provides. Do not upload media, publish projects, commit generated bundles or request unrelated account access during setup.
