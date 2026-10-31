# SketchGapFinder - Fusion 360 add-in
# Finds small gaps between sketch curve endpoints (and between endpoints and
# nearby curves) while editing a sketch. Lets you step the camera through each
# gap, and optionally fills them with tagged, visually distinct bridge lines.
#
# Copyright (C) 2026 Chris Catlett
# SPDX-License-Identifier: AGPL-3.0-or-later

import adsk.core
import adsk.fusion
import os
import traceback

CMD_ID = 'ccatlett_SketchGapFinder_cmd'
CMD_NAME = 'Find Sketch Gaps'
CMD_TOOLTIP = ('Find small gaps between open sketch endpoints. Step through them '
               'one at a time, or fill them with bridge lines.')
PANEL_IDS = ['SketchModifyPanel', 'SolidScriptsAddinsPanel']
ATTR_GROUP = 'SketchGapFinder'
ATTR_NAME = 'fill'
EPS = 1e-6  # cm (internal units). Anything closer counts as "touching".

COLOR_ALL = (230, 40, 40)      # every gap marker
COLOR_CURRENT = (255, 0, 255)  # the gap you're looking at

ONOK_ALL = 'Fill all gaps'
ONOK_CURRENT = 'Fill current gap only'
ONOK_NONE = 'Just inspect (no changes)'

METHOD_BRIDGE = 'Bridge with a line'
METHOD_MERGE = 'Merge ends (coincident)'

STYLE_FIXED = 'Fixed line (shows green)'
STYLE_CENTER = 'Centerline (dash-dot)'
STYLE_NORMAL = 'Normal line'

_app = None
_ui = None
_handlers = []
_session = None


# --------------------------------------------------------------------------
# Geometry helpers (all sketch-space)
# --------------------------------------------------------------------------

def _endpoints(curve):
    pts = []
    for name in ('startSketchPoint', 'endSketchPoint'):
        try:
            p = getattr(curve, name)
        except Exception:
            p = None
        if p is not None:
            pts.append(p)
    return pts


def _closest_on_curve(geom, pt):
    """Closest point on a Curve3D (sketch space) to pt. Returns (Point3D, dist)."""
    if geom.objectType == adsk.core.Line3D.classType():
        a, b = geom.startPoint, geom.endPoint
        ab = a.vectorTo(b)
        L2 = ab.dotProduct(ab)
        t = 0.0 if L2 < 1e-18 else max(0.0, min(1.0, a.vectorTo(pt).dotProduct(ab) / L2))
        q = adsk.core.Point3D.create(a.x + ab.x * t, a.y + ab.y * t, a.z + ab.z * t)
        return q, q.distanceTo(pt)

    ev = geom.evaluator
    ok, t0, t1 = ev.getParameterExtents()
    if not ok:
        return None, float('inf')
    n = 64
    params = [t0 + (t1 - t0) * i / n for i in range(n + 1)]
    ok, pts = ev.getPointsAtParameters(params)
    if not ok:
        return None, float('inf')
    best = min(range(len(pts)), key=lambda i: pts[i].distanceTo(pt))
    lo = params[max(best - 1, 0)]
    hi = params[min(best + 1, n)]

    def d(t):
        _, p = ev.getPointAtParameter(t)
        return p.distanceTo(pt)

    for _ in range(50):  # ternary search, plenty for sub-micron
        m1 = lo + (hi - lo) / 3.0
        m2 = hi - (hi - lo) / 3.0
        if d(m1) < d(m2):
            hi = m2
        else:
            lo = m1
    _, q = ev.getPointAtParameter((lo + hi) / 2.0)
    return q, q.distanceTo(pt)


class Gap:
    def __init__(self, distance, ptA, posA, posB, ptB=None, curve=None):
        self.distance = distance
        self.ptA = ptA        # SketchPoint (open end)
        self.posA = posA      # Point3D sketch space
        self.posB = posB      # Point3D sketch space
        self.ptB = ptB        # SketchPoint, for end-to-end gaps
        self.curve = curve    # SketchCurve, for end-to-curve gaps

    @property
    def kind(self):
        return 'end-to-end' if self.ptB is not None else 'end-to-curve'


class SketchModel:
    """Topology of a sketch, built once per command session."""

    def __init__(self, sketch):
        self.sketch = sketch
        self.occ = sketch.assemblyContext
        self.native = sketch.nativeObject if self.occ else sketch

        self.curves = []      # (curve, token, endpoint tokens, geom, bbox min, bbox max)
        self.points = {}      # token -> SketchPoint
        deg = {}
        for c in sketch.sketchCurves:
            try:
                if c.isConstruction:
                    continue
            except Exception:
                pass
            eps_tok = []
            for p in _endpoints(c):
                t = p.entityToken
                self.points[t] = p
                deg[t] = deg.get(t, 0) + 1
                eps_tok.append(t)
            bb = c.boundingBox
            self.curves.append((c, c.entityToken, eps_tok, c.geometry, bb.minPoint, bb.maxPoint))
        construction = {c.entityToken for c in sketch.sketchCurves if c.isConstruction}

        # Union endpoints joined by coincident constraints; count endpoint-on-curve attachments.
        parent = {t: t for t in self.points}

        def find(t):
            while parent[t] != t:
                parent[t] = parent[parent[t]]
                t = parent[t]
            return t

        for gc in sketch.geometricConstraints:
            try:
                ot = gc.objectType
                if ot == adsk.fusion.CoincidentConstraint.classType():
                    p, e = gc.point, gc.entity
                    pt = p.entityToken
                    if e.objectType == adsk.fusion.SketchPoint.classType():
                        et = e.entityToken
                        if pt in parent and et in parent:
                            parent[find(pt)] = find(et)
                    elif pt in deg and e.entityToken not in construction:
                        deg[pt] += 1
                elif ot == adsk.fusion.MidPointConstraint.classType():
                    pt = gc.point.entityToken
                    if pt in deg and gc.midPointCurve.entityToken not in construction:
                        deg[pt] += 1
            except Exception:
                continue

        group_deg = {}
        for t in self.points:
            r = find(t)
            group_deg[r] = group_deg.get(r, 0) + deg[t]
        self.open = [t for t in self.points if group_deg[find(t)] == 1]

    def to_world(self, p):
        q = self.native.sketchToModelSpace(p)
        if self.occ:
            q.transformBy(self.occ.transform2)
        return q

    def find_gaps(self, tol, include_zero, include_curves):
        gaps = []
        used = set()
        opens = [(t, self.points[t], self.points[t].geometry) for t in self.open]

        # 1) end-to-end, greedy by shortest distance
        cands = []
        for i in range(len(opens)):
            for j in range(i + 1, len(opens)):
                d = opens[i][2].distanceTo(opens[j][2])
                if d <= tol and (include_zero or d >= EPS):
                    cands.append((d, i, j))
        cands.sort(key=lambda c: c[0])
        for d, i, j in cands:
            if i in used or j in used:
                continue
            used.add(i)
            used.add(j)
            gaps.append(Gap(d, opens[i][1], opens[i][2], opens[j][2], ptB=opens[j][1]))

        # 2) remaining open ends to nearby curves
        if include_curves:
            for i, (t, sp, pos) in enumerate(opens):
                if i in used:
                    continue
                best = None
                for c, ctok, ctoks, geom, mn, mx in self.curves:
                    if t in ctoks:
                        continue
                    if (pos.x < mn.x - tol or pos.x > mx.x + tol or
                            pos.y < mn.y - tol or pos.y > mx.y + tol):
                        continue
                    q, d = _closest_on_curve(geom, pos)
                    if q is None or d > tol or (not include_zero and d < EPS):
                        continue
                    if best is None or d < best[1]:
                        best = (c, d, q, ctoks)
                if best is None:
                    continue
                c, d, q, ctoks = best
                # If the closest spot is that curve's own endpoint, connect to the vertex instead.
                snap = None
                for et in ctoks:
                    ep = self.points[et]
                    if ep.geometry.distanceTo(q) < EPS:
                        snap = ep
                        break
                if snap is not None:
                    gaps.append(Gap(d, sp, pos, snap.geometry, ptB=snap))
                else:
                    gaps.append(Gap(d, sp, pos, q, curve=c))
                used.add(i)

        gaps.sort(key=lambda g: (round(g.posA.x, 4), round(g.posA.y, 4)))
        return gaps


# --------------------------------------------------------------------------
# Command session
# --------------------------------------------------------------------------

class Session:
    def __init__(self, sketch, inputs):
        self.design = adsk.fusion.Design.cast(_app.activeProduct)
        self.model = SketchModel(sketch)
        self.inputs = inputs
        self.gaps = []
        self.index = 0
        self.group = None

    def um(self):
        return self.design.unitsManager

    def fmt(self, v):
        um = self.um()
        return um.formatInternalValue(v, um.defaultLengthUnits, True)

    def rescan(self):
        tol = self.inputs.itemById('tol').value
        zero = self.inputs.itemById('zero').value
        curves = self.inputs.itemById('tocurve').value
        self.gaps = self.model.find_gaps(tol, zero, curves) if tol > 0 else []
        self.index = min(self.index, max(len(self.gaps) - 1, 0))
        self.redraw()
        self.update_status()

    def update_status(self):
        tol = self.inputs.itemById('tol').value
        box = self.inputs.itemById('status')
        if not self.gaps:
            box.formattedText = 'No gaps found at or below <b>{}</b>.'.format(self.fmt(tol))
            return
        g = self.gaps[self.index]
        box.formattedText = ('Found <b>{}</b> gap(s) at or below {}.<br>'
                             'Gap <b>{}</b> of {}: {} ({})').format(
            len(self.gaps), self.fmt(tol), self.index + 1, len(self.gaps),
            self.fmt(g.distance), g.kind)

    # ---- custom graphics -------------------------------------------------

    def clear_graphics(self):
        if self.group is not None:
            try:
                self.group.deleteMe()
            except Exception:
                pass
            self.group = None

    def _marker(self, center_sk, color, px, weight):
        # unit-radius circle in the sketch plane, scaled to a fixed pixel size
        import math
        coords = []
        for k in range(33):
            a = 2 * math.pi * k / 32
            p = adsk.core.Point3D.create(center_sk.x + math.cos(a), center_sk.y + math.sin(a), center_sk.z)
            w = self.model.to_world(p)
            coords += [w.x, w.y, w.z]
        ent = self.group.addLines(adsk.fusion.CustomGraphicsCoordinates.create(coords), [], True)
        ent.color = color
        ent.weight = weight
        ent.viewScale = adsk.fusion.CustomGraphicsViewScale.create(px, self.model.to_world(center_sk))
        self._front(ent)

    @staticmethod
    def _front(ent):
        try:
            ent.depthPriority = 10
        except Exception:
            pass
        try:
            ent.isSelectable = False
        except Exception:
            pass

    def redraw(self):
        self.clear_graphics()
        if not self.gaps:
            _app.activeViewport.refresh()
            return
        root = self.design.rootComponent
        self.group = root.customGraphicsGroups.add()
        c_all = adsk.fusion.CustomGraphicsSolidColorEffect.create(adsk.core.Color.create(*COLOR_ALL, 255))
        c_cur = adsk.fusion.CustomGraphicsSolidColorEffect.create(adsk.core.Color.create(*COLOR_CURRENT, 255))

        for i, g in enumerate(self.gaps):
            cur = (i == self.index)
            color = c_cur if cur else c_all
            mid = adsk.core.Point3D.create((g.posA.x + g.posB.x) / 2, (g.posA.y + g.posB.y) / 2,
                                           (g.posA.z + g.posB.z) / 2)
            self._marker(mid, color, 14 if cur else 8, 3 if cur else 2)
            if g.distance >= EPS:
                a, b = self.model.to_world(g.posA), self.model.to_world(g.posB)
                seg = self.group.addLines(
                    adsk.fusion.CustomGraphicsCoordinates.create([a.x, a.y, a.z, b.x, b.y, b.z]), [], False)
                seg.color = color
                seg.weight = 4 if cur else 3
                self._front(seg)
        _app.activeViewport.refresh()

    # ---- navigation ------------------------------------------------------

    def step(self, delta):
        if not self.gaps:
            return
        self.index = (self.index + delta) % len(self.gaps)
        self.redraw()
        self.update_status()
        if self.inputs.itemById('zoom').value:
            self.zoom_to(self.gaps[self.index])

    def zoom_to(self, g):
        vp = _app.activeViewport
        cam = vp.camera
        mid_sk = adsk.core.Point3D.create((g.posA.x + g.posB.x) / 2, (g.posA.y + g.posB.y) / 2,
                                          (g.posA.z + g.posB.z) / 2)
        mid = self.model.to_world(mid_sk)
        offset = cam.target.vectorTo(cam.eye)
        tol = self.inputs.itemById('tol').value
        extents = max(g.distance * 40, tol * 20, 0.2)
        if cam.cameraType == adsk.core.CameraTypes.PerspectiveCameraType:
            offset.normalize()
            offset.scaleBy(extents * 2)
        eye = mid.copy()
        eye.translateBy(offset)
        cam.target = mid
        cam.eye = eye
        cam.isFitView = False
        cam.viewExtents = extents
        cam.isSmoothTransition = True
        vp.camera = cam
        vp.refresh()

    # ---- filling ---------------------------------------------------------

    def fill(self):
        mode = self.inputs.itemById('onok').selectedItem.name
        if mode == ONOK_NONE or not self.gaps:
            return
        targets = self.gaps if mode == ONOK_ALL else [self.gaps[self.index]]
        method = self.inputs.itemById('method').selectedItem.name
        style = self.inputs.itemById('style').selectedItem.name

        sk = self.model.sketch
        lines = sk.sketchCurves.sketchLines
        gcs = sk.geometricConstraints
        bridged, merged, failed = 0, 0, 0

        for g in targets:
            try:
                merge = method == METHOD_MERGE or g.distance < EPS
                if merge:
                    gcs.addCoincident(g.ptA, g.ptB if g.ptB is not None else g.curve)
                    merged += 1
                    continue
                if g.ptB is not None:
                    ln = lines.addByTwoPoints(g.ptA, g.ptB)
                else:
                    ln = lines.addByTwoPoints(g.ptA, g.posB)
                    gcs.addCoincident(ln.endSketchPoint, g.curve)
                ln.attributes.add(ATTR_GROUP, ATTR_NAME, '1')
                if style == STYLE_FIXED:
                    ln.isFixed = True
                elif style == STYLE_CENTER:
                    ln.isCenterLine = True
                bridged += 1
            except Exception:
                failed += 1

        if failed:
            _ui.messageBox('Bridged {}, merged {}, failed {} (usually over-constrained geometry).'
                           .format(bridged, merged, failed), CMD_NAME)


# --------------------------------------------------------------------------
# Event handlers
# --------------------------------------------------------------------------

class CreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        global _session
        try:
            cmd = args.command
            inputs = cmd.commandInputs
            sketch = adsk.fusion.Sketch.cast(_app.activeEditObject)
            if sketch is None:
                inputs.addTextBoxCommandInput('msg', '', 'Open a sketch for editing first.', 2, True)
                cmd.isOKButtonVisible = False
                return

            um = _app.activeProduct.unitsManager
            inputs.addValueInput('tol', 'Gap tolerance', um.defaultLengthUnits,
                                 adsk.core.ValueInput.createByString('0.5 mm'))
            inputs.addBoolValueInput('zero', 'Include touching but unconnected', True, '', True)
            inputs.addBoolValueInput('tocurve', 'Include end-to-curve gaps', True, '', True)
            inputs.addTextBoxCommandInput('status', '', '', 2, True)

            nav = inputs.addGroupCommandInput('nav', 'Jump to gap')
            nav.children.addBoolValueInput('prev', 'Previous gap', False, '', False)
            nav.children.addBoolValueInput('next', 'Next gap', False, '', False)
            nav.children.addBoolValueInput('zoom', 'Zoom camera to gap', True, '', True)

            fill = inputs.addGroupCommandInput('fillgrp', 'Fill')
            onok = fill.children.addDropDownCommandInput(
                'onok', 'On OK', adsk.core.DropDownStyles.TextListDropDownStyle)
            onok.listItems.add(ONOK_ALL, True)
            onok.listItems.add(ONOK_CURRENT, False)
            onok.listItems.add(ONOK_NONE, False)
            meth = fill.children.addDropDownCommandInput(
                'method', 'Method', adsk.core.DropDownStyles.TextListDropDownStyle)
            meth.listItems.add(METHOD_BRIDGE, True)
            meth.listItems.add(METHOD_MERGE, False)
            meth.tooltip = 'Merge moves the existing endpoints together; it adds no new geometry.'
            sty = fill.children.addDropDownCommandInput(
                'style', 'Bridge line style', adsk.core.DropDownStyles.TextListDropDownStyle)
            sty.listItems.add(STYLE_FIXED, True)
            sty.listItems.add(STYLE_CENTER, False)
            sty.listItems.add(STYLE_NORMAL, False)
            sty.tooltip = ('Fusion does not allow custom sketch line colors through the API. '
                           'Fixed lines display green; centerlines display dash-dot.')

            _session = Session(sketch, inputs)
            _session.rescan()

            for evt, h in ((cmd.inputChanged, InputChangedHandler()),
                           (cmd.validateInputs, ValidateHandler()),
                           (cmd.execute, ExecuteHandler()),
                           (cmd.destroy, DestroyHandler())):
                evt.add(h)
                _handlers.append(h)
        except Exception:
            _ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))


class InputChangedHandler(adsk.core.InputChangedEventHandler):
    def notify(self, args):
        try:
            if _session is None:
                return
            cid = args.input.id
            if cid in ('tol', 'zero', 'tocurve'):
                _session.rescan()
            elif cid == 'next':
                _session.step(1)
            elif cid == 'prev':
                _session.step(-1)
            elif cid == 'method':
                merge = args.input.selectedItem.name == METHOD_MERGE
                _session.inputs.itemById('style').isEnabled = not merge
        except Exception:
            _ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))


class ValidateHandler(adsk.core.ValidateInputsEventHandler):
    def notify(self, args):
        tol = args.inputs.itemById('tol')
        args.areInputsValid = tol is None or tol.value > 0


class ExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            if _session is None:
                return
            _session.clear_graphics()
            _session.fill()
        except Exception:
            _ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))


class DestroyHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        global _session
        try:
            if _session is not None:
                _session.clear_graphics()
                _app.activeViewport.refresh()
        finally:
            _session = None
            # keep the CreatedHandler; drop the per-command ones
            del _handlers[1:]


# --------------------------------------------------------------------------
# Add-in entry points
# --------------------------------------------------------------------------

def run(context):
    global _app, _ui
    try:
        _app = adsk.core.Application.get()
        _ui = _app.userInterface

        old = _ui.commandDefinitions.itemById(CMD_ID)
        if old:
            old.deleteMe()
        res = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources')
        cmd_def = _ui.commandDefinitions.addButtonDefinition(
            CMD_ID, CMD_NAME, CMD_TOOLTIP, res if os.path.isdir(res) else '')
        h = CreatedHandler()
        cmd_def.commandCreated.add(h)
        _handlers.append(h)

        for pid in PANEL_IDS:
            panel = _ui.allToolbarPanels.itemById(pid)
            if panel:
                if not panel.controls.itemById(CMD_ID):
                    panel.controls.addCommand(cmd_def)
                break
    except Exception:
        if _ui:
            _ui.messageBox('Failed to start {}:\n{}'.format(CMD_NAME, traceback.format_exc()))


def stop(context):
    try:
        for pid in PANEL_IDS:
            panel = _ui.allToolbarPanels.itemById(pid)
            if panel:
                ctrl = panel.controls.itemById(CMD_ID)
                if ctrl:
                    ctrl.deleteMe()
        cmd_def = _ui.commandDefinitions.itemById(CMD_ID)
        if cmd_def:
            cmd_def.deleteMe()
        _handlers.clear()
    except Exception:
        if _ui:
            _ui.messageBox('Failed to stop {}:\n{}'.format(CMD_NAME, traceback.format_exc()))
