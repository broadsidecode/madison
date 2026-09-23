# Edit clips in Madison

Madison can make a small set of real edits to a connected Tesseract project. Reviewing a movie alone still works without Tesseract and remains read only.

## Connect an editable project

Prepare a review bundle from the same Tesseract project revision as its rendered movie. Every clip you want to edit needs its native `layerId`, clip ID, picture or audio lane, source range and timeline range in the [bundle data](manifest.md). The agent preparing the bundle should check those values against a fresh Tesseract checkout. A movie with only one generic full length clip is useful for review, but does not provide the individual native clip references needed here.

Start the viewer with the separately installed Tesseract 0.1.0 engine and the editable document:

```sh
python -m timeline_reviewer serve "../my-review" --editable-project "../my-edit.tsrct" --open
```

For a quick source picture preview, add one `--media-root "../my-footage"` for each local source folder you choose to share with this loopback viewer. The original source path must also be present in the selected native layer's description. Madison does not expose source files from unapproved directories and does not upload them. A project that only has media inside its native archive can still accept structural edits; its new picture remains pending until a rendered movie is supplied.

The review bundle and the selected project's folder are available for local source preview by default. Additional folders require `--media-root`.

If the selected editor project or review data changes outside Madison during a draft, the timeline can refresh in place for review, but editing pauses. Start a new matching editing session with the refreshed project and review bundle. Stale edits are rejected instead of being applied to a different timeline.

## Make and apply a batch

Select a clip. Choose **Remove clip**, set a narrower start and end, or set its volume. The time boxes use seconds in the movie. **Undo**, **Redo** and **Restore** act on the queued draft. Changes to the draft save locally and the visible timeline updates immediately. Removing a clip leaves the time in place and can expose a lower picture layer; this is not a ripple delete. Complex speed changes, native animation and unsupported effects still need the editing agent.

Choose **Apply changes** once the batch looks right. Madison saves a new versioned editable Tesseract project beside the selected project. It keeps the previous project intact. The page reports where the new project was saved, and the agent can continue from that version.

The existing rendered movie is not silently presented as the edited picture. When an approved source file is available, Madison shows an approximate draft picture for the active clip. Tesseract effects and the changed soundtrack remain pending until a fresh render is prepared. Tesseract 0.1.0 can preview single frames, but has no native command for exporting only a short range. Save several simple edits together, then make the full final export when the cut is ready.

Draft operations and the current version reference are saved in a local private sidecar. They are never included in the public repository or served as review media. The edit connection is opt in and accepts changes only from the local Madison page. Other processes on the same computer can access loopback services, so use the same local privacy care as with ordinary review bundles.

## Keep your place and pop out the preview

**Refresh timeline** checks for updated review data in place. Madison also checks periodically and restores the playhead, zoom, scroll, selected clip when it still exists, divider height, marked range, notes and preview speed after a page reload. If the movie becomes shorter, the playhead moves to its new end.

On browsers supporting Document Picture in Picture, **Pop out preview** opens the same player with simple custom controls. This avoids the browser's dark native control overlay. Where that API is unavailable, Madison offers the browser's standard picture in picture if supported; that browser may shade the picture when its controls appear. The project and playback position stay together when the window closes.
