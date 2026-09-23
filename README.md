# Madison

![Madison / Tesseract by Mirage. Built for Tesseract.](images/madison-tesseract.png)

**A free review companion for Tesseract.**

Your AI agent edits the video in [Tesseract](https://github.com/mirage-hq/Tesseract), built by [Mirage](https://mirage.app/tesseract). Madison lets you review the timeline and make simple clip edits yourself.

## Get started

Copy this into Codex, Claude Code, or another coding agent that can work on your computer. You do not need to install Madison first.

```text
Help me set up Tesseract and Madison on my computer.

Get Madison from https://github.com/broadsidecode/madison
and read its START-HERE.md before doing the setup.

Ask whether I want to try a demo, use an existing Tesseract
project, or start with my own footage. Ask where to keep it.
Check my computer and installed tools yourself, then follow
the official Tesseract instructions for anything missing.
Explain any terms or system approval that needs my decision.

Do the setup work for me and open a review in my browser.
If I have an editable Tesseract project, connect it to
Madison so I can remove, trim and mute clips. Show me how
to review, edit, undo, and apply a batch of changes.

Keep my original files safe and my media local. Only use
the optional CapCut importer if I ask. Finish with the
review link and simple instructions for opening it again.
```

Tesseract needs a supported Windows or Mac computer. Your agent will check compatibility and guide you through setup.

## Review your edit

Pop out the video onto another screen and resize its window. View picture and audio lanes, zoom in, and scrub to a moment. With a connected Tesseract project, remove, trim or mute simple clips and apply the batch. Copy precise feedback for anything your agent should handle.

![Madison reviewing an episode with video preview, audio waveform and multiple timeline lanes](images/viewer-demo.png)

Madison saves your basic edits into a new Tesseract project version, preserving the previous version. Source clips can show a quick draft picture when their files are available. The final movie and soundtrack still need a fresh render before delivery. Copy any unsent notes before closing the page.

The normal demo shows **Connect CapCut** so you can see how to set it up. That button does not import a project by itself. If you use CapCut, your local agent can connect one saved project to Madison. The button then becomes **Sync from CapCut** and shows what changed and which effects or audio may differ before you choose to import. It builds a new local Tesseract version and keeps the previous review visible until the new movie is ready. **Reload viewer** only updates the browser view; it does not read CapCut. This optional sync remains experimental, so compare the result with your CapCut cut before delivery.

## Optional extras

* [Bring a CapCut project or set up local sync](docs/experimental-importer.md). Experimental; some effects and audio details need rebuilding. No transfer back to CapCut.
* [Edit clips in Madison](docs/editing.md), [review controls](docs/viewer-controls.md) and [manual setup](docs/manual-setup.md).
* [Technical details](docs/manifest.md) and [what we tested](docs/verification.md).

Madison can also review prepared previews from other editing tools. It is free under the [MIT license](LICENSE). This is an independent community project, not an official Mirage product. Tesseract has its own [terms](https://github.com/mirage-hq/Tesseract/blob/main/TERMS.md), and your AI agent's normal usage costs still apply.
