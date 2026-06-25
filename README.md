# PLY Sequence Importer

A Blender addon that imports a folder of **3D Gaussian Splatting PLY files** as a frame-by-frame animated sequence.

Each PLY file becomes one frame of animation, displayed via Blender's visibility system — no keyframes needed. The sequence persists across save/reload, automatically trims redundant data to reduce file size, and keeps the GaussianSplatting node tree organized.

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

### Frame hold behavior

- **Before the sequence starts**: the first frame is held (no black frames)
- **After the sequence ends**: the last frame is held (no black frames)

### Playback persistence

The sequence survives file save and reload. The frame-change handler is automatically re-registered every time the `.blend` file is opened.

---

## Features

| Feature | Detail |
|---|---|
| Frame-accurate playback | Driven by Blender's `frame_change_post` handler |
| Persistent across reloads | Uses `@persistent` load-post handler + scene properties |
| Shared GN + material | All frames share one node group and one material |
| Automatic size reduction | Removes `sh4–sh15`, `log_opacity`, `logscale`, `quatxyz`, `quatw` |
| GN auto-layout | Arranges GaussianSplatting nodes into a readable left-to-right flow |
| Frame hold | Clamps first/last frame outside the sequence range |
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
         ↓  Hide all except current frame
         ↓  Parent to PLY_Sequence empty
         ↓  Store object list in scene properties (CSV)

frame_change_post handler
    → reads scene.ply_sequence_props.active_frame_objects
    → shows object[frame_current - start_frame]
    → hides all others
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

---

## Known Limitations

- Requires the **3D Gaussian Splatting** addon — this addon is a wrapper around it, not a standalone PLY importer.
- All `.ply` files in the folder are imported; there is no frame range filter.
- Only one sequence per scene is tracked at a time. Importing again overwrites the tracked sequence.

---

## License

MIT License — see [LICENSE](LICENSE)
