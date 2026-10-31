# SketchGapFinder (Fusion 360 add-in)

Finds small gaps in the sketch you're editing — the reason a profile won't close — and either jumps you to each one or fills them in.

## Install

1. Copy the `SketchGapFinder` folder to your add-ins folder:
   - Windows: `%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\`
   - macOS: `~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns/`
2. In Fusion: **Utilities → Add-Ins → Scripts and Add-Ins → Add-Ins tab**, select **SketchGapFinder**, click **Run** (tick *Run on Startup* if you like).

## Use

Edit a sketch, then **Sketch tab → Modify → Find Sketch Gaps**.

| Option | What it does |
|---|---|
| Gap tolerance | Anything at or below this distance is reported (default 0.5 mm). Changing it rescans live. |
| Include touching but unconnected | Ends that sit on top of each other (or on a curve) but aren't actually joined. |
| Include end-to-curve gaps | An open end that stops just short of another curve (T-junction gaps), not only end-to-end. |
| Previous / Next gap | Steps through the gaps. The current one is magenta, the rest red. |
| Zoom camera to gap | Moves the view to center on the current gap while stepping. |
| On OK | Fill all gaps, fill only the current gap, or just inspect. All fills are one undo step. |
| Method | **Bridge** adds a new line across the gap. **Merge** adds a coincident constraint so the existing geometry moves to close it (no new geometry). Zero-length gaps are always merged. |
| Bridge line style | How the new lines stand out (see below). |

### Why the fill lines aren't a custom color

Fusion's API doesn't allow setting a color on a sketch line, so the add-in uses Fusion's own display states:

- **Fixed (default)** — the bridge line is Fixed, which Fusion draws in green. Note that fixing it also pins the endpoints it connects to.
- **Centerline** — dash-dot. Verify the profile closes in your Fusion version before relying on it.
- **Normal** — looks like any other line.

Every bridge line is also tagged with an attribute (`SketchGapFinder` / `fill`), so it can be found again later by script.

## What counts as a gap

An endpoint is **open** if exactly one non-construction curve uses it and it isn't joined to anything else by a coincident or midpoint constraint. Open ends are paired with each other first (closest pairs win), then any leftover open end is checked against nearby curves. If the nearest spot on a curve is that curve's endpoint, the bridge connects to that vertex instead of the curve. Construction geometry is ignored.

## License

AGPL-3.0-or-later.
