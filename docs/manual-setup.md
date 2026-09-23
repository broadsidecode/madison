# Manual setup and technical reference

A free local review screen for Tesseract users and their editing agents. The internal Python module remains `timeline_reviewer`; use the commands below as written.

Watch a rendered movie, inspect its video and audio lanes, mark a range, and copy a precise editing request. The review controls never move clips, change the soundtrack, or write to an editing project.

![Madison reviewing an episode with video preview, audio waveform and multiple timeline lanes](../images/viewer-demo.png)

## Start with the demo

For setup through an agent, use the [prompt on the front page](../README.md). These instructions are for manual setup.

Install Python 3.10 or newer, download this repository, and open a terminal in its folder:

```sh
python -m timeline_reviewer demo --open
```

On macOS or Linux, use `python3` if `python` is not available. Windows users can also run `start-review.cmd`; macOS and Linux users can run `bash start-review.sh`.

The bundled demo uses a synthetic test pattern and a quiet generated tone. It requires no editor, account, API key, Python package installation, or cloud service. Keep the terminal open while reviewing; press Ctrl+C to stop the server.

The local address is **http://127.0.0.1:8765/**. If that port is occupied, choose another explicitly:

```sh
python -m timeline_reviewer demo --port 8766 --open
```

## Review controls

- Resize the preview by dragging the divider. Double click it to reset.
- Zoom with the slider, buttons, or Ctrl/Cmd plus the mouse wheel.
- Pan with Shift plus the wheel or a middle mouse drag.
- Scrub by dragging the ruler, waveform, or playhead.
- Step through frames, jump between picture boundaries, enter a time, or change preview speed.
- Press **I** and **O** to mark a feedback range. Loop it while reviewing.
- Write a note and choose **Copy feedback**, then paste it into your editor or agent conversation.
- Keep inactive alternatives out of the main view with **Show parked takes**.

Notes and marked ranges live in the current page only. Copy them before closing or reloading. Frame stepping seeks according to the manifest frame rate; it is a review aid, not a frame accurate editing engine.

[Full controls guide](viewer-controls.md)

## Use your own footage

A review bundle contains `data.json`, one rendered preview movie, and optional thumbnails, poster and waveform samples. It does not need the original editing project.

To create a bundle from a movie, install FFmpeg and ffprobe separately and place them on PATH:

```sh
python -m timeline_reviewer prepare --video "my movie.mp4" --output "../my-review"
python -m timeline_reviewer serve "../my-review" --open
```

That creates one review clip covering the whole movie. To show individual lanes and cuts, supply a manifest or have your agent produce one using the [manifest format](manifest.md):

```sh
python -m timeline_reviewer prepare --video "my movie.mp4" --timeline "timeline.json" --output "../my-review"
```

`prepare` preserves the source. H.264 picture is copied when possible; other picture codecs are converted to H.264. Preview audio is encoded as AAC for browser compatibility. The preview is a separate review derivative, not a mastering export. Existing destination folders are refused.

You can serve a hand prepared bundle without FFmpeg:

```sh
python -m timeline_reviewer validate "../my-review"
python -m timeline_reviewer serve "../my-review"
```

To edit basic clips in an existing Tesseract document, include the native layer IDs and matching ranges in the review bundle, then bind the project when starting the viewer. See [editing instructions](editing.md) for the exact command and limits. Review only mode remains available without Tesseract.

Keep personal review bundles and media outside this repository. **Do not publish your own footage merely to use the viewer.**

## Optional experimental CapCut to Tesseract importer

This is a separate, opt-in feature. The viewer does not require either editor.

The importer reads one explicitly selected saved CapCut timeline and creates individual native layers in a **new** Tesseract project. It does not write back to CapCut or provide a round trip.

First inspect the saved project:

```sh
python -m timeline_reviewer inspect-capcut "/path/to/CapCut/project" --timeline "Timeline 01" --report "inspection.json"
```

Then, with Tesseract 0.1.0 installed separately on Windows or macOS:

```sh
python -m timeline_reviewer import-capcut "/path/to/CapCut/project" --timeline "Timeline 01" --output "../new-tesseract-import"
```

Unsupported active features stop the conversion by default. The optional `--allow-lossy` flag explicitly accepts reported omissions; it does not make the conversion faithful. Rendering is off by default and requires `--render`.

To check saved changes and start a versioned import from the browser instead, use the opt-in local server binding in the [CapCut sync guide](experimental-importer.md#optional-browser-sync). The separate **Reload viewer** button never imports. Keep the sync output folder outside both the source project and review bundle.

**Known boundaries:** proprietary color matching, complex effects, volume automation and fades, some mute/pitch settings, nested timelines and other unsupported features can require rebuilding. Tesseract 0.1.0 controls export size and frame rate automatically; native audio export has shown timing differences. Inspect and audition any imported draft against a reference export before using it as a final film.

[Importer setup and supported behavior](experimental-importer.md)

Tesseract is supplied by Mirage under its own terms. Its binaries, skills and vendor resources are not included here. Obtain it from the [official repository](https://github.com/mirage-hq/Tesseract) and review its [license terms](https://github.com/mirage-hq/Tesseract/blob/main/TERMS.md). CapCut and Tesseract are third party products; this project is independent and is not affiliated with their providers.

## Local access and privacy

The server binds only to IPv4 loopback. Review only mode exposes the application files and media referenced by a validated manifest and rejects writes. The optional editing mode accepts a small set of local clip operations, serves approved source video from selected local folders, and saves a new native project version. Optional CapCut sync binds one named local source when the server starts and creates versioned output without changing that source. None of these modes has an upload endpoint or analytics integration.

Other processes on your computer can access a running loopback server. Stop it when finished. Do not expose the port through a proxy or tunnel for confidential work. Copied feedback includes the manifest's clip labels and revision information; review it before sharing.

## Development and tests

The default Python runtime has no third party package dependencies.

```sh
python -m unittest discover -s tests -v
node --check timeline_reviewer/web/app.js
```

Importer unit tests use synthetic fixtures and a mocked editor. The FFmpeg preparation test skips when those optional tools are absent. A symlink test may skip where the operating system does not permit creating symlinks. See [verification notes](verification.md) for what has and has not been tested.

Generate another synthetic demo with `python scripts/make_demo.py --output "../fresh-demo"` and a separately installed FFmpeg. Choose a new folder; existing output is preserved. No production footage is used.

## License

MIT. See [LICENSE](../LICENSE). The license covers this repository's code and included synthetic demo; separately installed third party tools retain their own licenses.
