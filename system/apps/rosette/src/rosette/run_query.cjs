// Reusable server-side DACTAL query executor (dactal-based skill).
//
// Execute one DACTAL query against an export, server-side, through the real DACTAL
// engine -- the same engine the browser front-end runs. This is how a DACTAL-based
// app *uses the exact algorithm it exposes*: a custom surface renders the result of
// running the saved query, never a reimplementation of it. Copy this into your lib
// (e.g. src/<pkg>/run_query.cjs) alongside a bundled engine copy (dactal_engine.js);
// have the Flask runner pull the saved query's exact text from the export and shell
// out to this. See references/use-what-you-expose.md.
//
// Usage: node run_query.cjs --engine <dactal.js> --export <export.json> --query "<dactal query>"
// Emits the result as a JSON array of rows on stdout.
//
// The .cjs extension is load-bearing: this is a CommonJS script, and a .js file is
// read as an ES module whenever some package.json above it declares "type": "module"
// (system/package.json, the workspace's npm root, does). .cjs pins CommonJS wherever
// the file is copied to. The engine beside it keeps its .js name (the browser and
// the app serve the same bytes), so it is read as a script rather than imported --
// see the engine load below.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

function arg(name) {
  const i = process.argv.indexOf(name);
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : null;
}

const enginePath = arg("--engine");
const exportPath = arg("--export");
const query = arg("--query");
if (!enginePath || !exportPath || query == null) {
  console.error('usage: node run_query.cjs --engine <dactal.js> --export <export.json> --query "<query>"');
  process.exit(2);
}

// The engine attaches its singleton to window.DACTAL; shim window to global so
// the same file that runs in the browser runs in Node. It is a plain browser
// script (no import/export, no require), so run it as one: `require` would make
// its module system depend on whatever package.json sits above the engine, which
// is exactly the trap the .cjs extension above avoids for this file.
global.window = global;
const engineFile = path.resolve(enginePath);
vm.runInThisContext(fs.readFileSync(engineFile, "utf8"), { filename: engineFile });
const DACTAL = global.window.DACTAL.constructor;

const exp = JSON.parse(fs.readFileSync(exportPath, "utf8"));
const data = exp.data || {};

// The engine narrates to console.log as it loads ("loading non-array into dataset rosette",
// which a packaged item always is). stdout here is the result and nothing else, so hold the
// narration aside until the result is written, then let anything later through again.
const narrate = console.log;
console.log = () => {};
function speakAgain() {
  console.log = narrate;
}

(async () => {
  const d = new DACTAL();
  for (const [name, rows] of Object.entries(data)) {
    if (rows != null) d.load(rows, name);
  }
  const result = await d.query(query);
  const rows = Array.isArray(result) ? result : result == null ? [] : [result];
  speakAgain();
  process.stdout.write(JSON.stringify(rows));
})().catch((e) => {
  speakAgain();
  console.error(String(e && e.message ? e.message : e));
  process.exit(1);
});
