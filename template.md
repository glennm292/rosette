---
title: "Rosette"
description: "A two-column notebook for language class: your notes and work on the left, automatic translations and corrections on the right."
thumbnail: "template.svg"
version: v1
format: v2
---

# Rosette

This file is the manifest for the **Rosette** template (slug:
`rosette`). It is the one document a future agent reads to understand,
present, and adapt this template. If you are an agent in a mind that was
created from this template, this file is your script: read all of it, then
follow "How to adapt it" below.

## What it is

A two-column notebook for language class: your notes and work on the left, automatic translations and corrections on the right.

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
the workspace's own Claude access.

## How it works

The snapshot includes these paths (each is a repo-root-relative path copied
from the original mind onto a clean default-workspace-template base):

- `system/apps/rosette`
- `system/supervisord.conf.d/rosette.conf`

`system/apps/rosette` is the whole app -- a small Flask package with its own
`pyproject.toml` (flask, flask-sock, loguru, pydantic, werkzeug), its app
manifest (`app.toml`), its icon, its README and its ratchets file. Inside
`src/rosette/`: `runner.py` is the server and its HTTP routes, `tutor.py` holds
the model, the prompt and the per-language grammar checklists, `notebook.py` and
`settings.py` are the two on-disk stores, `data_types.py` the pydantic shapes,
`claude_p.py` a vendored copy of the workspace's headless-Claude helper, and
`assets/index.html` the entire front end -- one self-contained page, no build
step.

`system/supervisord.conf.d/rosette.conf` is the program entry that runs it. On
start it calls `system/scripts/forward_port.py` with the app's manifest to
register `http://localhost:8082` as this app's tab, then runs the `rosette`
console script. Port and data directory are both overridable
(`ROSETTE_PORT`, `ROSETTE_DATA_DIR`) so a throwaway copy can run beside the live
one while it is being edited.

**The two columns.** The browser `PUT`s each line to `/api/rows/<row_id>` as it
is typed. The server stores the line immediately with status `pending` and hands
it to a small thread pool (four workers -- each reply is a whole subprocess, so
an unbounded burst would starve the machine rather than answer sooner). When the
answer lands it is written back only if the line still says what it said when
the question was asked, so an answer to a line you have since rewritten is
dropped rather than shown against the new text.

**Answers reach the page the instant they are stored.** There is no polling: the
notebook store keeps a version counter and a condition variable, the page holds
one `GET /api/notebook?since=<version>` open, and the server completes that
request the moment a write bumps the counter (or after 25 seconds, so a dead
connection gets noticed and renewed).

**Where the answers come from.** `tutor.py` calls `claude -p` through the
vendored `claude_p.py` helper -- the keyless, subscription-backed route, with no
API key anywhere. The model is `claude-sonnet-5`, named in one constant.

**Extended thinking is deliberately off** (`MAX_THINKING_TOKENS=0` in the child
environment), and this is the most transferable decision in the app. Left on,
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
the line the student actually wrote, or that changes nothing at all -- it falls
back to showing the explanation with no verdict. This caught real failures: a
German correction that silently dropped the subject, and a Spanish one that
claimed a fix it had not made.

**On disk.** `notebook.json` and `settings.json` under the app's data directory,
each written through a temp file and renamed so a crash leaves the previous copy
intact. Every row keeps the model's raw response even though nothing in the page
links to it. Clearing the notebook writes the cleared copy into `cleared/` with
a timestamped name rather than deleting it.

**Cost.** Roughly half a cent per line, a few seconds each.

**Look.** The page is an Italian school exercise book: a red margin rule down
the middle, corrections in red pen, the answer inking in from the left as it
arrives. The student's writing and the target language are set in the same serif
(EB Garamond) because both are *language*; the explanations are in a sans
(Archivo) because they are *commentary about* language. Both webfonts load from
Google Fonts at runtime and the page degrades to system serif and sans without
network.

## Recipe

This template is version `v1`. It is not a fork of the
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

- requires_llm: calls Claude through the KEYLESS `claude -p` path (the
  subscription route, via the `claude_p.py` copy vendored into the package);
  no API key, no latchkey connector and no third-party account are involved,
  so on a mind already signed in to Claude this needs nothing done to it. An
  adopter whose mind is on the KEYED path (`ANTHROPIC_API_KEY` -> litellm) must
  switch the call in `src/rosette/tutor.py` to that route per the
  `use-ai-integration` skill; everything else about the app is unaffected.

There are no other activation requirements. This template needs **no latchkey
permissions** and **no secrets** -- both lists are genuinely empty, so there is
nothing for the adopting agent to request or wire up before starting it.

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
(flask, flask-sock, loguru, pydantic, werkzeug), which `uv sync --all-packages`
resolves like any other package in the repo; it shells out to nothing but the
`claude` CLI the workspace already has. The `[environment]` tables in
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

## Adaptation history

Each mind that adapts this template appends one dated entry below. Earlier
entries are never rewritten.
