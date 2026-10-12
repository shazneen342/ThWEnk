"""THWENK: a transcript-first English/Bangla podcast companion."""
import hashlib
import hmac
import os
from pathlib import Path

import streamlit as st

   from ai import AIError, DEFAULT_GROQ_MODEL, generate, build_long_episode, select_passages, chunk_segmentsfrom core import (Chapter, Episode, Explanation, Overview, Quiz, Reflection,
                  MAX_CHARS, decode_upload, export_markdown, parse_transcript, timestamp)

st.set_page_config(page_title="THWENK · Podcast Companion", page_icon="🎧", layout="wide")


def setting(name, default=""):
    value = os.environ.get(name)
    if value:
        return value
    try:
        return str(st.secrets.get(name, default))
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        return default


password = setting("APP_PASSWORD")
if password and not st.session_state.get("authenticated"):
    st.title("THWENK · Reviewer access")
    with st.form("login"):
        supplied = st.text_input("Review password", type="password")
        if st.form_submit_button("Open companion"):
            if hmac.compare_digest(supplied.encode(), password.encode()):
                st.session_state.authenticated = True
                st.rerun()
            st.error("Incorrect password.")
    st.stop()

provider = setting("AI_PROVIDER", "groq").lower()
key = setting("GROQ_API_KEY" if provider == "groq" else "Groq_API_KEY")
model = setting("GROQ_MODEL", DEFAULT_GROQ_MODEL) if provider == "groq" else setting("Groq_MODEL", DEFAULT_MODEL)


def ask(schema, task, segments, language, context=None):
    count = st.session_state.get("request_count", 0)
    if count >= 600:
        raise AIError("This session reached its 600-request session limit. Export your notes before starting a new session.")
    st.session_state.request_count = count + 1
    if sum(len(s.text) for s in segments) > 3000 or len(segments) > 35:
        query = str(context or {})
        segments = select_passages(segments, query)
        st.info("This follow-up uses selected source passages, not every passage in the full episode. Inspect its source links.")
        task += " Analyse only these selected passages. Explicitly state this limited scope; do not claim complete coverage."
        if context and "current" in context:
            context = {k: v for k, v in context.items() if k != "current"}
    return generate(schema, task, segments, language, key, model, context, provider=provider)


def sources(ids, segments):
    by_id = {s.id: s for s in segments}
    with st.expander("View source · " + ", ".join(f"S{i}" for i in ids)):
        for source_id in ids:
            s = by_id[source_id]
            label = f"S{s.id}"
            if s.start is not None:
                label += f" · {timestamp(s.start)}–{timestamp(s.end)}"
            st.caption(label)
            st.text(s.text)


def note(item, segments):
    st.text(item.text)
    sources(item.source_ids, segments)


with st.sidebar:
    st.title("🎧 THWENK")
    st.caption("Inspired by think. Listen. Understand. Question.")
    output_language = st.selectbox("Output language", ["Bangla", "English"])
    st.info("English / Bangla transcripts. Full transcripts: up to 200,000 characters, 2 MB and 5,000 source passages.")
    st.caption("No audio upload in this version. Chakma support is planned, not implemented.")
    st.caption("AI content may be incorrect. Source links show transcript support, not independent verification.")
    st.caption(f"Transcript content is sent to {provider.title()} when you generate or revise. Use permitted, non-sensitive material.")
    if provider == "groq":
        st.caption("Long transcripts are processed section by section. If quota is reached, wait and click Generate again to resume. Keep this tab open.")
    if st.button("Clear episode"):
        for name in ["episode", "segments", "language", "reflections", "explanations", "fingerprint"]:
            st.session_state.pop(name, None)
        st.rerun()

st.title("THWENK · Turn a podcast into understanding")
st.write("Explore the ideas, inspect the evidence, and learn in English or বাংলা.")
#if not key or key.startswith("REPLACE_"):
   # st.warning("Setup needed: add your selected provider API key to server secrets. See SETUP_GUIDE.md.")

with st.expander("1 · Add your transcript", expanded="episode" not in st.session_state):
    mode = st.radio("Input method", ["Paste text", "Upload file", "Try a sample"], horizontal=True)
    raw, extension = "", "txt"
    if mode == "Paste text":
        raw = st.text_area("English or Bangla transcript", height=220, max_chars=MAX_CHARS + 1,
                           placeholder="Paste the episode transcript here…")
    elif mode == "Upload file":
        uploaded = st.file_uploader("UTF-8 TXT, SRT or VTT · maximum 2 MB", type=["txt", "srt", "vtt"])
        if uploaded is not None:
            try:
                raw = decode_upload(uploaded.getvalue())
                extension = Path(uploaded.name).suffix.lower().lstrip(".")
            except ValueError as exc:
                st.error(str(exc))
    else:
        sample = st.selectbox("Sample", ["English text", "English with timestamps", "Bangla text"])
        name = {"English text": "learning.txt", "English with timestamps": "learning.srt", "Bangla text": "bangla.txt"}[sample]
        raw = (Path(__file__).parent / "samples" / name).read_text(encoding="utf-8")
        extension = Path(name).suffix.lstrip(".")
        st.caption("Original illustrative sample, not a real podcast or scientific study. This still calls the real AI API.")
        st.text(raw)
    st.caption("Long episodes take multiple API calls. Completed sections can be resumed in this tab after an error. The full overview combines section summaries; follow-up analysis uses selected passages.")
    consent = st.checkbox(f"I have permission to process this content and agree to send it to {provider.title()}.")
    if st.button("Generate companion", type="primary", disabled=not consent):
        try:
            segments = parse_transcript(raw, extension)
            fingerprint = hashlib.sha256((raw + output_language + provider + model).encode()).hexdigest()
            if st.session_state.get("fingerprint") == fingerprint:
                st.info("This transcript and language are already loaded. Use the revision controls below.")
            else:
                with st.spinner("Reading the transcript and building your companion…"):
                    if st.session_state.get("work_fingerprint") != fingerprint:
                        st.session_state.work_fingerprint = fingerprint
                        st.session_state.section_cache = {}
                    bar = st.progress(0)
                    def progress(done, total, remaining=0):
                        bar.progress(done / total, text=f"Section {done}/{total} complete" + (f" · quota pacing: {remaining}s" if remaining else " · saved in this session"))
                    episode = build_long_episode(segments, output_language, ask,
                        st.session_state.section_cache, progress, pause_seconds=65 if provider == "groq" else 0)
                st.session_state.update(episode=episode, segments=segments, language=output_language,
                    reflections={}, explanations={}, fingerprint=fingerprint,
                    quiz_version=st.session_state.get("quiz_version", 0) + 1)
                st.rerun()
        except (ValueError, AIError) as exc:
            st.error(str(exc))

if "episode" not in st.session_state:
    st.info("Add a transcript to generate an overview, navigable chapters and a quiz. Explore perspectives after generation.")
    st.stop()

episode = st.session_state.episode
segments = st.session_state.segments
language = st.session_state.language
st.text(episode.title)
st.caption(f"Current companion: {language} · {len(segments)} source passages · {len(episode.chapters)} chapters")
if output_language != language:
    st.info(f"These results remain in {language}. Generate again to create a companion in {output_language}.")

overview_tab, chapter_tab, perspective_tab, quiz_tab, export_tab = st.tabs(
    ["Overview", "Chapters", "Perspectives", "Quiz", "Download"])

with overview_tab:
    note(episode.overview.summary, segments)
    st.markdown("**Main themes**")
    for theme in episode.overview.themes:
        st.text("• " + theme)
    st.markdown("**Takeaways**")
    for takeaway in episode.overview.takeaways:
        note(takeaway, segments)
    with st.form("revise_overview"):
        revision = st.text_input("Revise this overview", placeholder="Make it simpler, with shorter sentences", max_chars=500)
        if st.form_submit_button("Update overview only"):
            if not revision.strip():
                st.warning("Describe the change you want.")
            else:
                try:
                    with st.spinner("Revising the overview…"):
                        updated = ask(Overview, "Revise only the overview according to the user's request. Preserve factual grounding.",
                                      segments, language, {"request": revision, "current": episode.overview.model_dump()})
                    st.session_state.episode = episode.model_copy(update={"overview": updated})
                    st.rerun()
                except AIError as exc:
                    st.error(str(exc))

with chapter_tab:
    selected = st.selectbox("Jump to a chapter", range(len(episode.chapters)),
                            format_func=lambda i: f"{i + 1}. {episode.chapters[i].title}")
    chapter: Chapter = episode.chapters[selected]
    chapter_sources = [s for s in segments if s.id in chapter.source_ids]
    st.text(chapter.title)
    if chapter_sources[0].start is not None:
        st.caption(f"{timestamp(chapter_sources[0].start)}–{timestamp(max(s.end for s in chapter_sources))} · from uploaded subtitles")
    else:
        st.caption("No reliable timestamps supplied. Navigate using the source passages below.")
    st.text(chapter.explanation)
    st.caption("Chapter source ranges are assigned by the app for continuous navigation. Inspect the passages to assess the explanation.")
    sources(chapter.source_ids, segments)
    with st.form("chapter_help"):
        help_mode = st.selectbox("How can we help?", ["Simplify this chapter", "Translate the source passages faithfully", "Explain the concepts more deeply"])
        if st.form_submit_button("Explain selected chapter"):
            try:
                with st.spinner("Working on this chapter…"):
                    result = ask(Explanation,
                        "Help with the selected chapter using the user's requested mode. "
                        "For faithful translation translate every supplied passage and leave illustrative_example empty. "
                        "For explanation, add at most one clearly hypothetical example, or an empty string if unhelpful.",
                        chapter_sources, language, {"mode": help_mode})
                st.session_state.explanations[f"{selected + 1}. {chapter.title}"] = result
                st.rerun()
            except AIError as exc:
                st.error(str(exc))
    explanation = st.session_state.explanations.get(f"{selected + 1}. {chapter.title}")
    if explanation:
        note(explanation.explanation, segments)
        if explanation.illustrative_example:
            st.caption("Illustrative example · AI-generated")
            st.text(explanation.illustrative_example)

with perspective_tab:
    st.write("Choose a suggested perspective, or enter your own. Analysis is generated only when requested.")
    with st.form("perspective"):
        lens = st.selectbox("Suggested perspectives", episode.perspectives + ["Custom perspective"])
        custom = st.text_input("Your perspective (used only when Custom is selected)", max_chars=200,
                               placeholder="How could these ideas affect rural students in Bangladesh?")
        depth = st.selectbox("Detail", ["Simple and concise", "Deeper critical analysis"])
        submitted = st.form_submit_button("Explore perspective")
    if submitted:
        chosen = custom.strip() if lens == "Custom perspective" else lens
        if not chosen:
            st.warning("Enter a custom perspective.")
        else:
            try:
                with st.spinner("Exploring the argument and its limits…"):
                    reflection = ask(Reflection,
                        "Analyse the selected perspective. Include a speaker claim, evidence provided "
                        "(explicitly state when evidence is absent), and distinguish inferred assumptions, "
                        "possible implications and an alternative view. Ground each item in source passages. "
                        "If the lens is unrelated, explain the mismatch rather than forcing a connection. "
                        "Do not add purported facts about Bangladesh or any other context not in the source.",
                        segments, language, {"perspective": chosen, "detail": depth})
                st.session_state.reflections[chosen] = reflection
                st.rerun()
            except AIError as exc:
                st.error(str(exc))
    for lens_name, reflection in st.session_state.reflections.items():
        with st.expander(lens_name, expanded=True):
            st.text(reflection.title)
            for item in reflection.items:
                st.caption(item.kind)
                st.text(item.text)
                sources(item.source_ids, segments)
            st.caption("Analysis limitations")
            st.text(reflection.limitations)

with quiz_tab:
    version = st.session_state.quiz_version
    with st.form(f"quiz_{version}"):
        answers = []
        for i, q in enumerate(episode.quiz.questions):
            answers.append(st.radio(f"{i + 1}. {q.question}", range(4), index=None,
                format_func=lambda n, options=q.options: options[n], key=f"answer_{version}_{i}"))
        checked = st.form_submit_button("Check answers")
    if checked:
        if any(answer is None for answer in answers):
            st.warning("Answer each question before checking.")
        else:
            st.session_state[f"checked_{version}"] = answers
    saved_answers = st.session_state.get(f"checked_{version}")
    if saved_answers is not None:
        score = sum(answer == q.correct_index for answer, q in zip(saved_answers, episode.quiz.questions))
        st.success(f"Score: {score}/{len(episode.quiz.questions)}")
        for i, (answer, q) in enumerate(zip(saved_answers, episode.quiz.questions)):
            st.text(f"{i + 1}. {'✓' if answer == q.correct_index else '✗'} Correct answer: {q.options[q.correct_index]}")
            st.text(q.explanation)
            sources(q.source_ids, segments)
    with st.form("revise_quiz"):
        quiz_request = st.text_input("Change the quiz", max_chars=500, placeholder="Make the questions more challenging")
        if st.form_submit_button("Update quiz only"):
            if not quiz_request.strip():
                st.warning("Describe your requested change.")
            else:
                try:
                    with st.spinner("Revising the quiz…"):
                        updated = ask(Quiz, "Revise only the quiz. Keep it answerable from the source, with at most five questions.",
                            segments, language, {"request": quiz_request, "current": episode.quiz.model_dump()})
                    st.session_state.episode = episode.model_copy(update={"quiz": updated})
                    st.session_state.quiz_version += 1
                    st.rerun()
                except AIError as exc:
                    st.error(str(exc))

with export_tab:
    exported = export_markdown(episode, segments, language, st.session_state.reflections, st.session_state.explanations)
    st.write("Includes current notes, generated perspectives, chapter help, quiz answer key and source transcript.")
    st.download_button("Download Markdown notes", exported.encode("utf-8"), "podcast-notes.md", "text/markdown")
    st.download_button("Download text notes", exported.encode("utf-8-sig"), "podcast-notes.txt", "text/plain")
    st.download_button("Download structured episode JSON", episode.model_dump_json(indent=2).encode("utf-8"),
                       "episode.json", "application/json")
    st.caption("JSON contains the base companion and its overview/quiz revisions. Full exports above also include perspectives and chapter help.")
    st.text_area("Select and copy your notes", exported, height=300)
