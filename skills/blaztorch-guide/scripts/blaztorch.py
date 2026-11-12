#!/usr/bin/env python3
"""Bounded, read-only search of public Blaztorch resources. Python 3.10+."""
from __future__ import annotations

import argparse
import json
import sys
from urllib.parse import quote

from blaztorch_source import Client, ORIGIN, SourceError

RESOURCE_FIELDS = ('id', 'category', 'title', 'provider', 'url', 'summary', 'tags', 'status',
                   'participation', 'location', 'period', 'periodLabel', 'deadline', 'checkedDate',
                   'requirements', 'access', 'accessNotes', 'level', 'languages', 'resourceType',
                   'syllabus', 'practice', 'limitations', 'evidence', 'rulesUrl', 'applicationUrl',
                   'publicDetails', 'eventSchedule', 'publishedTimeline', 'timelineReview', 'eventView')
PROJECT_FIELDS = ('id', 'eventId', 'name', 'summary', 'topic', 'kind', 'team', 'projectUrl', 'productUrl',
                  'codeUrl', 'demoUrl', 'technologies', 'members', 'awards', 'checkedAt', 'linkStatus', 'linkCheckedAt')

def public_item(item, project=False):
    return {k: item[k] for k in (PROJECT_FIELDS if project else RESOURCE_FIELDS) if k in item}


def literal_match(item, query):
    """Case-insensitive literal token matching, without semantic expansion."""
    text = json.dumps(item, ensure_ascii=False).casefold()
    return all(word.casefold() in text for word in query.split())


def compact(item, project=False):
    fields = (('id', 'eventId', 'name', 'summary', 'topic', 'kind', 'projectUrl', 'codeUrl', 'demoUrl', 'awards', 'checkedAt')
              if project else ('id', 'category', 'title', 'provider', 'summary', 'url', 'tags', 'participation',
                               'level', 'access', 'languages', 'checkedDate', 'status', 'eventView',
                               'eventSchedule', 'publishedTimeline', 'timelineReview'))
    result = {k: item[k] for k in fields if k in item}
    result['websiteUrl'] = (ORIGIN + '/resources/winners/' + quote(item['id'], safe='') if project else
                            ORIGIN + '/resources?tab=' + ('learning' if item.get('category') == 'learning' else 'hackathon') +
                            '&event=' + quote(item['id'], safe=''))
    return result


def result_reference(props, item_id):
    """Copy an existing source association, without interpreting its meaning."""
    record = props.get('awardEvents', {}).get(item_id)
    if record is None:
        return None
    return {k: record[k] for k in ('id', 'title', 'sourceUrl', 'checkedAt', 'scopeNote', 'projectCount') if k in record}


def search(client, args):
    if args.kind == 'winners':
        found, reports, source_pages, total = [], [], [], 0
        for page in range(args.page, args.page + args.pages):
            props, meta = client.winners(args.query, page, args.code)
            reports.append(meta)
            total = props.get('total', 0)
            source_pages.append({'page': props.get('page'), 'pages': props.get('pages')})
            for raw in props['items']:
                item = public_item(raw, True)
                found.append(item if args.details else compact(item, True))
            if len(found) >= args.limit or page >= props.get('pages', page):
                break
        return {'kind': 'winners', 'sources': reports, 'websiteTotalMatches': total, 'sourcePages': source_pages,
                'order': 'website_order', 'items': found[:args.limit]}
    props, meta = client.resources()
    category = 'hackathon' if args.kind == 'events' else 'learning'
    results = []
    for raw in props['items']:
        if raw.get('category') != category or not literal_match(raw, args.query):
            continue
        item = public_item(raw)
        if args.status and item.get('status') != args.status:
            continue
        if args.level and item.get('level') != args.level:
            continue
        if args.access and item.get('access') != args.access:
            continue
        results.append(item)
    output = []
    for item in results[args.offset:args.offset + args.limit]:
        row = item if args.details else compact(item)
        if category == 'hackathon':
            row = {**row, 'publishedAwardResult': result_reference(props, item['id'])}
        output.append(row)
    return {'kind': args.kind, 'source': meta, 'catalogAsOf': props.get('asOf'), 'order': 'website_order',
            'totalMatches': len(results), 'offset': args.offset,
            'hasMore': args.offset + len(output) < len(results), 'items': output}


def detail(client, args):
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
                result['publishedAwardResult'] = result_reference(props, item['id'])
            return result
    raise SourceError('No matching item in the inspected public pages; use the website detail link')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh', action='store_true', help='重新读取官网，不使用15分钟缓存')
    parser.add_argument('--offline', action='store_true', help='仅用明确标记的旧缓存；不可据此确认报名开放')
    commands = parser.add_subparsers(dest='command', required=True)
    s = commands.add_parser('search', help='查赛事、课程或获奖作品')
    s.add_argument('--kind', choices=['events', 'learning', 'winners'], required=True)
    s.add_argument('--query', default='')
    s.add_argument('--limit', type=int, default=5)
    s.add_argument('--offset', type=int, default=0, help='赛事/课程在字面匹配结果中的偏移量')
    s.add_argument('--page', type=int, default=1, help='获奖作品查询的起始页')
    s.add_argument('--pages', type=int, default=1, help='获奖作品最多查询3页')
    s.add_argument('--status', help='按官网原始status字段精确匹配，不推算当前状态')
    s.add_argument('--level', choices=['beginner', 'intermediate', 'advanced', 'mixed'])
    s.add_argument('--access', choices=['free', 'free_account', 'freemium'], help='按官网原始access字段精确匹配')
    s.add_argument('--code', action='store_true', help='获奖作品需有目录记录的代码入口，仍需另行核对链接')
    s.add_argument('--details', action='store_true')
    d = commands.add_parser('detail')
    d.add_argument('--kind', choices=['events', 'learning', 'winners'], required=True)
    d.add_argument('--id', required=True)
    d.add_argument('--query', default='')
    args = parser.parse_args()
    if args.refresh and args.offline:
        parser.error('--refresh与--offline不能同时使用')
    if hasattr(args, 'limit') and not 1 <= args.limit <= 20:
        parser.error('--limit应为1至20')
    if hasattr(args, 'pages') and not 1 <= args.pages <= 3:
        parser.error('--pages应为1至3')
    if hasattr(args, 'offset') and args.offset < 0:
        parser.error('--offset不得为负数')
    if hasattr(args, 'page') and args.page < 1:
        parser.error('--page不得小于1')
    if args.command == 'search':
        if args.kind == 'winners' and (args.offset or args.status or args.level or args.access):
            parser.error('获奖作品使用--page分页，不支持赛事/课程字段筛选')
        if args.kind != 'winners' and (args.page != 1 or args.pages != 1 or args.code):
            parser.error('--page、--pages、--code仅用于获奖作品')
    if len(getattr(args, 'query', '')) > 160:
        parser.error('--query最多160个字符')
    client = Client(refresh=args.refresh, offline=args.offline)
    result = search(client, args) if args.command == 'search' else detail(client, args)
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
