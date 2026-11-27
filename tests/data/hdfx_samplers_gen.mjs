// Golden values for test_hdfx_samplers.py, computed by PopcornFX-Web's own engine (MIT, @d22b8fe, src/engine).
// Usage: node hdfx_samplers_gen.mjs <PopcornFX-Web>/src/engine > hdfx_samplers_golden.json   (copy the folder first;
// nothing is installed). The same file runs live in the test when HDFX_POPCORNFX_ENGINE points at that folder.
import { pathToFileURL } from 'node:url';
import path from 'node:path';
const dir = path.resolve(process.argv[2]);
const { CurveSampler, ShapeSampler } = await import(pathToFileURL(path.join(dir, 'curves.js')));
const { TurbulenceRef } = await import(pathToFileURL(path.join(dir, 'turbulence.js')));
const f = Math.fround;
const sym = (s) => ({ sym: s });
const out = { curves: [], turbulence: [], shapes: [] };

const curveCases = [
  { name: 'hermite1', dim: 1, times: [0, 0.25, 1], values: [0, 1, 0.5], tan: [0, 0.4, 1, -0.2, 0.3, 0] },
  { name: 'hermite3', dim: 3, times: [0, 0.5, 1], values: [0, 0, 1, 1, 2, 0, 0.5, 1, 1],
    tan: [0, 0, 0, 0.1, 0.2, 0.3, 0.3, 0.2, 0.1, -0.2, 0, 0.1, 0, 0, 0, 0, 0, 0] },
  { name: 'linear2', dim: 2, times: [0, 0.3, 1], values: [0, 1, 2, 0, 1, 4], tan: [], linear: true },
  { name: 'limited', dim: 1, times: [0, 0.5, 1], values: [-1, 3, 0], tan: [0, 0, 4, 4, 0, 0], min: [0], max: [2] },
];
const ts = [-0.5, 0, 0.01, 0.1, 0.25, 0.3, 0.5, 0.77, 1, 1.5];
for (const c of curveCases) {
  const p = { Times: c.times, FloatValues: c.values, FloatTangents: c.tan, ValueType: sym('Float' + (c.dim > 1 ? c.dim : '')),
    Interpolator: sym(c.linear ? 'Linear' : 'Cubic') };
  if (c.min) p.MinLimits = c.min;
  if (c.max) p.MaxLimits = c.max;
  const s = new CurveSampler({ props: p });
  out.curves.push({ ...c, ts, out: ts.map((t) => s.sample([t])), integral: [0, 0.2, 0.9].map((b) => s.integral(0, b)) });
}

const grid = [];
for (let i = 0; i < 24; i++) grid.push([f(Math.sin(i * 1.7) * 4), f(Math.cos(i * 2.3) * 3), f(i * 0.37 - 4)]);
const turbCases = [
  { name: 'default', p: {} },
  { name: 'quintic', p: { Strength: 1, Wavelength: 1, Octaves: 3, Interpolator: 'Quintic', TimeScale: 0.4 } },
  { name: 'linear', p: { Strength: 0.7, Wavelength: 0.8, Octaves: 4, Lacunarity: 0.6, Gain: 0.7, Interpolator: 'Linear', TimeScale: 1 } },
  { name: 'flow', p: { Strength: 1, Wavelength: 2, FlowFactor: 1.5, DivergenceFactor: 0.4, TimeScale: 1, TimeBase: 0.3, TimeRandomVariation: 1 } },
  { name: 'fake', p: { Strength: 1, Wavelength: 1, FastFakeFlow: true, TimeScale: 0.5 } },
  { name: 'seeded_uniform', p: { Strength: 2, Wavelength: 1.5, InitialSeed: 12345, TimeRandomVariation: 0, TimeScale: 2, GlobalScale: 2 } },
  { name: 'unit_lac', p: { Strength: 1, Wavelength: 1, Octaves: 3, Lacunarity: 1, TimeScale: 0.2 } },
];
for (const c of turbCases) {
  const t = new TurbulenceRef(c.p);
  const rows = [];
  for (const time of [0, 0.37, 2.5]) {
    rows.push({ time, curl: grid.map((q) => t.sampleCurl(q, time)), potential: grid.map((q) => t.samplePotential(q, time)) });
  }
  out.turbulence.push({ ...c, grid, rows });
}

const shapeCases = [
  { name: 'box_surface', p: { ShapeType: sym('BOX'), BoxDimensions: [2, 1, 3] } },
  { name: 'box_volume', volume: true, p: { ShapeType: sym('BOX'), BoxDimensions: [2, 1, 3], Position: [1, 2, 3], EulerOrientation: [10, 20, 30] } },
  { name: 'sphere_shell', p: { ShapeType: sym('SPHERE'), Radius: 2, InnerRadius: 1 } },
  { name: 'sphere_volume', volume: true, p: { ShapeType: sym('SPHERE'), Radius: 2, InnerRadius: 1 } },
  { name: 'ellipsoid_hemi', p: { ShapeType: sym('COMPLEX_ELLIPSOID'), Radius: 1.5, NonUniformScale: [1, 2, 0.5], Hemisphere: true, Position: [0, 1, 0] } },
  { name: 'cylinder', p: { ShapeType: sym('CYLINDER'), Radius: 1.2, Height: 3, InnerRadius: 0.4, EulerOrientation: [90, 0, 0] } },
  { name: 'cylinder_volume', volume: true, p: { ShapeType: sym('CYLINDER'), Radius: 1.2, Height: 3, InnerRadius: 0.4 } },
  { name: 'capsule', p: { ShapeType: sym('CAPSULE'), Radius: 0.8, Height: 2.5 } },
  { name: 'capsule_volume', volume: true, p: { ShapeType: sym('CAPSULE'), Radius: 0.8, Height: 2.5 } },
  { name: 'cone', p: { ShapeType: sym('CONE'), Radius: 1, Height: 2 } },
  { name: 'cone_volume', volume: true, p: { ShapeType: sym('CONE'), Radius: 1, Height: 2 } },
];
const pcs = [];
for (let i = 0; i < 16; i++) pcs.push([((i * 0.6180339887) % 1), ((i * 0.7548776662) % 1), ((i * 0.5698402909 + 0.1) % 1)]);
for (const c of shapeCases) {
  const s = new ShapeSampler({ className: 'CShapeDescriptor', props: c.p }, () => 0.5, null);
  if (c.volume) s.volume = true;
  out.shapes.push({ ...c, pcs, pos: pcs.map((u) => s.samplePosition(u)), nrm: pcs.map((u) => s.sampleNormal(u)) });
}
process.stdout.write(JSON.stringify(out));
