#!/usr/bin/env python3
"""Bounded, read-only search of public Blaztorch resources. Python 3.10+."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from urllib.parse import quote, urlsplit

from matching import DIMENSIONS, event_state, recommend, validate_profile
from blaztorch_source import Client, ORIGIN, SourceError

RESOURCE_FIELDS = ('id', 'category', 'title', 'provider', 'url', 'summary', 'tags', 'status',
                   'participation', 'location', 'period', 'periodLabel', 'deadline', 'checkedDate',
                   'requirements', 'access', 'accessNotes', 'level', 'languages', 'resourceType',
                   'syllabus', 'practice', 'limitations', 'evidence', 'rulesUrl', 'applicationUrl',
                   'publicDetails', 'eventSchedule', 'publishedTimeline', 'timelineReview', 'eventView')
PROJECT_FIELDS = ('id', 'eventId', 'name', 'summary', 'topic', 'kind', 'team', 'projectUrl', 'productUrl',
                  'codeUrl', 'demoUrl', 'technologies', 'members', 'awards', 'checkedAt', 'linkStatus', 'linkCheckedAt')
ALIASES = {'智能体': ('智能体', 'agent'), 'agent': ('智能体', 'agent'),
           '大模型': ('大模型', '大语言模型', 'llm', 'language model'),
           'llm': ('大模型', '大语言模型', 'llm', 'language model'),
           'rag': ('rag', '检索增强'), '入门': ('入门', 'beginner', '基础')}


def public_item(item, project=False):
    return {k: item[k] for k in (PROJECT_FIELDS if project else RESOURCE_FIELDS) if k in item}


def relevant(item, query):
    text = json.dumps(item, ensure_ascii=False).casefold()
    return all(any(alias in text for alias in ALIASES.get(word.casefold(), (word.casefold(),)))
               for word in query.split())


def compact(item, project=False):
    fields = (('id', 'eventId', 'name', 'summary', 'topic', 'kind', 'projectUrl', 'codeUrl', 'demoUrl', 'awards', 'checkedAt')
              if project else ('id', 'category', 'title', 'provider', 'summary', 'url', 'tags', 'participation',
                               'level', 'access', 'languages', 'checkedDate'))
    result = {k: item[k] for k in fields if k in item}
    result['websiteUrl'] = (ORIGIN + '/resources/winners/' + quote(item['id'], safe='') if project else
                            ORIGIN + '/resources?tab=' + ('learning' if item.get('category') == 'learning' else 'hackathon') +
                            '&event=' + quote(item['id'], safe=''))
    return result


def canonical(project):
    url = project.get('projectUrl')
    if not url:
        return project['id']
    value = urlsplit(url)
    return value.netloc.lower().removeprefix('www.') + value.path.rstrip('/')


def search(client, args, now):
    if args.kind == 'winners':
        found, seen, reports, total, visited = [], set(), [], 0, 0
        for page in range(1, args.pages + 1):
            props, meta = client.winners(args.query, page, args.code)
            reports.append(meta)
            total = props.get('total', 0)
            visited += 1
            for raw in props['items']:
                key = canonical(raw)
                if key not in seen:
                    seen.add(key)
                    item = public_item(raw, True)
                    found.append(item if args.details else compact(item, True))
            if len(found) >= args.limit or page >= props.get('pages', page):
                break
        return {'kind': 'winners', 'sources': reports, 'websiteTotalMatches': total, 'pagesRead': visited,
                'coverage': '仅所列公开查询页面；非对全部获奖项目的综合排名', 'items': found[:args.limit]}
    props, meta = client.resources()
    category = 'hackathon' if args.kind == 'events' else 'learning'
    results, states = [], {}
    for raw in props['items']:
        if raw.get('category') != category or not relevant(raw, args.query):
            continue
        item = public_item(raw)
        if args.kind == 'events':
            state = event_state(item, now, item['id'] in props.get('awardEvents', {}))
            if args.state != 'all' and state['group'] != args.state:
                continue
            states[item['id']] = state
        if args.level and item.get('level') != args.level:
            continue
        if args.free and item.get('access') != 'free':
            continue
        results.append(item)
    results.sort(key=lambda r: (r.get('checkedDate', ''), r.get('id', '')), reverse=True)
    output = []
    for item in results[:args.limit]:
        row = item if args.details else compact(item)
        if item['id'] in states:
            row = {**row, 'calendar': states[item['id']]}
        output.append(row)
    return {'kind': args.kind, 'source': meta, 'catalogAsOf': props.get('asOf'), 'evaluatedAt': now.isoformat(),
            'totalMatches': len(results), 'items': output}


def detail(client, args, now):
    if args.kind == 'winners':
        # The public item page does not deliver a client-side data component.
        # Find its entry through a bounded name query supplied by the user/agent.
        if not args.query:
            raise ValueError('获奖项目详情请同时提供--query项目名称，以限定公开查询范围')
        for page in range(1, 4):
            props, meta = client.winners(args.query, page)
            item = next((p for p in props['items'] if p['id'] == args.id), None)
            if item:
                return {'source': meta, 'item': public_item(item, True), 'websiteUrl': ORIGIN + '/resources/winners/' + quote(args.id, safe='')}
            if page >= props.get('pages', page):
                break
    else:
        props, meta = client.resources()
        category = 'hackathon' if args.kind == 'events' else 'learning'
        item = next((i for i in props['items'] if i['id'] == args.id and i.get('category') == category), None)
        if item:
            result = {'source': meta, 'catalogAsOf': props.get('asOf'), 'item': public_item(item)}
            if category == 'hackathon':
                result['calendar'] = event_state(item, now, item['id'] in props.get('awardEvents', {}))
            return result
    raise SourceError('No matching item in the inspected public pages; use the website detail link')


def profile_template():
    return {'skills': {k: {'level': None, 'evidence': ''} for k in DIMENSIONS}, 'interests': [],
            'languages': [], 'hours_per_week': None, 'team_size': None,
            'participation': [], 'registered_event_ids': [],
            'notes': '示例空模板：未知保留null，按具体经历补充；资料只在当前会话使用，保存前征得使用者同意。'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh', action='store_true', help='重新读取官网，不使用15分钟缓存')
    parser.add_argument('--offline', action='store_true', help='仅用明确标记的旧缓存；不可据此确认报名开放')
    commands = parser.add_subparsers(dest='command', required=True)
    s = commands.add_parser('search', help='查赛事、课程或获奖作品')
    s.add_argument('--kind', choices=['events', 'learning', 'winners'], required=True)
    s.add_argument('--query', default='')
    s.add_argument('--limit', type=int, default=5)
    s.add_argument('--pages', type=int, default=1, help='获奖作品最多查询3页')
    s.add_argument('--state', choices=['active', 'ended', 'unknown', 'all'], default='active')
    s.add_argument('--level', choices=['beginner', 'intermediate', 'advanced', 'mixed'])
    s.add_argument('--free', action='store_true', help='学习资源仅限公开材料免费')
    s.add_argument('--code', action='store_true', help='获奖作品需有目录记录的代码入口，仍需另行核对链接')
    s.add_argument('--details', action='store_true')
    d = commands.add_parser('detail')
    d.add_argument('--kind', choices=['events', 'learning', 'winners'], required=True)
    d.add_argument('--id', required=True)
    d.add_argument('--query', default='')
    r = commands.add_parser('recommend', help='按能力证据筛选赛事候选，资格仍需AI核对')
    r.add_argument('--profile', type=Path, required=True)
    r.add_argument('--limit', type=int, default=5)
    commands.add_parser('profile-template')
    args = parser.parse_args()
    if args.refresh and args.offline:
        parser.error('--refresh与--offline不能同时使用')
    if hasattr(args, 'limit') and not 1 <= args.limit <= 20:
        parser.error('--limit应为1至20')
    if hasattr(args, 'pages') and not 1 <= args.pages <= 3:
        parser.error('--pages应为1至3')
    if len(getattr(args, 'query', '')) > 160:
        parser.error('--query最多160个字符')
    client = Client(refresh=args.refresh, offline=args.offline)
    now = datetime.now(timezone.utc)
    if args.command == 'profile-template':
        result = profile_template()
    elif args.command == 'search':
        result = search(client, args, now)
    elif args.command == 'detail':
        result = detail(client, args, now)
    else:
        if str(args.profile) == '-':
            content = sys.stdin.read(65537)
        else:
            if args.profile.stat().st_size > 65536:
                raise ValueError('能力资料过大，应只包含最小必要字段')
            content = args.profile.read_text('utf-8-sig')
        if len(content.encode('utf-8')) > 65536:
            raise ValueError('能力资料过大，应只包含最小必要字段')
        profile = validate_profile(json.loads(content.lstrip('\ufeff')))
        props, meta = client.resources()
        result = recommend([public_item(i) for i in props['items']], props.get('awardEvents', {}), profile, now, args.limit)
        result.update(source=meta, catalogAsOf=props.get('asOf'))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    try:
        main()
    except (SourceError, ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'error': str(exc), 'fallback': ORIGIN + '/resources',
                          'instruction': '使用浏览器读取公开页面；不得编造接口、日期、资格或空结果。'}, ensure_ascii=False), file=sys.stderr)
        sys.exit(2)
