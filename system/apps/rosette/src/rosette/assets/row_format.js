/* One Rosette line, drawn the way the notebook draws it.
 *
 * A line the student wrote and the answer that came back are shown in three
 * places now: the notebook, the review page, and -- through the renderer in
 * dactal_render.js -- the Data app's query page. They are the same thing in all
 * three, so they are drawn here once rather than three times over.
 *
 * Nothing in here touches the network or the page it is on. It takes a row as
 * the notebook stores it (`row_id`, `text`, `status`, `analysis`, ...) and
 * returns markup. A host that already styles Rosette's classes -- the notebook
 * and the review page do -- uses the markup as it is; a host that does not can
 * add `RosetteFormat.CSS`, which styles the same classes inside `.rosette-row`
 * and nowhere else.
 */
(function (root) {
  "use strict";

  // A value read back out of a DACTAL query may arrive wrapped in a list of
  // one, because that is how the query language carries a property it followed.
  // Everything here reads a plain value, so unwrap it at the door.
  function one(value) {
    return Array.isArray(value) ? value[0] : value;
  }

  function escapeText(value) {
    const holder = document.createElement("span");
    holder.textContent = value === null || value === undefined ? "" : value;
    return holder.innerHTML;
  }

  function countLabel(count, singular, plural) {
    return count + " " + (count === 1 ? singular : plural);
  }

  /** The corrected line, run by run: what was wrong struck through, the fix beside it. */
  function segmentsHtml(segments) {
    return segments.map((raw, index) => {
      const segment = one(raw) || {};
      const kind = one(segment.kind);
      const text = one(segment.text);
      if (kind === "wrong") return '<span class="wrong">' + escapeText(text) + "</span>";
      if (kind === "right") {
        // A replacement lands straight after the text it replaces, so it needs
        // a real space to read -- and to copy -- as a separate word.
        const previous = one(segments[index - 1]) || {};
        const isAfterWrong = index > 0 && one(previous.kind) === "wrong";
        return (isAfterWrong ? " " : "") + '<span class="right">' + escapeText(text) + "</span>";
      }
      return escapeText(text);
    }).join("");
  }

  /** What came back for one line: the meaning, the phrase, the reply, or the marking -- and the note under it.
   *
   * Returns "" when there is nothing to show, so a caller can tell an answer
   * apart from an empty one without inspecting the markup.
   */
  function answerHtml(rawAnalysis) {
    const analysis = one(rawAnalysis);
    if (!analysis || typeof analysis !== "object") return "";

    const mode = one(analysis.mode);
    const note = one(analysis.note);
    const parts = [];

    if (mode === "answer" && one(analysis.answer)) {
      parts.push('<p class="reply">' + escapeText(one(analysis.answer)) + "</p>");
    } else if (mode === "translate" && one(analysis.italian)) {
      parts.push('<p class="italian">' + escapeText(one(analysis.italian)) + "</p>");
    } else if (mode === "gloss" && one(analysis.english)) {
      parts.push('<p class="english">' + escapeText(one(analysis.english)) + "</p>");
    } else if (mode === "assess") {
      const verdict = one(analysis.verdict);
      const segments = Array.isArray(analysis.segments) ? analysis.segments : [];
      if (verdict === "correct") {
        parts.push('<p class="verdict ok">All correct.</p>');
      } else if (verdict === null || verdict === undefined) {
        // The tutor had something to say but could not mark it on the line, so
        // the note below stands on its own rather than under a wrong verdict.
      } else {
        const fixCount = segments.filter(s => one((one(s) || {}).kind) === "right").length;
        parts.push('<p class="verdict">' + countLabel(fixCount, "fix", "fixes") + ".</p>");
        parts.push('<p class="italian">' + segmentsHtml(segments) + "</p>");
      }
    }

    if (note) parts.push('<p class="note">' + escapeText(note) + "</p>");
    return parts.join("");
  }

  /** Which of the four things was asked of a line: "gloss", "translate", "assess", "answer", or "". */
  function modeOf(row) {
    const analysis = one((one(row) || {}).analysis);
    return (analysis && one(analysis.mode)) || "";
  }

  /** A line as the student wrote it, beside what came back for it. */
  function rowHtml(rawRow) {
    const row = one(rawRow) || {};
    const text = one(row.text);
    const mode = modeOf(row);
    const answer = answerHtml(row.analysis);
    const failure = one(row.error);
    const cameBack = answer || (failure ? '<p class="failed">' + escapeText(failure) + "</p>" : "");
    return '<div class="rosette-row' + (mode ? " is-" + escapeText(mode) : "") + '">' +
      '<div class="wrote"><p class="phrase">' +
      escapeText(text === null || text === undefined ? "" : String(text).trim()) +
      "</p></div>" +
      '<div class="came-back">' + cameBack + "</div>" +
      "</div>";
  }

  /** The same line, as an element -- what a host that appends rather than sets innerHTML wants. */
  function rowElement(row) {
    const holder = document.createElement("div");
    holder.innerHTML = rowHtml(row);
    return holder.firstElementChild;
  }

  // Enough of Rosette's look to carry the line into a page that has never heard
  // of it. Scoped to `.rosette-row`, so a host that already styles these
  // classes -- the notebook, the review page -- is untouched by it.
  const CSS = `
.rosette-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 0;
  padding: .4rem 0;
  font-family: "EB Garamond", "Iowan Old Style", Palatino, Georgia, serif;
  font-size: 17px;
  line-height: 1.6;
  color: #1b2430;
}
.rosette-row .wrote { padding-right: .95rem; }
.rosette-row .came-back { padding-left: .95rem; border-left: 1.5px solid rgba(200, 54, 43, .3); }
.rosette-row p { margin: 0; }
.rosette-row .phrase, .rosette-row .italian { font-size: 1.02rem; }
.rosette-row .english { font-size: 1.02rem; font-style: italic; }
.rosette-row .wrong {
  color: #c8362b;
  text-decoration: line-through;
  text-decoration-thickness: 1.5px;
}
.rosette-row .right { color: #c8362b; font-weight: 500; }
.rosette-row .verdict {
  font-family: "Archivo", "Helvetica Neue", Arial, sans-serif;
  font-size: .8rem;
  font-weight: 500;
  color: #c8362b;
  margin: 0 0 .28rem;
}
.rosette-row .verdict.ok { color: #3f7d58; }
.rosette-row .reply, .rosette-row .note {
  font-family: "Archivo", "Helvetica Neue", Arial, sans-serif;
  font-size: .82rem;
  line-height: 1.55;
  color: #66707f;
  max-width: 62ch;
}
.rosette-row .note { margin: .3rem 0 0; }
.rosette-row .failed { font-size: .82rem; color: #c8362b; }
@media (max-width: 720px) {
  .rosette-row { grid-template-columns: 1fr; }
  .rosette-row .came-back { padding-left: 0; border-left: 0; margin-top: .25rem; }
  .rosette-row .wrote { padding-right: 0; }
}
`;

  root.RosetteFormat = {
    answer: answerHtml,
    row: rowHtml,
    element: rowElement,
    segments: segmentsHtml,
    mode: modeOf,
    escapeText: escapeText,
    CSS: CSS,
  };
})(typeof window !== "undefined" ? window : globalThis);
