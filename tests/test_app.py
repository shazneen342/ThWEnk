"""Streamlit UI tests use a mocked provider; they do not claim live model validation."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from core import Episode, Overview, Quiz, Reflection, Explanation
from test_core import fixture
from ai import AIError

ROOT = Path(__file__).resolve().parents[1]


def fake_generate(schema, task, segments, language, key, model, context=None):
    episode = fixture(segments)
    if schema is Episode:
        return episode
    if schema is Overview:
        return episode.overview.model_copy(update={"themes": ["Simplified learning"]})
    if schema is Quiz:
        return episode.quiz
    if schema is Reflection:
        return Reflection(title="Critical reading", items=[
            {"kind": kind, "text": "An example grounded in the discussion.", "source_ids": [segments[0].id]}
            for kind in ["Speaker claim", "AI interpretation", "Alternative viewpoint"]
        ], limitations="No independent verification.")
    if schema is Explanation:
        return Explanation(explanation={"text": "A simpler explanation.", "source_ids": [segments[0].id]},
                           illustrative_example="An imaginary learner pauses to explain a term.")
    raise AssertionError(schema)


def button(at, label):
    return next(b for b in at.button if b.label == label)


def select(at, label):
    return next(b for b in at.selectbox if b.label == label)


class AppTests(unittest.TestCase):
    def test_initial_screen_without_key(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "", "APP_PASSWORD": ""}):
            at = AppTest.from_file(str(ROOT / "app.py")).run()
        self.assertFalse(at.exception)
        self.assertTrue(any("Setup needed" in x.value for x in at.warning))

    def test_core_workflow_and_revision_isolation(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key", "APP_PASSWORD": ""}), patch("ai.generate", side_effect=fake_generate):
            at = AppTest.from_file(str(ROOT / "app.py")).run()
            at.radio[0].set_value("Try a sample").run()
            at.checkbox[0].check().run()
            button(at, "Generate companion").click().run()
            self.assertFalse(at.exception)
            initial_quiz = at.session_state["episode"].quiz.model_dump()
            next(t for t in at.text_input if t.label == "Revise this overview").set_value("Simpler")
            button(at, "Update overview only").click().run()
            self.assertFalse(at.exception)
            self.assertEqual(at.session_state["episode"].quiz.model_dump(), initial_quiz)
            self.assertEqual(at.session_state["episode"].overview.themes, ["Simplified learning"])
            button(at, "Explain selected chapter").click().run()
            self.assertTrue(at.session_state["explanations"])
            button(at, "Explore perspective").click().run()
            self.assertTrue(at.session_state["reflections"])
            next(r for r in at.radio if r.label.startswith("1.")).set_value(0)
            button(at, "Check answers").click().run()
            self.assertTrue(any("Score: 1/1" in x.value for x in at.success))
            self.assertFalse(at.exception)
            before = at.session_state["episode"].model_dump()
            with patch("ai.generate", side_effect=AIError("Provider quota reached")):
                next(t for t in at.text_input if t.label == "Revise this overview").set_value("Try again")
                button(at, "Update overview only").click().run()
                self.assertEqual(before, at.session_state["episode"].model_dump())
                self.assertTrue(any("quota" in x.value for x in at.error))


if __name__ == "__main__":
    unittest.main()
