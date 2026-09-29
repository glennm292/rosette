from typing import Literal

from pydantic import BaseModel
from pydantic import Field

# What kind of help a line needs. A line opening with "?" is always ANSWER;
# otherwise the tutor picks.
#   TRANSLATE -- the line is English; the reply is the Italian for it.
#   GLOSS     -- the line is a single Italian word or a fragment too short to
#                judge; the reply is its English meaning.
#   ASSESS    -- the line is Italian with enough substance to check; the reply
#                is a verdict plus a corrected reconstruction.
#   ANSWER    -- the line is a question the student asked; the reply answers it.
ResponseMode = Literal["translate", "gloss", "assess", "answer"]

# Whether an assessed line needed changing.
Verdict = Literal["correct", "fix"]

# How a run of text in a corrected line relates to what the student wrote.
#   SAME  -- kept verbatim from the student's line.
#   WRONG -- the student's text that is being replaced.
#   RIGHT -- the replacement for the WRONG run immediately before it.
SegmentKind = Literal["same", "wrong", "right"]

# Where a line is in its trip through the tutor.
RowStatus = Literal["empty", "pending", "done", "error"]


class CorrectionSegment(BaseModel):
    """One run of a corrected line, tagged by how it relates to what was written."""

    kind: SegmentKind = Field(description="How this run relates to the student's line")
    text: str = Field(description="The run's text")


class Analysis(BaseModel):
    """The tutor's reply for one line."""

    mode: ResponseMode = Field(description="Which kind of help this line got")
    italian: str | None = Field(
        default=None, description="The Italian, when the line was English"
    )
    english: str | None = Field(
        default=None, description="The English meaning, when the line was a single word"
    )
    answer: str | None = Field(
        default=None, description="The reply, when the line was a question"
    )
    # A question can tell the tutor something durable about the student ("I am
    # male", "I only ever write about work"), which every later line is then
    # judged against. Kept separate from the answer because it outlives the row.
    learned: str | None = Field(
        default=None,
        description="A lasting fact the question taught, as the tutor would state it",
    )
    # Which earlier lines that fact changes the reading of, newest-first by
    # distance: 1 is the line directly above the question.
    revisit_distances: tuple[int, ...] = Field(
        default=(),
        description="How far above the question the lines it affects sit",
    )
    verdict: Verdict | None = Field(
        default=None, description="Whether an assessed line needed changing"
    )
    segments: tuple[CorrectionSegment, ...] = Field(
        default=(), description="The corrected line, run by run"
    )
    note: str = Field(
        default="", description="One or two sentences on why, in plain English"
    )


class Row(BaseModel):
    """One line of the notebook: what the student wrote and what came back."""

    row_id: str = Field(description="Client-minted identifier, stable across edits")
    text: str = Field(description="Exactly what the student typed")
    status: RowStatus = Field(description="Where this line is in its trip")
    analysis: Analysis | None = Field(
        default=None, description="The tutor's reply, once it has arrived"
    )
    # The model's reply verbatim, kept so a later change in how replies are
    # parsed or rendered needs no re-run, and so the student can always see the
    # unprocessed original behind the rendered view.
    raw_response: str | None = Field(
        default=None, description="The model's reply before any parsing"
    )
    error: str | None = Field(
        default=None, description="Why the tutor could not answer, if it could not"
    )
    model: str | None = Field(default=None, description="Which model answered")
    # Recorded per row rather than read from the current setting, so a line
    # written during Italian still reads as Italian after the notebook is
    # switched to another language.
    language: str | None = Field(
        default=None, description="The language this line was answered in"
    )
    cost_usd: float | None = Field(
        default=None, description="What this line's reply cost"
    )
    created_at: str = Field(description="When the line was first written, UTC ISO 8601")
    analyzed_at: str | None = Field(
        default=None, description="When the reply arrived, UTC ISO 8601"
    )


class Notebook(BaseModel):
    """Every line of the notebook, in the order they appear on the page."""

    rows: tuple[Row, ...] = Field(default=(), description="The lines, top to bottom")
