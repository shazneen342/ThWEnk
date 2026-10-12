"""Input parsing, validated output contracts, source checks and text export."""
from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_CHARS = 200_000
MAX_BYTES = 2_000_000
MAX_SEGMENTS = 5_000
Text = Annotated[str, Field(min_length=1, max_length=6000)]
IDs = Annotated[list[int], Field(min_length=1, max_length=20_000)]


@dataclass(frozen=True)
class Segment:
    id: int
    text: str
    start: float | None = None
    end: float | None = None


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Note(StrictModel):
    text: Text
    source_ids: IDs


class Overview(StrictModel):
    summary: Note
    themes: Annotated[list[Text], Field(min_length=1, max_length=6)]
    takeaways: Annotated[list[Note], Field(min_length=1, max_length=6)]


class Chapter(StrictModel):
    title: Text
    explanation: Text
    source_ids: IDs


class Question(StrictModel):
    question: Text
    options: Annotated[list[Text], Field(min_length=4, max_length=4)]
    correct_index: Annotated[int, Field(ge=0, le=3)]
    explanation: Text
    source_ids: IDs


class Quiz(StrictModel):
    questions: Annotated[list[Question], Field(min_length=1, max_length=5)]


class Episode(StrictModel):
    title: Text
    overview: Overview
    chapters: Annotated[list[Chapter], Field(min_length=1, max_length=2000)]
    quiz: Quiz
    perspectives: Annotated[list[Text], Field(min_length=3, max_length=5)]


class ReflectionItem(StrictModel):
    kind: Literal["Speaker claim", "Evidence in transcript", "AI interpretation",
                  "Inferred assumption", "Possible implication", "Alternative viewpoint"]
    text: Text
    source_ids: IDs


class Reflection(StrictModel):
    title: Text
    items: Annotated[list[ReflectionItem], Field(min_length=3, max_length=8)]
    limitations: Text


class Explanation(StrictModel):
    explanation: Note
    illustrative_example: str


TIME = r"(?:\d{2,}:)?[0-5]\d:[0-5]\d[,.]\d{3}"
CUE = re.compile(rf"^({TIME})\s+-->\s+({TIME})(?:[ \t]+.*)?$", re.MULTILINE)


def seconds(stamp: str) -> float:
    parts = stamp.replace(",", ".").split(":")
    return sum(float(part) * 60 ** i for i, part in enumerate(reversed(parts)))


def timestamp(value: float) -> str:
    whole = int(value)
    return f"{whole // 3600:02}:{whole % 3600 // 60:02}:{whole % 60:02}"


def parse_transcript(text: str, extension: str = "txt") -> list[Segment]:
    if extension.lower() not in {"txt", "srt", "vtt"}:
        raise ValueError("Supported formats: TXT, SRT and VTT only.")
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    if "\x00" in text:
        raise ValueError("This appears to be a binary file. Upload UTF-8 text.")
    if len(text) < 120:
        raise ValueError("Please provide at least 120 characters of transcript.")
    if len(text) > MAX_CHARS:
        raise ValueError(f"This application accepts at most {MAX_CHARS:,} characters. Use a shorter excerpt.")
    segments: list[Segment] = []
    timed = extension in {"srt", "vtt"} or "-->" in text
    if timed:
        previous = -1.0
        for block in re.split(r"\n[ \t]*\n", text):
            block = block.strip()
            if not block:
                continue
            if extension == "vtt" and block.splitlines()[0].split()[0] in {"WEBVTT", "NOTE", "STYLE", "REGION"}:
                continue
            match = CUE.search(block)
            if not match:
                raise ValueError("Malformed subtitles. Each cue needs a valid timestamp line and a blank line after it.")
            start, end = seconds(match[1]), seconds(match[2])
            if end <= start or start < previous:
                raise ValueError("Subtitle cue times must be ordered and each end must follow its start.")
            previous = start
            body = html.unescape(re.sub(r"<[^>]+>", "", block[match.end():])).strip()
            if not body:
                raise ValueError("A subtitle cue has no spoken text.")
            segments.append(Segment(len(segments) + 1, body, start, end))
    else:
        for paragraph in re.split(r"\n+", text):
            words = paragraph.split()
            chunk = ""
            for word in words:
                if chunk and len(chunk) + len(word) + 1 > 700:
                    segments.append(Segment(len(segments) + 1, chunk))
                    chunk = ""
                chunk = (chunk + " " + word).strip()
            if chunk:
                segments.append(Segment(len(segments) + 1, chunk))
    if not segments or sum(len(s.text) for s in segments) < 120:
        raise ValueError("Not enough spoken text to analyse. Include a longer excerpt.")
    if len(segments) > MAX_SEGMENTS:
        raise ValueError(f"Too many source passages; this application supports at most {MAX_SEGMENTS}. Use a shorter excerpt.")
    return segments


def decode_upload(data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise ValueError("Upload must be no larger than 2 MB.")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Save the file with UTF-8 encoding in VS Code, then upload again.") from exc


def source_payload(segments: list[Segment]) -> list[dict]:
    return [asdict(s) for s in segments]


def validate_sources(result: BaseModel, segments: list[Segment]) -> None:
    allowed = {s.id for s in segments}

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "source_ids":
                    if not item or not set(item).issubset(allowed) or len(item) != len(set(item)):
                        raise ValueError("AI returned missing, duplicate or unknown source references.")
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(result.model_dump())
    if isinstance(result, Episode):
        seen = []
        for chapter in result.chapters:
            ids = chapter.source_ids
            if ids != list(range(min(ids), max(ids) + 1)):
                raise ValueError("AI returned non-contiguous chapters.")
            seen.extend(ids)
        if seen != [s.id for s in segments]:
            raise ValueError("AI chapters must cover the transcript once, in order.")
    quiz = result.quiz if isinstance(result, Episode) else result if isinstance(result, Quiz) else None
    if quiz:
        for q in quiz.questions:
            if len(set(option.strip().casefold() for option in q.options)) != 4:
                raise ValueError("AI returned duplicate answer options.")


def references(ids: list[int]) -> str:
    return ", ".join(f"S{i}" for i in ids)


def export_markdown(episode: Episode, segments: list[Segment], language: str,
                    reflections: dict[str, Reflection], explanations: dict[str, Explanation]) -> str:
    lines = [f"# {episode.title}", f"Output language: {language}",
             "AI-generated study notes. Transcript support is not independent fact-checking.",
             "\n## Overview", episode.overview.summary.text,
             f"Sources: {references(episode.overview.summary.source_ids)}", "\n## Themes"]
    lines += [f"- {t}" for t in episode.overview.themes]
    lines.append("\n## Takeaways")
    lines += [f"- {n.text} [{references(n.source_ids)}]" for n in episode.overview.takeaways]
    for i, chapter in enumerate(episode.chapters, 1):
        lines += [f"\n## Chapter {i}: {chapter.title}", chapter.explanation,
                  f"Sources: {references(chapter.source_ids)}"]
    for title, explanation in explanations.items():
        lines += [f"\n## Chapter help: {title}", explanation.explanation.text,
                  f"Sources: {references(explanation.explanation.source_ids)}",
                  f"Illustrative example (AI-generated): {explanation.illustrative_example}"]
    for title, reflection in reflections.items():
        lines.append(f"\n## Perspective: {title}")
        for item in reflection.items:
            lines.append(f"- **{item.kind}:** {item.text} [{references(item.source_ids)}]")
        lines.append(f"Limitations: {reflection.limitations}")
    lines.append("\n## Quiz and answer key")
    for i, q in enumerate(episode.quiz.questions, 1):
        lines.append(f"\n{i}. {q.question}")
        lines += [f"   {j + 1}) {o}" for j, o in enumerate(q.options)]
        lines += [f"Answer: {q.correct_index + 1}. {q.explanation}", f"Sources: {references(q.source_ids)}"]
    lines.append("\n## Source transcript")
    for s in segments:
        time = f" ({timestamp(s.start)}–{timestamp(s.end)})" if s.start is not None else ""
        lines.append(f"\nS{s.id}{time}: {s.text}")
    return "\n\n".join(lines)
