# rosette

A two-column notebook for use during a language class. The left column is the
student's: one line per row, Enter starts the next row. The right column is the
tutor's, filled a few seconds behind each line:

- an English line comes back translated into the chosen language,
- a line in that language with enough substance is assessed and corrected in red,
- a single word (or a fragment too short to judge) is translated to English,
- a line beginning with `?` is a question, and is answered.

The language is chosen from the list beside the title and defaults to Italian;
"Other" names one that is not on the list. Each row records the language it was answered in,
so switching does not disturb what is already on the page.

Lines are saved as they are typed, and the page holds one request open so an
answer appears the moment it is stored. Clear (beside the line count) empties
the notebook, and takes a second confirming click; the cleared notebook is
written to `cleared/` under the data directory rather than destroyed, so it can
be put back by copying it over `notebook.json`. Every row keeps the model's raw
response on disk even though nothing links to it.

Answers come from `claude -p` (see `tutor.py` for the model, the prompt, and the
per-language grammar checklists); each line costs under half a cent and takes
around five seconds. Extended thinking is deliberately off -- it tripled the
wait without improving the answers. A correction whose runs do not faithfully
rebuild what the student wrote is dropped, leaving the explanation without a
wrong mark-up.
