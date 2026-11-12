from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import subprocess
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/blaztorch-guide/scripts'
sys.path.insert(0, str(SCRIPTS))
from blaztorch_source import Client, SourceError, check_url, parse_props
from blaztorch import public_item, literal_match, search, detail


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


def arguments(**changes):
    values = {'kind': 'events', 'query': '', 'limit': 20, 'offset': 0, 'page': 1,
              'pages': 1, 'status': None, 'level': None, 'access': None, 'code': False, 'details': False}
    return SimpleNamespace(**{**values, **changes})


class PublicFixture:
    def __init__(self):
        self.items = [
            {'id': 'old-open', 'category': 'hackathon', 'title': 'AI agent event', 'status': 'open',
             'checkedDate': '2026-10-01', 'summary': 'source description',
             'eventSchedule': {'dates': {'submissionClose': {'raw': '2000-01-01', 'date': '2000-01-01', 'precision': 'date'}}, 'conflicts': ['submissionClose']}},
            {'id': 'future-closed', 'category': 'hackathon', 'title': 'Same name', 'status': 'closed',
             'checkedDate': '2026-11-12', 'deadline': '2099-01-01',
             'eventView': {'status': 'unknown', 'note': 'conflicting source observation'}},
            {'id': 'unknown', 'category': 'hackathon', 'title': 'Unknown event', 'status': 'unknown'},
            {'id': 'course', 'category': 'learning', 'title': 'Beginner course', 'access': 'free_account', 'level': 'advanced'}]

    def resources(self):
        return {'items': self.items, 'asOf': '2026-11-12', 'awardEvents': {
            'old-open': {'id': 'old-open', 'sourceUrl': 'https://example.org/result',
                         'checkedAt': '2026-11-10', 'projectCount': 2}}}, {'url': 'https://blaztorch.com/resources'}

    def winners(self, query='', page=1, code=False):
        self.request = (query, page, code)
        return {'items': [{'id': 'first', 'name': 'Same work', 'projectUrl': 'https://example.org/work', 'eventId': 'event1'},
                          {'id': 'second', 'name': 'Same work', 'projectUrl': 'https://example.org/work', 'eventId': 'event2'}],
                'total': 2, 'page': page, 'pages': page}, {'url': 'https://blaztorch.com/resources?tab=winners'}


class DataTransportTests(unittest.TestCase):
    def test_all_statuses_returned_in_source_order_despite_dates(self):
        result = search(PublicFixture(), arguments())
        self.assertEqual([r['id'] for r in result['items']], ['old-open', 'future-closed', 'unknown'])
        self.assertEqual(result['order'], 'website_order')
        self.assertEqual(result['items'][0]['status'], 'open')
        self.assertEqual(result['items'][0]['eventSchedule']['dates']['submissionClose']['raw'], '2000-01-01')
        self.assertEqual(result['items'][1]['eventView']['status'], 'unknown')
        for row in result['items']:
            for computed in ('calendar', 'fit', 'skillGaps', 'eligibility', 'minimumMinutesToSubmission'):
                self.assertNotIn(computed, row)

    def test_explicit_status_filter_is_literal_not_current_state(self):
        result = search(PublicFixture(), arguments(status='closed', details=True))
        self.assertEqual([r['id'] for r in result['items']], ['future-closed'])
        self.assertEqual(result['items'][0]['deadline'], '2099-01-01')

    def test_no_implicit_synonyms_or_difficulty_inference(self):
        fixture = PublicFixture()
        self.assertFalse(literal_match({'title': 'AI agent for beginner'}, '智能体'))
        self.assertTrue(literal_match({'title': 'AI agent for beginner'}, 'AGENT beginner'))
        self.assertEqual(search(fixture, arguments(kind='learning', level='beginner'))['items'], [])
        self.assertEqual(search(fixture, arguments(kind='learning', level='advanced'))['items'][0]['id'], 'course')

    def test_access_filter_uses_only_original_field(self):
        self.assertEqual(search(PublicFixture(), arguments(kind='learning', access='free'))['items'], [])
        result = search(PublicFixture(), arguments(kind='learning', access='free_account'))
        self.assertEqual(result['items'][0]['access'], 'free_account')

    def test_offset_has_no_relevance_ranking(self):
        result = search(PublicFixture(), arguments(offset=1, limit=1))
        self.assertEqual(result['items'][0]['id'], 'future-closed')
        self.assertEqual(result['totalMatches'], 3)
        self.assertTrue(result['hasMore'])

    def test_different_records_with_same_project_url_are_not_merged(self):
        fixture = PublicFixture()
        result = search(fixture, arguments(kind='winners', query='Same work', page=2, code=True))
        self.assertEqual([p['id'] for p in result['items']], ['first', 'second'])
        self.assertEqual(fixture.request, ('Same work', 2, True))
        self.assertEqual(result['sourcePages'], [{'page': 2, 'pages': 2}])

    def test_full_detail_does_not_resolve_conflicting_observations(self):
        fixture = PublicFixture()
        result = detail(fixture, arguments(id='future-closed'))
        self.assertEqual(result['item'], public_item(fixture.items[1]))
        self.assertNotIn('calendar', result)
        self.assertEqual(result['item']['status'], 'closed')
        self.assertEqual(result['item']['eventView']['status'], 'unknown')

    def test_result_link_is_source_data_without_an_end_verdict(self):
        result = detail(PublicFixture(), arguments(id='old-open'))
        self.assertEqual(result['item']['status'], 'open')
        self.assertEqual(result['publishedAwardResult']['sourceUrl'], 'https://example.org/result')
        self.assertNotIn('calendar', result)

    def test_recommendation_and_profile_commands_are_not_exposed(self):
        for command in ('recommend', 'profile-template'):
            result = subprocess.run([sys.executable, str(SCRIPTS / 'blaztorch.py'), command], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('invalid choice', result.stderr)


if __name__ == '__main__':
    unittest.main()
