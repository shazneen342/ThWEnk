import unittest
from ai import build_long_episode, chunk_segments, select_passages, AIError
from core import parse_transcript, validate_sources
from test_core import fixture

class LongTests(unittest.TestCase):
    def test_full_text_coverage_and_budgets(self):
        segments = parse_transcript(('Speaker: learning matters. ' * 25 + '\n') * 90)
        chunks = chunk_segments(segments)
        self.assertGreater(len(chunks), 10)
        self.assertEqual([s for c in chunks for s in c], segments)
        self.assertTrue(all(sum(len(s.text) for s in c) <= 3000 and len(c)<=35 for c in chunks))
        result = build_long_episode(segments, 'English', lambda schema,task,src,lang: fixture(src), {}, lambda *a: None)
        validate_sources(result, segments)
        self.assertEqual([i for c in result.chapters for i in c.source_ids], [s.id for s in segments])
        self.assertLessEqual(len(result.quiz.questions),5)

    def test_more_than_180_subtitles(self):
        text = '\n\n'.join(f'{i}\n00:{i//60:02}:{i%60:02},000 --> 00:{(i+1)//60:02}:{(i+1)%60:02},000\nWords about learning and evidence.' for i in range(400))
        segments = parse_transcript(text,'srt')
        self.assertEqual(len(segments),400)
        self.assertEqual(segments[-1].start,399)

    def test_resume_does_not_repeat_successful_section(self):
        src = parse_transcript(('many words here ' * 100 + '\n') * 12)
        cache, calls = {}, []
        def fail(schema,task,chunk,lang):
            calls.append(chunk[0].id)
            if len(calls)==2: raise AIError('quota')
            return fixture(chunk)
        with self.assertRaises(AIError): build_long_episode(src,'English',fail,cache,lambda *a:None)
        first = calls[0]
        result=build_long_episode(src,'English',fail,cache,lambda *a:None)
        self.assertEqual(calls.count(first),1)
        validate_sources(result,src)

    def test_retrieval_budget_and_original_ids(self):
        src=parse_transcript(('some words here ' * 100 + '\n') * 12)
        selected=select_passages(src,'words')
        self.assertLessEqual(sum(len(s.text) for s in selected),3000)
        self.assertTrue(set(s.id for s in selected).issubset(s.id for s in src))


class ChapterRangeTests(unittest.TestCase):
    def test_gaps_are_assigned_continuous_ranges(self):
        from ai import normalise_chapter_ranges
        from core import Chapter
        src=parse_transcript(('some words here ' * 35 + '\n') * 8)
        ep=fixture(src)
        ep=ep.model_copy(update={"chapters":[
            Chapter(title="First", explanation="First section", source_ids=[1,3]),
            Chapter(title="Second", explanation="Second section", source_ids=[5,8])]})
        fixed=normalise_chapter_ranges(ep,src)
        validate_sources(fixed,src)
        self.assertEqual(fixed.chapters[0].source_ids,[1,2,3,4])
        self.assertEqual(fixed.chapters[1].source_ids,[5,6,7,8])

    def test_unknown_chapter_source_still_rejected(self):
        from ai import normalise_chapter_ranges
        src=parse_transcript('words here ' * 30)
        ep=fixture(src)
        ep.chapters[0].source_ids=[999]
        with self.assertRaises(ValueError): normalise_chapter_ranges(ep,src)


class RootFormatTests(unittest.TestCase):
    def test_single_object_array_unwrapped(self):
        import json
        from ai import unwrap_json_object
        from core import Episode
        src=parse_transcript('words here ' * 30)
        ep=fixture(src)
        parsed=Episode.model_validate_json(unwrap_json_object(json.dumps([ep.model_dump()]),Episode))
        self.assertEqual(parsed,ep)

    def test_wrapper_unwrapped(self):
        import json
        from ai import unwrap_json_object
        from core import Episode
        src=parse_transcript('words here ' * 30)
        ep=fixture(src)
        self.assertEqual(Episode.model_validate_json(unwrap_json_object(json.dumps({"episode":ep.model_dump()}),Episode)),ep)

    def test_multiple_objects_not_silently_discarded(self):
        import json
        from pydantic import ValidationError
        from ai import unwrap_json_object
        from core import Episode
        ep=fixture(parse_transcript('words here ' * 30))
        with self.assertRaises(ValidationError):
            Episode.model_validate_json(unwrap_json_object(json.dumps([ep.model_dump(),ep.model_dump()]),Episode))


    def test_format_failure_retries_once(self):
        from unittest.mock import patch
        from ai import generate
        from core import Episode
        src=parse_transcript('words here ' * 30)
        ep=fixture(src)
        with patch('ai._generate_once',side_effect=[AIError('AI format checks failed: model_type'),ep]) as call:
            self.assertEqual(generate(Episode,'test',src,'English','fake'),ep)
            self.assertEqual(call.call_count,2)
