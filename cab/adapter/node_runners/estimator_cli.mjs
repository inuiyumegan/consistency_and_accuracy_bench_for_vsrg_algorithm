#!/usr/bin/env node
// CAB-VA estimator CLI bridge.
//
// Two modes:
//   1) One-shot:  node estimator_cli.mjs --algo <x> --file <osu_file>   -> prints a float
//   2) Server:    node estimator_cli.mjs --algo <x> --server
//      Reads newline-delimited JSON requests from stdin:
//        {"id": 1, "file": "/path/to/map.osu"}
//      and writes one JSON response line per request to stdout:
//        {"id": 1, "difficulty": 14.28, "status": "ok"}
//      Algorithm modules (and the treestar ONNX engine) are loaded once and
//      reused across requests, eliminating per-call startup cost.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const rootDir = path.resolve(__dirname, '../../..');

// Command line argument parser
const args = process.argv.slice(2);
let algo = 'mixed';
let filePath = null;
let serverMode = false;

for (let i = 0; i < args.length; i++) {
  if (args[i] === '--algo' || args[i] === '-a') {
    algo = args[++i];
  } else if (args[i] === '--file' || args[i] === '-f') {
    filePath = args[++i];
  } else if (args[i] === '--server') {
    serverMode = true;
  } else if (!args[i].startsWith('-') && !filePath) {
    filePath = args[i];
  }
}

// ---------------------------------------------------------------------------
// Persistent module cache: each algorithm module is imported only once.
// ---------------------------------------------------------------------------
const moduleCache = new Map();
async function loadModule(relPath) {
  const full = path.join(rootDir, relPath);
  if (!moduleCache.has(full)) {
    moduleCache.set(full, await import(full));
  }
  return moduleCache.get(full);
}

const MMA = 'test_algorithms/osumania_map_analyser/ManiaMapAnalyser by Leo_Black/js';
const OSU_STAR_WEB = 'test_algorithms/osu-star-web';

// Cached treestar engine (model loaded once in persistent mode).
let treestarEngine = null;

async function computeDifficulty(lowerAlgo, fileText, resolvedFilePath) {
  let diff = 0.0;

  if (lowerAlgo === 'mixed') {
    const { runMixedEstimatorFromText, applyCompanellaToMixedResult } = await loadModule(
      `${MMA}/estimator/mixedEstimator.js`
    );
    const { rcLabelToNumeric } = await loadModule(`${MMA}/estimator/rcDifficultyFormat.js`);
    let res = runMixedEstimatorFromText(fileText);
    if (res && res.mixedCompanellaPlan) {
      const { analyzeEtternaFromText } = await loadModule(`${MMA}/ett/calc.js`);
      const { runSunnyEstimatorFromText } = await loadModule(`${MMA}/estimator/sunnyEstimator.js`);
      const { calculateInterludeStar } = await loadModule(`${MMA}/interlude/index.js`);
      const { classifyCompanellaDifficulty } = await loadModule(`${MMA}/estimator/companellaEstimator.js`);

      const [ett, interludeStar, sunny] = await Promise.all([
        analyzeEtternaFromText(fileText, { etternaVersion: '0.70.0' }),
        calculateInterludeStar(fileText),
        runSunnyEstimatorFromText(fileText),
      ]);

      const comp = await classifyCompanellaDifficulty({
        msdValues: ett?.values,
        interludeStar,
        sunnyStar: Number(sunny?.star) || 0.0,
      });
      res = applyCompanellaToMixedResult(res, comp);
    }
    diff = res.numericDifficulty ?? rcLabelToNumeric(res.estDiff) ?? Number(res.star) ?? 0.0;
  } else if (lowerAlgo === 'minacalc' || lowerAlgo === 'msd' || lowerAlgo === 'etterna') {
    const { analyzeEtternaFromText } = await loadModule(`${MMA}/ett/calc.js`);
    const res = await analyzeEtternaFromText(fileText, { etternaVersion: '0.72.3' });
    diff = res.values?.Overall ?? 0.0;
  } else if (lowerAlgo === 'companella') {
    const { analyzeEtternaFromText } = await loadModule(`${MMA}/ett/calc.js`);
    const { runSunnyEstimatorFromText } = await loadModule(`${MMA}/estimator/sunnyEstimator.js`);
    const { calculateInterludeStar } = await loadModule(`${MMA}/interlude/index.js`);
    const { classifyCompanellaDifficulty } = await loadModule(`${MMA}/estimator/companellaEstimator.js`);

    const [ett, interludeStar, sunny] = await Promise.all([
      analyzeEtternaFromText(fileText, { etternaVersion: '0.70.0' }),
      calculateInterludeStar(fileText),
      runSunnyEstimatorFromText(fileText),
    ]);

    const comp = await classifyCompanellaDifficulty({
      msdValues: ett?.values,
      interludeStar,
      sunnyStar: Number(sunny?.star) || 0.0,
    });
    diff = comp.numericDifficulty ?? comp.rawModelOutput ?? 0.0;
  } else if (lowerAlgo === 'treestar' || lowerAlgo === 'osu-star-web') {
    const ort = await loadModule(`${OSU_STAR_WEB}/node_modules/onnxruntime-web/dist/ort.all.bundle.min.mjs`);
    if (!treestarEngine) {
      const { createEngine } = await loadModule(`${OSU_STAR_WEB}/docs/js/engine.js`);
      const docsPath = path.join(rootDir, OSU_STAR_WEB, 'docs');
      ort.env.wasm.numThreads = 1;
      treestarEngine = await createEngine({
        ort,
        loadBytes: async p => new Uint8Array(fs.readFileSync(path.join(docsPath, p))),
        loadJson: async p => JSON.parse(fs.readFileSync(path.join(docsPath, p), 'utf8')),
      });
    }
    const bytes = new Uint8Array(fs.readFileSync(resolvedFilePath));
    const res = await treestarEngine.scoreBytes(bytes, { withSegments: false });
    diff = res.star ?? 0.0;
  } else if (lowerAlgo === 'interlude' || lowerAlgo === 'yavsrg') {
    const { calculateInterludeStar } = await loadModule(`${MMA}/interlude/index.js`);
    diff = await calculateInterludeStar(fileText);
  } else {
    throw new Error(`Unknown algorithm: ${algo}`);
  }

  return Number(diff);
}

async function runServer() {
  const readline = await import('node:readline');
  const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  const lowerAlgo = algo.toLowerCase();

  for await (const line of rl) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    let req;
    try {
      req = JSON.parse(trimmed);
    } catch (e) {
      continue;
    }
    let resp;
    try {
      const resolved = path.resolve(req.file);
      const text = fs.readFileSync(resolved, 'utf8');
      const diff = await computeDifficulty(lowerAlgo, text, resolved);
      resp = { id: req.id, difficulty: diff, status: 'ok' };
    } catch (err) {
      resp = { id: req.id, status: 'error', error: String((err && err.message) || err) };
    }
    process.stdout.write(JSON.stringify(resp) + '\n');
  }
}

async function main() {
  if (serverMode) {
    await runServer();
    return;
  }

  if (!filePath) {
    console.error('Usage: node estimator_cli.mjs --algo <mixed|minacalc|companella|treestar|interlude> --file <osu_file> [--server]');
    process.exit(1);
  }

  const resolvedFile = path.resolve(filePath);
  if (!fs.existsSync(resolvedFile)) {
    console.error(`File not found: ${resolvedFile}`);
    process.exit(1);
  }

  const fileText = fs.readFileSync(resolvedFile, 'utf8');

  try {
    const diff = await computeDifficulty(algo.toLowerCase(), fileText, resolvedFile);
    // Print single float difficulty value to stdout
    console.log(Number(diff).toFixed(6));
  } catch (err) {
    console.error(`Calculation failed: ${err.message || err}`);
    process.exit(3);
  }
}

main();
