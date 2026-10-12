import copy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import urllib.error

from pydantic import ValidationError
import ai
from core import (Episode, Overview, Quiz, decode_upload, export_markdown,
                  parse_transcript, validate_sources)

ROOT = Path(__file__).resolve().parents[1]


def fixture(segments):
    ids = [s.id for s in segments]
    n = {"text": "The speakers discuss learning and evidence.", "source_ids": [ids[0]]}
    return Episode.model_validate({
        "title": "Learning from podcasts",
        "overview": {"summary": n, "themes": ["Learning"], "takeaways": [n]},
        "chapters": [{"title": "Learning", "explanation": "A discussion of learning.", "source_ids": ids}],
        "quiz": {"questions": [{"question": "What is discussed?", "options": ["Learning", "Fishing", "Cooking", "Driving"],
            "correct_index": 0, "explanation": "The speakers discuss learning.", "source_ids": [ids[0]]}]},
        "perspectives": ["Educational", "Critical", "Social"],
    })


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.segments = parse_transcript((ROOT / "samples/learning.txt").read_text())
        self.episode = fixture(self.segments)
        ai._calls.clear()

    def test_all_samples(self):
        for name in ["learning.txt", "learning.srt", "bangla.txt"]:
            p = ROOT / "samples" / name
            segments = parse_transcript(p.read_text(encoding="utf-8"), p.suffix[1:])
            self.assertGreater(len(segments), 1)

    def test_plain_has_no_fabricated_times(self):
        self.assertTrue(all(s.start is None for s in self.segments))

    def test_srt_preserves_actual_times(self):
        parsed = parse_transcript((ROOT / "samples/learning.srt").read_text(), "srt")
        self.assertEqual(parsed[1].start, 12)
        self.assertEqual(parsed[-1].end, 108)

    def test_vtt_metadata_and_milliseconds(self):
        text = "WEBVTT\n\nNOTE annotation\nignored\n\ncue1\n00:01.250 --> 00:05.500 align:start\n" + "spoken words " * 15
        parsed = parse_transcript(text, "vtt")
        self.assertEqual(parsed[0].start, 1.25)
        self.assertEqual(parsed[0].end, 5.5)

    def test_invalid_inputs(self):
        for text, ext in [("", "txt"), ("a" * 200001, "txt"), ("a" * 200, "pdf"),
                          ("a" * 200 + "\x00", "txt"), ("broken " * 40, "srt"),
                          ("00:02.000 --> 00:01.000\n" + "word " * 40, "srt")]:
            with self.subTest(ext=ext, text=text[:20]), self.assertRaises(ValueError):
                parse_transcript(text, ext)

    def test_upload_encoding_and_size(self):
        self.assertEqual(decode_upload("বাংলা".encode("utf-8-sig")), "বাংলা")
        for data in [b"\xff\xfe", b"a" * 2000001]:
            with self.assertRaises(ValueError):
                decode_upload(data)

    def test_unknown_sources_rejected(self):
        data = self.episode.model_dump()
        data["overview"]["summary"]["source_ids"] = [999]
        with self.assertRaises(ValueError):
            validate_sources(Episode.model_validate(data), self.segments)

    def test_chapter_gaps_rejected(self):
        data = self.episode.model_dump()
        data["chapters"][0]["source_ids"] = [1]
        with self.assertRaises(ValueError):
            validate_sources(Episode.model_validate(data), self.segments)

    def test_question_index_rejected(self):
        data = self.episode.model_dump()
        data["quiz"]["questions"][0]["correct_index"] = 4
        with self.assertRaises(ValidationError):
            Episode.model_validate(data)

    def test_duplicate_options_rejected(self):
        data = self.episode.model_dump()
        data["quiz"]["questions"][0]["options"] = ["A", "a", "B", "C"]
        with self.assertRaises(ValueError):
            validate_sources(Episode.model_validate(data), self.segments)

    def test_component_revision_preserves_others(self):
        original = copy.deepcopy(self.episode.model_dump())
        new = self.episode.overview.model_copy(update={"themes": ["Revised"]})
        result = self.episode.model_copy(update={"overview": new})
        self.assertEqual(result.chapters, self.episode.chapters)
        self.assertEqual(result.quiz, self.episode.quiz)
        self.assertEqual(original, self.episode.model_dump())

    def test_export_bangla_and_sources(self):
        exported = export_markdown(self.episode, self.segments, "Bangla", {}, {})
        self.assertIn("Source transcript", exported)
        self.assertIn("S1", exported)
        self.assertIn("Answer: 1", exported)

    def test_successful_api_response_with_mock(self):
        body = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": self.episode.model_dump_json()}]}}]}
        with patch("ai.urllib.request.urlopen", return_value=io.BytesIO(json.dumps(body).encode())) as request:
            result = ai.generate(Episode, "Test", self.segments, "English", "test-key")
            self.assertEqual(result, self.episode)
            sent = request.call_args.args[0]
            self.assertNotIn("test-key", sent.full_url)
            self.assertIn("responseJsonSchema", json.loads(sent.data)["generationConfig"])

    def test_rate_limit_redacts_provider_body(self):
        err = urllib.error.HTTPError("https://example.test", 429, "secret", {}, io.BytesIO(b"sensitive"))
        with patch("ai.urllib.request.urlopen", side_effect=err) as request:
            with self.assertRaisesRegex(ai.AIError, "quota"):
                ai.generate(Episode, "Test", self.segments, "English", "test-key")
            self.assertEqual(request.call_count, 1)

    def test_timeout_has_no_automatic_retry(self):
        with patch("ai.urllib.request.urlopen", side_effect=TimeoutError) as request:
            with self.assertRaisesRegex(ai.AIError, "timed out"):
                ai.generate(Episode, "Test", self.segments, "English", "test-key")
            self.assertEqual(request.call_count, 1)

    def test_transient_503_retried_once(self):
        err = urllib.error.HTTPError("https://example.test", 503, "down", {}, None)
        with patch("ai.time.sleep"), patch("ai.urllib.request.urlopen", side_effect=err) as request:
            with self.assertRaises(ai.AIError):
                ai.generate(Episode, "Test", self.segments, "English", "test-key")
            self.assertEqual(request.call_count, 2)

    def test_malformed_ai_response(self):
        body = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "not JSON"}]}}]}
        with patch("ai.urllib.request.urlopen", side_effect=[io.BytesIO(json.dumps(body).encode()),io.BytesIO(json.dumps(body).encode())]):
            with self.assertRaisesRegex(ai.AIError, "checks"):
                ai.generate(Episode, "Test", self.segments, "English", "test-key")

    def test_truncated_ai_response(self):
        body = {"candidates": [{"finishReason": "MAX_TOKENS"}]}
        with patch("ai.urllib.request.urlopen", return_value=io.BytesIO(json.dumps(body).encode())):
            with self.assertRaisesRegex(ai.AIError, "incomplete"):
                ai.generate(Episode, "Test", self.segments, "English", "test-key")

    def test_groq_response_and_authentication(self):
        body = {"choices": [{"finish_reason": "stop", "message": {"content": self.episode.model_dump_json()}}]}
        with patch("ai.urllib.request.urlopen", return_value=io.BytesIO(json.dumps(body).encode())) as request:
            result = ai.generate(Episode, "Test", self.segments, "Bangla", "test-groq-key", "openai/gpt-oss-120b", provider="groq")
            self.assertEqual(result, self.episode)
            sent = request.call_args.args[0]
            self.assertEqual(sent.full_url, "https://api.groq.com/openai/v1/chat/completions")
            self.assertEqual(sent.get_header("Authorization"), "Bearer test-groq-key")
            self.assertEqual(json.loads(sent.data)["response_format"]["type"], "json_object")

    def test_groq_truncated_response_rejected(self):
        body = {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]}
        with patch("ai.urllib.request.urlopen", return_value=io.BytesIO(json.dumps(body).encode())):
            with self.assertRaisesRegex(ai.AIError, "incomplete"):
                ai.generate(Episode, "Test", self.segments, "English", "test-key", "openai/gpt-oss-120b", provider="groq")

    def test_process_budget(self):
        for _ in range(600):
            ai.reserve_request()
        with self.assertRaises(ai.AIError):
            ai.reserve_request()


if __name__ == "__main__":
    unittest.main()
