---
title: "Rosette"
description: "A two-column notebook for language class: your notes and work on the left, automatic translations and corrections on the right. Runs inside Mind, or standalone with your own Anthropic API key."
thumbnail: "template.svg"
version: v3
format: v2
---

# Rosette

This file is the manifest for the **Rosette** template (slug:
`rosette`). It is the one document a future agent reads to understand,
present, and adapt this template. If you are an agent in a mind that was
created from this template, this file is your script: read all of it, then
follow "How to adapt it" below.

## What it is

A two-column notebook for language class: your notes and work on the left, automatic translations and corrections on the right. Runs inside Mind, or standalone with your own Anthropic API key.

Rosette is a single page you keep open during a language lesson. It looks like a
school exercise book split down the middle by a red margin rule: the left half
is yours to write in, the right half is the teacher's margin. You type one line
per row -- Enter starts the next row, Shift+Enter breaks a line without leaving
it, Backspace on an empty row removes it -- and about five seconds behind you
the right-hand margin fills in. What comes back depends on what you wrote. An
English line comes back as the natural sentence in the language you are
studying. A line in that language with enough substance to judge is marked:
either "All correct" with a short note, or a correction written as red pen --
your wrong words struck through with the replacement beside them -- plus a
sentence or two on why. A single word, or a fragment too short to judge, is
translated into English with a note on its gender and everyday use. And a line
that starts with `?` is treated as a question to the tutor and simply answered.

The language sits in a drop-down beside the title, defaults to Italian, and has
an "Other" entry for naming anything not on the list. Each row remembers the
language it was answered in, so switching mid-notebook leaves earlier lines
alone. Notes persist between sessions; a Clear control beside the line count
empties the page behind a two-click confirm and files the cleared notebook away
rather than destroying it. The whole thing is one app, one tab, no setup beyond
the workspace's own Claude access -- or, outside a Mind, your own Anthropic API
key, which also makes each answer arrive faster inside one.

A question is answered against the notebook rather than in isolation, so "the
line above" means the line above. Tell it something lasting about yourself ("I'm
male") and it is remembered: every later line is judged with it known, the
earlier lines it changes are re-read, and the fact is listed under the header
where it can be taken back. A small pair of scissors in the left margin removes a
line and everything below it, with an undo for a few seconds afterwards.

## How it works

The snapshot includes these paths (each is a repo-root-relative path copied
from the original mind onto a clean default-workspace-template base):

- `system/apps/rosette`
- `system/supervisord.conf.d/rosette.conf`

`system/apps/rosette` is the whole app -- a small Flask package with its own
`pyproject.toml` (flask, flask-sock, litellm, loguru, pydantic, werkzeug), its
app manifest (`app.toml`), its icon, its README, its ratchets file and its tests
(`runner_test.py`). Inside `src/rosette/`: `runner.py` is the server and its
HTTP routes, `tutor.py` holds the model, the prompt, the per-language grammar
checklists and the two routes to Claude, `notebook.py` and `settings.py` are the
two on-disk stores, `credentials.py` looks after the optional API key,
`data_types.py` the pydantic shapes, `claude_p.py` a vendored copy of the
workspace's headless-Claude helper, and `assets/index.html` the entire front end
-- one self-contained page, no build step.

`system/supervisord.conf.d/rosette.conf` is the program entry that runs it. On
start it calls `system/scripts/forward_port.py` with the app's manifest to
register `http://localhost:8082` as this app's tab, then runs the `rosette`
console script. Port and data directory are both overridable
(`ROSETTE_PORT`, `ROSETTE_DATA_DIR`) so a throwaway copy can run beside the live
one while it is being edited.

**The two columns.** The browser `PUT`s each line to `/api/rows/<row_id>` as it
is typed. The server stores the line immediately with status `pending` and hands
it to a small thread pool (four workers -- on the keyless route each reply is a
whole subprocess, so an unbounded burst would starve the machine rather than
answer sooner). When the answer lands it is written back only if the line still
says what it said when the question was asked, so an answer to a line you have
since rewritten is dropped rather than shown against the new text. Until the new
answer arrives, an edited line keeps showing its previous one rather than
blanking.

**Nothing is stranded or lost silently.** Any row still `pending` when the
server starts goes straight back in the queue -- a restart mid-answer used to
leave that row waiting forever. A worker's failure is read off the finished task
rather than caught inside it, so an error nobody anticipated still reaches the
row as a visible failure instead of vanishing. The red margin rule doubles as
status: it shows red beside a line while that line is being read, and green once
its answer is in.

**Questions are answered against the notebook.** A `?` line is sent with the
eight lines above it, each with a short recap of what the tutor said about it,
so "the line above" and "that last sentence" resolve. The reply can also carry a
lasting fact about the student ("the student is male") and the distances of the
lines above whose reading that fact changes. The fact is recorded in the
settings store and included in every later prompt, and the named lines are
re-queued -- so a hedge like "if you are female, use *stanca*" disappears from an
answer that was already given. The facts are listed under the header, each with
a control to take it back, since a wrong one would quietly skew everything
judged after it.

**Answers reach the page the instant they are stored.** There is no polling: the
notebook store keeps a version counter and a condition variable, the page holds
one `GET /api/notebook?since=<version>` open, and the server completes that
request the moment a write bumps the counter (or after 25 seconds, so a dead
connection gets noticed and renewed).

**Where the answers come from -- two routes.** With an API key, `tutor.py`
calls the Anthropic API directly through litellm, with the tutor's instructions
marked cacheable since they are the same on every line; this is the quicker
route, because it skips starting a fresh process per line. Without a key it
calls `claude -p` through the vendored `claude_p.py` helper -- the keyless,
subscription-backed route a signed-in Mind already has. The key is read from
`ANTHROPIC_API_KEY` first (how a standalone copy is configured), then from a
file in the app's own data directory that the page writes when the key is
entered under "Speed this up" in the header. That file is created readable by
its owner only, and no route ever returns the key to the page -- only whether
one is set, where it came from, and its last four characters. The model is
`claude-sonnet-5`, named in one constant, on both routes.

**Extended thinking is deliberately off on both routes**
(`MAX_THINKING_TOKENS=0` in the child environment on the keyless route,
`thinking` disabled in the API call on the keyed one), and this is the most
transferable decision in the app. The keyed route needs it said separately:
there, left on, the worst line measured took 31s and 3728 output tokens against
4.8s and 553 with it off -- and it overran the reply limit, which does not
truncate a reply but returns an EMPTY one, so the row got nothing back at all.
On the keyless route, left on,
the model spent thousands of invisible tokens deliberating before each line --
up to 4757 to gloss a single word -- which roughly doubled both the wait and the
cost. Turning it off and spending part of the saving on a stronger model plus an
explicit grammar checklist scored *better* on a fixed set of ten Italian lines
with known mistakes (8-10 out of 10, against 8 with thinking on) at about half
the wait (~6s typical and 8s worst case, against ~13s and 24s) and less per
line. Deliberation was not buying accuracy here; a checklist and a better model
were.

**The grammar checklist is per-language.** `_CHECKLIST_BY_LANGUAGE` in
`tutor.py` holds a specific, tuned list for Italian -- auxiliary choice in the
passato prossimo, gender and number agreement, prepositions contracting with the
article, piacere-type verbs, subjunctive triggers, accents, pronoun word order.
Every other language falls back to a generic list naming the same seven
categories and leaving the specifics to the model. Adding a tuned list for
another language is one new entry in that dict and nothing else.

**Corrections are typed, and verified before they are shown.** The model returns
a correction as a sequence of runs (`same` / `wrong` / `right`) rather than free
text, and the server rejects any correction whose runs do not faithfully rebuild
the line the student actually wrote, or that changes nothing at all. Such a
reply first gets one more attempt, since a fresh one often marks the same line
cleanly and the marked-up line is most of a correction's value; only if that
fails too does it fall back to showing the explanation with no verdict. This
caught real failures: a German correction that silently dropped the subject, and
a Spanish one that claimed a fix it had not made.

**Clearing from a line down.** Hovering the left margin beside a line reveals a
small scissors; clicking it dims that line and everything below it, then removes
them and offers an undo for twelve seconds. The removed stretch is kept in the
cleared list before it goes, and the undo appends it back with its answers
intact, so nothing is re-asked.

**On disk.** `notebook.json` and `settings.json` (the chosen language and the
recorded facts) under the app's data directory, each written through a temp file
and renamed so a crash leaves the previous copy intact. Every row keeps the
model's raw response even though nothing in the page links to it. Every cleared
line, from Clear or the scissors, goes into `cleared.json` rather than being
deleted, labelled with which clearing removed it, when, how, and whether it was
put back. A key entered in the page is kept in `anthropic-api-key` beside them,
owner-readable only; removing it from the page deletes the file.

**Tests.** `runner_test.py` covers the behaviour most likely to regress
silently: a failed answer surfacing on its row rather than vanishing, the key
never appearing in any response, the key file's permissions, an edited line
keeping its answer and an emptied one clearing, and scissors-then-undo.
`notebook_test.py` covers the cleared list: its labels, an undo marking a
clearing restored, old per-clearing files folded in exactly once, and an
unreadable cleared file never blocking startup.

**Cost.** Roughly half a cent per line, a few seconds each -- quicker on the
keyed route, which has no process to start.

**Look.** The page is an Italian school exercise book: a red margin rule down
the middle, corrections in red pen, the answer inking in from the left as it
arrives. The page runs the full width of its window, with the line spacing kept
tight. The student's writing and the target language are set in the same serif
(EB Garamond) because both are *language*; the explanations are in a sans
(Archivo) because they are *commentary about* language. Both webfonts load from
Google Fonts at runtime and the page degrades to system serif and sans without
network.

## Recipe

This template is version `v3`. It is not a fork of the
workspace it came from -- it is DERIVED from it by a recipe: include these
paths, leave these out, apply these published-version rules. An update re-runs
the recipe against the current workspace and publishes the result as the next
version, so anything excluded stays excluded even though it still exists in the
source workspace.

The recipe is machine-read, so it lives in the sibling
[`template.toml`](template.toml) -- its `[recipe]` table -- along with
the structured requirements and the environment this template needs
installed. That file is authoritative for all of it; this one holds the prose.

## Requirements

Everything the adopting mind must deal with before this template is really
theirs. Two kinds of entry, handled at different times:

- **Activation** -- what must be SET UP before anything runs, in the
  machine-readable `requires_` forms below. The adopting agent acts on these
  ITSELF, first, before asking anything.
- **Adaptation** -- what must be DECIDED or REWIRED, in prose. Worked through
  interactively with the user, after activation.

### Activation

- requires_llm: runs on EITHER route and picks one per call, so there is
  nothing to switch. It uses an Anthropic API key if it has one (KEYED, direct
  via litellm): `ANTHROPIC_API_KEY` in the environment first, then the key file
  the page writes into the app's own data directory when the user enters a key
  under "Speed this up" in the header. With neither, it falls back to the
  KEYLESS `claude -p` path (the subscription route, via the `claude_p.py` copy
  vendored into the package). So inside a Mind already signed in to Claude, no
  key is required and this needs nothing done to it; a key is what lets the app
  run standalone, outside a Mind, and it also makes answers quicker inside one.
  No latchkey connector and no third-party account are involved either way.

There are no other activation requirements. This template needs **no latchkey
permissions** and **no secrets** -- both lists are genuinely empty, so there is
nothing for the adopting agent to request or wire up before starting it. The
API key above is optional, never required, and is the user's to enter in the
page if they want it; it is not a secret the adopter must supply.

### Adaptation

Nothing must be rewired: the app runs as published, with an empty notebook, on
whatever language the user picks. The publishing user's own class notes were
deliberately left out (see the recipe's `exclude`), and a fresh notebook is the
correct starting state, not a gap.

Two optional changes are worth knowing about, neither required: the per-language
grammar checklist in `src/rosette/tutor.py` has a tuned list for Italian and a
generic one for every other language, and adding a tuned list is one new entry
in that dict; and `TUTOR_MODEL` in the same file is a single constant.

## Environment

What this template needs INSTALLED, beyond what the template already has.
Declared in `template.toml`'s `[environment]` table; an adopting mind
converges it at ITS OWN pinned apt snapshot timestamp, so package versions come
out consistent with the rest of that mind's environment rather than frozen to
whatever this publisher happened to have.

Nothing extra -- runs on the stock workspace environment. The app's only
dependencies are ordinary Python packages declared in its own `pyproject.toml`
(flask, flask-sock, litellm, loguru, pydantic, werkzeug), which `uv sync
--all-packages` resolves like any other package in the repo; it shells out to
nothing but the `claude` CLI the workspace already has, and only on the keyless
route. The `[environment]` tables in
`template.toml` are therefore empty by design, not by omission.

One runtime note that is not an environment declaration: the page loads two
webfonts (EB Garamond and Archivo) from Google Fonts when it opens. With no
network to reach them it falls back to the system serif and sans and stays
perfectly usable -- nothing needs installing either way.

## How to adapt it

Instructions for the NEXT agent -- the one adapting this template into a
new mind. This is the `use-template` skill's template path; in short:

1. Read this entire file first, especially "Requirements" below. It holds two
   kinds of entry and they are handled at different times: the machine-readable
   `requires_` lines are ACTIVATION (set them up before anything runs), and
   the prose bullets are ADAPTATION (decide or rewire them afterwards).
2. Present the template to the user in plain, non-technical language: what
   it is, what it does, and what it needs from them (name the activation
   requirements).
3. Ask whether they want to use the same connectors (e.g. their own Slack).
   If YES: ACTIVATE FIRST -- initiate every `requires_permission` line NOW
   via a latchkey permission request (see the `latchkey` skill; the request
   opens the approval/login flow in the minds app), wire up any
   `requires_secret` values, start the services, and get the app showing
   THE USER'S OWN DATA. Done for a data-backed app means the user can open it
   and see their own data -- NOT that a service starts or an endpoint returns
   200. Then tell them it is live and to take a look.
4. Only AFTER that (or immediately, if they chose different connectors -- the
   swap is then the first adaptation) ask: "How do you want to adapt it?"
5. Work through each requirement interactively, one at a time. Translate each
   into plain language, ask for a decision only when you genuinely need one,
   and resolve the obvious ones yourself.
6. When done, append a dated entry to "Adaptation history" below (never
   rewrite earlier entries) and commit.

## Publication history

This template's changelog: what each published version changed. The PUBLISHER
appends one entry per version (newest last); earlier entries are never rewritten.
This is distinct from "Adaptation history" below, which is the ADOPTERS' log.

### v1 (2026-09-24) -- the two-column class notebook: type a line, get back a translation, a gloss, an answer, or a red-pen correction, in any of 37 listed languages or any other you name.

### v2 (2026-09-29) -- self-aware questions, your own API key, clear-from-here, and a batch of fixes

- Questions are answered against the lines above them, and can record a lasting
  fact about the student that every later line uses and that re-reads the
  earlier lines it changes; the facts are listed under the header, each
  removable.
- An optional Anthropic API key ("Speed this up" in the header, or
  `ANTHROPIC_API_KEY`) sends each line straight to the API, and lets the app run
  outside a Mind; without one it uses `claude -p` as before. The key is stored
  owner-readable only and never returned to the page.
- Extended thinking is disabled on the keyed route too, where leaving it on
  overran the reply limit and returned empty answers.
- A scissors in the left margin removes a line and everything below it, with an
  undo that restores them, answers intact.
- The margin rule shows red while a line is being read and green once answered;
  an edited line keeps its previous answer until the new one lands; the page
  runs the full width with tighter spacing.
- Fixes: rows left pending by a restart are re-queued; unexpected failures show
  on their row instead of vanishing; an unmarkable correction gets one more
  attempt before falling back to the explanation alone.
- Tests for the above in `runner_test.py`.

### v3 (2026-10-01) -- cleared lines kept as one labelled list, and short English phrases translated

- Every cleared line -- from Clear, or from the scissors in the margin -- is now
  kept in one list, `cleared.json`, each line labelled with the clearing that
  removed it, when, how ("line and below" or "whole notebook"), its place in
  that clearing, and when it was put back if it was undone. A notebook cleared
  under an earlier version has its old per-clearing files folded into the list
  on first start.
- A damaged cleared-lines file no longer stops the app from starting.
- Short English phrases ("to hit") are now translated, rather than explained
  back in English; glossing is only for the language being learned.

## Adaptation history

Each mind that adapts this template appends one dated entry below. Earlier
entries are never rewritten.
