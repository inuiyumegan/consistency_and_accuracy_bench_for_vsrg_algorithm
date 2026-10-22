#!/usr/bin/env node
// CAB-VA bridge: MinaCalc versions bundled with Leo_Black's ManiaMapAnalyser.
// Usage:
//   node minacalc_version_cli.mjs --version 0.72.3 --file <map.osu>
//   node minacalc_version_cli.mjs --server --version 0.72.3   (newline JSON protocol)
import { readFileSync } from "node:fs";
import { analyzeEtternaFromText } from "/home/inui/项目/todo/consistency_and_accuracy_bench_for_vsrg_algorithm/test_algorithms/osumania_map_analyser/ManiaMapAnalyser by Leo_Black/js/ett/calc.js";
import { ETTERNA_VERSION_KEYS } from "/home/inui/项目/todo/consistency_and_accuracy_bench_for_vsrg_algorithm/test_algorithms/osumania_map_analyser/ManiaMapAnalyser by Leo_Black/js/ett/constants.js";

const args = process.argv.slice(2);
function argOf(name, def) {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : def;
}
const version = argOf("--version", "0.72.3");
// An unknown version used to slip through: calc.js falls back to the loader's
// own path, which happily served the byte-identical 0.72.3 build for the
// retired "0.72.0" key.  Fail loudly instead of silently relabelling a run.
if (!ETTERNA_VERSION_KEYS.includes(version)) {
  console.error(
    "unknown --version " + JSON.stringify(version) + "; supported: " + ETTERNA_VERSION_KEYS.join(", "),
  );
  process.exit(2);
}
const file = argOf("--file", null);
const server = args.includes("--server");

async function calc(path) {
  const txt = readFileSync(path, "utf8");
  const res = await analyzeEtternaFromText(txt, { etternaVersion: version });
  return Number(res?.values?.Overall ?? 0);
}

if (server) {
  process.stdin.setEncoding("utf8");
  let buf = "";
  let queue = Promise.resolve();
  process.stdin.on("data", (chunk) => {
    buf += chunk;
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (!line) continue;
      let req;
      try { req = JSON.parse(line); } catch { continue; }
      queue = queue.then(async () => {
        try {
          const d = await calc(req.file);
          process.stdout.write(JSON.stringify({ id: req.id, difficulty: d, status: "ok" }) + "\n");
        } catch (e) {
          process.stdout.write(JSON.stringify({ id: req.id, status: "error", error: String(e?.message ?? e) }) + "\n");
        }
      });
    }
  });
} else {
  if (!file) { console.error("missing --file"); process.exit(2); }
  const d = await calc(file);
  process.stdout.write(String(d));
}
