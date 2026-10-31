# Sketch Gap Finder for Fusion 360

A Fusion 360 add-in that finds the tiny gaps that stop a sketch profile from closing. It can jump the camera to each gap one at a time, or fill the gaps with bridge lines that stand out from the rest of the sketch.

## Features

- **Finds near-miss endpoints** closer than a tolerance you set (default 0.5 mm). Changing the tolerance rescans live.
- **End-to-end and end-to-curve gaps.** It catches two loose ends that don't meet, and a line that stops just short of another curve.
- **Touching but unconnected ends.** It finds ends that sit on top of each other but were never actually joined.
- **On-screen markers.** Every gap gets a red circle that stays the same size at any zoom. The gap you're looking at is magenta.
- **Previous / Next navigation.** Steps through the gaps and can zoom the camera to each one.
- **Fill all, fill one, or just inspect.** Every fill from one run is a single undo step.
- **Two fill methods.** *Bridge* adds a new line across the gap. *Merge* adds a coincident constraint and pulls the existing ends together, with no new geometry.
- **Visible fill lines.** Bridge lines are made Fixed by default, so Fusion draws them in green. Each one is also tagged with an attribute so a script can find it later.
- Ignores construction geometry.

## Installation

### Windows (PowerShell)

```powershell
git clone https://github.com/ccatlett1984/Sketch_Gap_Filler_Fusion360.git
$addIns = Join-Path $env:APPDATA 'Autodesk\Autodesk Fusion 360\API\AddIns'
Copy-Item -Path '.\Sketch_Gap_Filler_Fusion360\SketchGapFinder' -Destination $addIns -Recurse -Force
```

### macOS

Copy the `SketchGapFinder` folder to:

```
~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns/
```

### Start the add-in

1. In Fusion, open **Utilities → Add-Ins → Scripts and Add-Ins**.
2. On the **Add-Ins** tab, select **SketchGapFinder** and click **Run**.
3. Tick **Run on Startup** to load it every time Fusion opens.

> You can skip the copy step: in **Scripts and Add-Ins**, click the green **+** next to *My Add-Ins* and point it at the `SketchGapFinder` folder inside your clone.

## Usage

1. Edit a sketch.
2. Go to **Sketch → Modify → Find Sketch Gaps**.
3. Set the tolerance and look at the gap count in the dialog.
4. Use **Previous gap / Next gap** to visit each one.
5. Choose what happens on **OK**, then click **OK**.

### Dialog options

| Option | What it does |
|---|---|
| Gap tolerance | Gaps at or below this distance are reported. |
| Include touching but unconnected | Reports ends that overlap (or sit on a curve) but aren't joined. |
| Include end-to-curve gaps | Reports open ends that stop short of another curve, not just other ends. |
| Previous gap / Next gap | Steps through the gaps found. |
| Zoom camera to gap | Centers and zooms the view on the current gap while stepping. |
| On OK | Fill all gaps, fill the current gap only, or make no changes. |
| Method | **Bridge with a line** or **Merge ends (coincident)**. Zero-length gaps are always merged. |
| Bridge line style | **Fixed** (green, default), **Centerline** (dash-dot), or **Normal**. |

## How gaps are detected

An endpoint is treated as **open** when exactly one non-construction curve uses it and no coincident or midpoint constraint joins it to anything else.

1. Open ends are paired with each other first, closest pairs winning.
2. Any open end left over is checked against nearby curves.
3. If the closest spot on that curve is the curve's own endpoint, the bridge connects to that vertex instead of to the middle of the curve.

## Limitations

- **No custom colors.** Fusion's API can't set the color of a sketch line, so the add-in relies on Fusion's own display states. Fixed lines show green, centerlines show dash-dot.
- **Fixed lines pin their neighbors.** A Fixed bridge line also locks the endpoints it connects to. Use the *Normal* style if that gets in your way.
- **Centerlines and profiles.** Check that a centerline bridge closes the profile in your Fusion version before relying on that style.
- **Merge moves geometry.** Merging pulls existing ends together, so the shape can shift slightly.
- **Over-constrained sketches.** A fill that conflicts with existing constraints is skipped, and the dialog reports how many failed.

## Repository layout

```
Sketch_Gap_Filler_Fusion360/
├── README.md                     ← this file
└── SketchGapFinder/              ← the add-in (copy this folder into AddIns)
    ├── SketchGapFinder.py
    ├── SketchGapFinder.manifest
    ├── README.md
    └── resources/                ← toolbar icons
```

## License

Licensed under the [GNU Affero General Public License v3.0 or later](https://www.gnu.org/licenses/agpl-3.0.html).
