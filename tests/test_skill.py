from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/blaztorch-guide/scripts'
sys.path.insert(0, str(SCRIPTS))
from blaztorch_source import Client, SourceError, check_url, parse_props
from matching import aware, boundaries, event_state, recommend, validate_profile, DIMENSIONS
from blaztorch import canonical, compact, public_item, relevant

NOW = datetime(2026, 11, 12, 8, tzinfo=timezone.utc)


def moment(raw):
    if 'T' in raw:
        return {'raw': raw, 'utc': raw, 'precision': 'instant', 'date': raw[:10], 'source': 'https://example.org/rules'}
    return {'raw': raw, 'date': raw, 'precision': 'date', 'source': 'https://example.org/rules'}


def event(**dates):
    return {'id': 'O-test', 'title': 'AI application event', 'category': 'hackathon', 'status': 'open',
            'summary': '智能体应用原型', 'requirements': '公开规则待复核', 'participation': 'online',
            'eventSchedule': {'dates': dates, 'conflicts': [], 'verifiedAt': '2026-11-11T00:00:00Z',
                              'validUntil': '2026-11-13T00:00:00Z'}}


def html(component='ResourceExplorer', items=None, long_text=None):
    rows = '17:I[7,[],"' + component + '"]\n:HL["/style.css","style"]\n'
    if long_text is not None:
        rows += '18:T' + format(len(long_text.encode('utf-8')), 'x') + ',' + long_text
    props = {'items': items if items is not None else [{'id': 'one', 'summary': '$18' if long_text else 'hello]world'}]}
    rows += '6:' + json.dumps([['$', '$L17', None, props]], ensure_ascii=False) + '\n'
    # Flight chunk boundaries need not coincide with record boundaries.
    return ''.join('<script>self.__next_f.push(' + json.dumps([1, chunk], ensure_ascii=False) + ')</script>'
                   for chunk in (rows[:35], rows[35:]))


class ParserTests(unittest.TestCase):
    def test_chunked_json_and_brackets_in_string(self):
        self.assertEqual(parse_props(html(), 'ResourceExplorer')['items'][0]['summary'], 'hello]world')

    def test_utf8_text_frame_without_newline_before_next_record(self):
        text = '中文条件：学生；无需账户。\n来源包含方括号]以及$金额' * 50
        self.assertEqual(parse_props(html(long_text=text), 'ResourceExplorer')['items'][0]['summary'], text)

    def test_allow_empty_search_but_not_empty_catalog(self):
        self.assertEqual(parse_props(html('AwardLibrary', []), 'AwardLibrary')['items'], [])
        with self.assertRaises(SourceError):
            parse_props(html(items=[]), 'ResourceExplorer')

    def test_refuses_schema_change_and_duplicates(self):
        for content in ('<html>new layout</html>', html(items=[{'id': 'same'}, {'id': 'same'}])):
            with self.assertRaises(SourceError):
                parse_props(content, 'ResourceExplorer')

    def test_no_javascript_execution_and_reference_failure(self):
        content = '<script>throw new Error("must not execute")</script>' + html()
        self.assertEqual(len(parse_props(content, 'ResourceExplorer')['items']), 1)
        with self.assertRaises(SourceError):
            parse_props(html(items=[{'id': 'one', 'summary': '$abc'}]), 'ResourceExplorer')

    def test_component_must_match(self):
        with self.assertRaises(SourceError):
            parse_props(html('AwardLibrary'), 'ResourceExplorer')

    def test_only_public_origin_path(self):
        check_url('https://blaztorch.com/resources?tab=winners&q=AI')
        for url in ('http://blaztorch.com/resources', 'https://evil.org/resources',
                    'https://blaztorch.com/api/resources', 'https://user@blaztorch.com/resources',
                    'https://blaztorch.com:444/resources'):
            with self.assertRaises(SourceError):
                check_url(url)

    def test_public_projection_omits_internal_fields(self):
        self.assertEqual(public_item({'id': 'one', 'reviewNote': 'private', 'importHash': 'private'}), {'id': 'one'})


class CacheTests(unittest.TestCase):
    def test_cache_is_explicit_and_refresh_bypasses_it(self):
        class Response(BytesIO):
            url = 'https://blaztorch.com/resources'
            headers = {'Content-Type': 'text/html; charset=utf-8'}
        with tempfile.TemporaryDirectory() as temp:
            client = Client(Path(temp))
            with patch.object(client.opener, 'open', return_value=Response(html().encode())) as fetch:
                _, meta = client.resources()
                self.assertFalse(meta['cache'])
                _, second = client.resources()
                self.assertTrue(second['cache'])
                self.assertEqual(second['retrievedAt'], meta['retrievedAt'])
                self.assertEqual(fetch.call_count, 1)
            offline = Client(Path(temp), offline=True)
            self.assertTrue(offline.resources()[1]['offline'])
            fresh = Client(Path(temp), refresh=True)
            with patch.object(fresh.opener, 'open', return_value=Response(html().encode())) as fetch:
                self.assertFalse(fresh.resources()[1]['cache'])
                fetch.assert_called_once()

    def test_offline_without_cache_is_not_empty_success(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(SourceError):
                Client(Path(temp), offline=True).resources()


class TimeTests(unittest.TestCase):
    def test_exact_offsets_and_midnight(self):
        m = moment('2026-11-28T00:00:00+01:00')
        m['utc'] = '2026-11-27T23:00:00Z'
        self.assertEqual(boundaries(m)[0], aware('2026-11-27T23:00:00Z'))
        item = event(submissionClose=m)
        self.assertEqual(event_state(item, aware('2026-11-27T23:00:00Z'))['group'], 'ended')

    def test_no_host_timezone_for_naive_clock(self):
        self.assertIsNone(boundaries(moment('2026-11-20T12:00:00')))

    def test_inconsistent_utc_is_not_a_fact(self):
        m = moment('2026-11-20T12:00:00+01:00')
        m['utc'] = '2026-11-20T12:00:00Z'
        self.assertIsNone(boundaries(m))

    def test_date_only_preserves_full_timezone_uncertainty(self):
        early, late = boundaries(moment('2026-11-12'))
        self.assertEqual(early, aware('2026-11-11T10:00:00Z'))
        self.assertEqual(late, aware('2026-11-13T12:00:00Z'))
        self.assertEqual(event_state(event(submissionClose=moment('2026-11-12')), NOW)['group'], 'unknown')

    def test_invalid_dates_are_not_normalized(self):
        self.assertIsNone(boundaries(moment('2026-02-30')))

    def test_results_or_closed_flag_cannot_be_resurrected(self):
        item = event(submissionClose=moment('2027-01-01T00:00:00Z'))
        self.assertEqual(event_state(item, NOW, True)['group'], 'ended')
        item['status'] = 'closed'
        self.assertEqual(event_state(item, NOW)['group'], 'ended')

    def test_submission_deadline_precedes_judging(self):
        item = event(submissionClose=moment('2026-11-10T00:00:00Z'), eventEnd=moment('2026-12-01T00:00:00Z'))
        self.assertEqual(event_state(item, NOW)['stage'], 'submission_closed')

    def test_earlier_event_end_does_not_override_future_submission(self):
        item = event(submissionClose=moment('2026-11-20T00:00:00Z'), eventEnd=moment('2026-11-10T00:00:00Z'))
        self.assertEqual(event_state(item, NOW)['group'], 'active')

    def test_registration_is_separate(self):
        item = event(registrationClose=moment('2026-11-10T00:00:00Z'), submissionClose=moment('2026-11-20T00:00:00Z'))
        self.assertEqual(event_state(item, NOW)['joinability'], 'registered_only')

    def test_conflicts_and_postponement_do_not_reuse_dates(self):
        item = event(submissionClose=moment('2026-11-20T00:00:00Z'))
        item['eventSchedule']['conflicts'] = ['submissionClose']
        self.assertEqual(event_state(item, NOW)['group'], 'unknown')
        item['eventSchedule']['special'] = 'postponed'
        self.assertEqual(event_state(item, NOW)['stage'], 'postponed')

    def test_no_dates_no_claim_of_open_registration(self):
        state = event_state(event(), NOW)
        self.assertEqual(state['group'], 'unknown')
        self.assertFalse(state['verifiedRegistration'])

    def test_old_observation_does_not_become_fresh(self):
        item = event(submissionClose=moment('2026-11-20T00:00:00Z'))
        item['eventSchedule']['validUntil'] = '2026-11-01T00:00:00Z'
        self.assertEqual(event_state(item, NOW)['freshness'], 'stale')


class MatchTests(unittest.TestCase):
    def test_assessment_requires_evidence_and_valid_levels(self):
        for profile in ({'skills': {'ai': {'level': 4, 'evidence': 'some'}}},
                        {'skills': {'ai': {'level': 1, 'evidence': ''}}},
                        {'skills': {'ai': {'level': True, 'evidence': 'some'}}}):
            with self.assertRaises(ValueError):
                validate_profile(profile)

    def test_unknown_is_not_zero_or_a_skill_gap(self):
        result = recommend([event(submissionClose=moment('2026-11-20T00:00:00Z'))], {}, {}, NOW)
        candidate = result['candidates'][0]
        self.assertEqual(candidate['skillGaps'], [])
        self.assertEqual(len(candidate['unassessedSkills']), 6)
        self.assertEqual(candidate['fit'], '需补充能力信息')
        self.assertEqual(candidate['eligibility'], 'not_checked')

    def test_closed_registration_is_only_for_existing_entrants(self):
        item = event(registrationClose=moment('2026-11-10T00:00:00Z'), submissionClose=moment('2026-11-20T00:00:00Z'))
        self.assertEqual(recommend([item], {}, {}, NOW)['candidates'], [])
        self.assertEqual(len(recommend([item], {}, {'registered_event_ids': ['O-test']}, NOW)['candidates']), 1)

    def test_unknown_dates_are_not_in_main_candidates(self):
        result = recommend([event()], {}, {}, NOW)
        self.assertFalse(result['candidates'])
        self.assertEqual(len(result['needsDateCheck']), 1)

    def test_no_available_time_does_not_trigger_registration_recommendation(self):
        self.assertFalse(recommend([event()], {}, {'hours_per_week': 0}, NOW)['candidates'])

    def test_interests_ordering_and_gaps_are_explained(self):
        one = event(submissionClose=moment('2026-11-20T00:00:00Z'))
        two = {**one, 'id': 'O-two', 'title': '数据科学与微调竞赛'}
        profile = {'skills': {k: {'level': 1, 'evidence': '明确的教程经验'} for k in DIMENSIONS}, 'interests': ['数据科学']}
        result = recommend([one, two], {}, profile, NOW)
        self.assertEqual(result['candidates'][0]['id'], 'O-two')
        self.assertTrue(result['candidates'][0]['skillGaps'])

    def test_related_queries_and_canonical_duplicates(self):
        self.assertTrue(relevant({'summary': 'AI agent course for beginner'}, '智能体 入门'))
        self.assertEqual(canonical({'id': 'x', 'projectUrl': 'https://devpost.com/software/a/?ref=one'}),
                         canonical({'id': 'y', 'projectUrl': 'https://devpost.com/software/a'}))
        self.assertEqual(canonical({'id': 'z'}), 'z')


if __name__ == '__main__':
    unittest.main()
