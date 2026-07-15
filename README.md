# PLY Sequence Importer

<video src="https://github.com/ardaevin/ply_sequencer/raw/main/media/PlySeqGitHUB.mp4" controls muted loop playsinline width="100%"></video>

> ▶️ If the player above doesn't load, [watch the demo video](media/PlySeqGitHUB.mp4).

A Blender addon that imports a folder of **3D Gaussian Splatting PLY files** as a frame-by-frame animated sequence.

Each PLY file becomes one frame of animation, shown via **render-safe visibility keyframes**. The sequence persists across save/reload with no runtime handler, renders correctly in animation output, automatically trims redundant data to reduce file size, and keeps the GaussianSplatting node tree organized.

---

## Requirements

- **Blender 5.1+** (tested on 5.1; compatible with 4.0+)
- **3D Gaussian Splatting addon** by ReshotAI — must be installed and enabled **first**  
  Download: https://github.com/ReshotAI/gaussian-splatting-blender-addon  
  (this provides the `bpy.ops.object.import_gaussian_splatting` operator that this addon wraps)

---

## Installation

> **Install the [ReshotAI Gaussian Splatting addon](https://github.com/ReshotAI/gaussian-splatting-blender-addon) first** — this addon depends on it and will refuse to import without it.

1. Download [`ply_sequence_importer.zip`](../../releases/latest) from the Releases page.
2. In Blender: **Edit > Preferences > Add-ons > Install**
3. Browse to the downloaded zip and click **Install Add-on**
4. Enable the checkbox next to **PLY Sequence Importer**

The panel appears in the **3D Viewport sidebar** under the **PLY Sequence** tab (`N` key to open).

---

## Usage

### Import a sequence

1. Open the **PLY Sequence** tab in the 3D Viewport sidebar.
2. Set **PLY Folder** to the folder containing your `.ply` files.
3. Set **Start Frame** (default: 1) — the timeline frame where the sequence begins.
4. Click **Import PLY Sequence**.

Blender will:
- Import each `.ply` via the 3DGS addon (converting raw attributes to the correct format)
- Share a single GN modifier and material across all frame objects (saves memory)
- Strip redundant mesh attributes to reduce `.blend` file size by ~40%
- Auto-arrange the GaussianSplatting GN node tree into a clean layout
- Create a `PLY_Sequence` empty and parent all frames to it
- Set the timeline range and start playback from the correct frame

### Move the sequence after import

If you want to shift the sequence to a different start frame after importing:

1. In the **PLY Sequence** panel, change the **Start Frame** value.
2. Click **Apply** next to it.

The sequence shifts to the new start frame and the timeline updates accordingly.

### Freeze on a single frame

To stop the sequence from playing frame-by-frame and hold one still frame:

1. **Select** the frame object you want to hold (optional — click it in the viewport or Outliner).
2. In the **PLY Sequence** panel, enable **Freeze Frame**.
   - If a frame object is selected, that frame is held.
   - Otherwise the frame currently shown on the timeline is held.
3. While frozen, drag the **Frozen Frame** slider to hold any other frame, or click
   **Set to Current Frame** to jump the held frame to the timeline position.
4. Uncheck **Freeze Frame** to resume normal playback.

While frozen, scrubbing or playing the timeline no longer changes the splat — the chosen
frame stays on screen. The freeze is **render-safe** and is saved in the `.blend`: a frozen
sequence renders (and reopens) frozen on the held frame. Internally it mutes the visibility
keyframes and pins the chosen object visible, so nothing is destroyed — unfreezing restores
the exact per-frame animation.

### Frame hold behavior

- **Before the sequence starts**: the first frame is held (no black frames)
- **After the sequence ends**: the last frame is held (no black frames)

### Playback persistence

The sequence survives file save and reload natively — visibility is stored as keyframes on the frame objects, so there is no runtime handler to re-register and nothing to break if the addon is later disabled.

---

## Features

| Feature | Detail |
|---|---|
| Frame-accurate playback | Driven by visibility keyframes (CONSTANT interpolation) |
| Render-safe | Renders correctly in animation output — no per-frame Python handler |
| Persistent across reloads | Keyframes live in the `.blend`; no handler to re-register |
| Shared GN + material | All frames share one node group and one material |
| Automatic size reduction | Removes `sh4–sh15`, `log_opacity`, `logscale`, `quatxyz`, `quatw` |
| GN auto-layout | Arranges GaussianSplatting nodes into a readable left-to-right flow |
| Frame hold | Clamps first/last frame outside the sequence range |
| Freeze frame | Hold any single frame; render-safe, reversible, saved in the `.blend` |
| Empty parent | All frame objects parented to `PLY_Sequence` for easy transforms |
| Post-import start frame | Move the whole sequence to any frame without reimporting |

---

## File Size Notes

3DGS PLY files contain high-order spherical harmonic coefficients (`sh4–sh15`) and intermediate conversion attributes that are not used for display. This addon removes them automatically on import:

- **Removed**: `sh4` – `sh15` (degree 2 & 3 SH bands), `log_opacity`, `logscale`, `quatxyz`, `quatw`
- **Kept**: `sh0` – `sh3` (degree 0 & 1 for view-dependent color), `opacity`, `scale`, `rot_euler`

Typical result: ~7 GB → ~4 GB for an 89-frame sequence.

> **Tip**: Also enable **Compress** in File > Save Preferences for additional savings.

---

## How It Works

```
PLY Folder
    └── frame_0000.ply
    └── frame_0001.ply
    └── ...
         ↓  bpy.ops.object.import_gaussian_splatting()
         ↓  Share GN node group + material
         ↓  Strip redundant attributes
         ↓  Parent to PLY_Sequence empty
         ↓  Keyframe hide_viewport / hide_render per object

Visibility keyframes (CONSTANT interp + extrapolation)
    → object i is visible only at frame (start + i)
    → first object holds visible before the sequence  (hold first)
    → last  object holds visible after  the sequence  (hold last)
    → evaluated natively by the depsgraph in viewport AND render
```

---

## Panel Reference

| Control | Description |
|---|---|
| PLY Folder | Directory containing `.ply` files (sorted alphabetically = frame order) |
| Start Frame | Timeline frame number where frame_0000 appears |
| Import PLY Sequence | Runs the full import pipeline |
| Active Sequence info | Shows frame count and current start frame |
| Move sequence to / Apply | Shifts the sequence to a new start frame without reimporting |
| Freeze Frame | Hold a single frame instead of playing the sequence |
| Frozen Frame | Which frame (0-based) to hold while frozen |
| Set to Current Frame | Jump the held frame to the current timeline position |

---

## Known Limitations

- Requires the **3D Gaussian Splatting** addon — this addon is a wrapper around it, not a standalone PLY importer.
- All `.ply` files in the folder are imported; there is no frame range filter.
- Only one sequence per scene is tracked at a time. Importing again overwrites the tracked sequence.

---

## Troubleshooting

### Blender crashes when rendering the animation (`EXCEPTION_ACCESS_VIOLATION`)

Sequences imported with **v1.5 or earlier** drove visibility from a `frame_change_post`
handler. Toggling visibility from that handler during a render triggers a collection /
depsgraph resync mid-frame and crashes Blender (the crash report points at
`graph_id_tag_update` during `RE_RenderAnim`).

**Fix:** update to **v1.6.0+** and bake the sequence to keyframes:

1. Install the latest `ply_sequence_importer.zip` and restart Blender.
2. Open your `.blend`, go to the **PLY Sequence** panel.
3. If a *“Legacy handler detected”* warning appears, click **Bake Render-Safe Keyframes**.
4. **Save** the file.

The animation will now render without crashing. New imports are render-safe automatically.

---

## Changelog

### v1.7.0
- **Freeze Frame.** New panel toggle to hold a single frame instead of playing the
  sequence — uses the selected frame object, or the current timeline frame. Includes a
  *Frozen Frame* slider and *Set to Current Frame* button. Render-safe, reversible, and
  saved in the `.blend` (mutes the visibility keyframes and pins one frame visible).

### v1.6.0
- **Render-safe visibility.** Visibility is now baked to `hide_viewport` / `hide_render`
  keyframes (CONSTANT interpolation) instead of a runtime `frame_change_post` handler,
  fixing a hard crash (`EXCEPTION_ACCESS_VIOLATION`) when rendering the animation.
- Removed the `@persistent` load handler — keyframes persist in the `.blend` natively.
- Added **Bake Render-Safe Keyframes** button to upgrade sequences imported with v1.5.
- *Move start frame* now re-keys the sequence at the new start.

### v1.5.0
- Auto-tidy the GaussianSplatting GN node tree on import.
- Strip redundant attributes (`sh4–sh15`, `log_opacity`, `logscale`, `quatxyz`, `quatw`).
- Post-import start-frame change; hold first/last frame; reload persistence.

---

## License

MIT License — see [LICENSE](LICENSE)
