/* Rosette's lines, drawn as lines, on the Data app's query page.
 *
 * The query page shows a Rosette row the way it shows any record: a wide table
 * of columns, with the answer buried inside an `analysis` cell. That is the
 * right default for data, and the wrong one for a notebook line -- the thing
 * the row is a record OF is a phrase and what came back for it, side by side.
 *
 * So any query whose rows look like Rosette lines (`rosette.cleared`,
 * `rosette.notebook`, anything filtered or grouped out of them) is offered a
 * "rosette" display beside "raw data", and picking it draws each row with the
 * same formatter the notebook and the review page use.
 *
 * Loaded as a module by the Data app, which serves this app's assets from its
 * own origin, so the sibling import below resolves there.
 */
import "./row_format.js";

const STYLE_ID = "rosette-row-style";

// The fields that say a row is a Rosette line. All three must be on the first
// row of a result for the display to be offered, and all three are always
// written, so a line with no answer yet still counts as one.
const ROSETTE_FIELDS = ["row_id", "text", "status"];

function addStyleOnce() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = window.RosetteFormat.CSS +
    // The query page lays its list rows out itself; a Rosette line wants the
    // full width of one so its two columns can divide it.
    "\n.resultslistrow > .rosette-row { width: 100%; }\n";
  document.head.appendChild(style);
}

if (typeof add_renderer === "function" && window.RosetteFormat) {
  addStyleOnce();
  add_renderer(["rosette"], {
    offerfor: ROSETTE_FIELDS,
    list: (row) => window.RosetteFormat.element(row),
  });
}
