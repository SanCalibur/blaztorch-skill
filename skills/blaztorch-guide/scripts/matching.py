"""Conservative calendar gates and explainable candidate preparation.

Qualification and task difficulty still require the agent to read the rules.
The heuristics here are not admission decisions or predictions of winning.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
DIMENSIONS = ('programming', 'ai', 'data', 'delivery', 'product', 'presentation')
ENDED = {'closed', 'submission_closed', 'cancelled'}
LABELS = {'programming': '编程', 'ai': 'AI接入', 'data': '数据处理', 'delivery': '交付部署',
          'product': '问题与产品', 'presentation': '展示表达'}


def aware(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result.astimezone(UTC) if result.tzinfo is not None else None
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


def boundaries(moment):
    if not isinstance(moment, dict):
        return None
    if moment.get('precision') == 'instant':
        utc, raw = aware(moment.get('utc')), aware(moment.get('raw'))
        if utc and raw and utc != raw:
            return None
        instant = utc or raw
        return (instant, instant) if instant else None
    if moment.get('precision') != 'date':
        return None
    try:
        day = date.fromisoformat(moment['date'])
        if moment.get('raw') != moment['date']:
            return None
        start = datetime.combine(day, time.min, UTC)
        zone = moment.get('timezone')
        if zone:
            try:
                tz = ZoneInfo(zone)
                return (datetime.combine(day, time.min, tz).astimezone(UTC),
                        datetime.combine(day + timedelta(days=1), time.min, tz).astimezone(UTC))
            except (ZoneInfoNotFoundError, ValueError):
                pass
        # No source timezone: retain the entire possible civil-date window.
        return start - timedelta(hours=14), start + timedelta(days=1, hours=12)
    except (KeyError, ValueError, TypeError, OverflowError):
        return None


def event_state(item, now, has_result=False):
    schedule = item.get('eventSchedule') or {}
    if not schedule and item.get('publishedTimeline'):
        schedule = {'dates': item['publishedTimeline'].get('dates', {}), 'conflicts': []}
    dates, conflicts = schedule.get('dates') or {}, schedule.get('conflicts') or []
    special = schedule.get('special')
    valid = aware(schedule.get('validUntil'))
    freshness = 'current' if valid and now <= valid else 'stale' if schedule else 'unknown'
    state = {'stage': 'unknown', 'group': 'unknown', 'joinability': 'needs_date_check',
             'freshness': freshness, 'verifiedRegistration': False,
             'registrationClose': dates.get('registrationClose'), 'submissionClose': dates.get('submissionClose')}

    def finish(stage, group, joinability, reason):
        return {**state, 'stage': stage, 'group': group, 'joinability': joinability, 'reason': reason}

    if has_result or item.get('status') in ENDED or special == 'cancelled':
        return finish('closed', 'ended', 'not_available', '已结束、取消或已公布正式结果')
    if special == 'postponed':
        return finish('postponed', 'unknown', 'needs_date_check', '赛事延期，不能沿用原日程推荐报名')

    def get(key):
        return boundaries(dates.get(key)) if key not in conflicts else None

    submission, registration, end = get('submissionClose'), get('registrationClose'), get('eventEnd')
    if submission:
        if now >= submission[1]:
            return finish('submission_closed', 'ended', 'not_available', '已超过作品提交截止边界')
        if now >= submission[0]:
            return finish('boundary_uncertain', 'unknown', 'needs_date_check', '提交截止只有日期或时区不完整，当前落在不确定边界内')
    elif 'submissionClose' in conflicts:
        return finish('conflict', 'unknown', 'needs_date_check', '作品提交截止资料存在冲突')
    elif end and now >= end[1]:
        return finish('closed', 'ended', 'not_available', '无提交截止资料，已超过已核实的活动结束日期')
    if registration:
        if now >= registration[1]:
            return finish('registration_closed', 'active', 'registered_only', '报名已截止；仅可向已报名者讨论继续参赛')
        if now >= registration[0]:
            return finish('boundary_uncertain', 'unknown', 'needs_date_check', '报名截止日期的时区边界尚不能确定')
    elif 'registrationClose' in conflicts:
        return finish('conflict', 'unknown', 'needs_date_check', '报名截止资料存在冲突')
    if not submission and not registration:
        return finish('unknown', 'unknown', 'needs_date_check', '缺少可用于判断新参赛者准入的截止资料')
    opening = get('registrationOpen')
    if opening and now < opening[0]:
        return finish('upcoming', 'active', 'not_yet_open', '公开日程显示报名尚未开始')
    if opening and opening[0] <= now < opening[1]:
        return finish('boundary_uncertain', 'unknown', 'needs_date_check', '报名开放仅有日期，边界待确认')
    if special == 'waitlist' or item.get('status') == 'waitlist':
        return finish('waitlist', 'active', 'waitlist', '页面显示候补，不能当作仍有正式名额')
    started = get('submissionOpen')
    stage = 'submitting' if started and now >= started[1] else 'calendar_active'
    return finish(stage, 'active', 'needs_rule_check', '公开日程尚未截止；仍须核对资格、名额与真实报名入口')


def validate_profile(profile):
    if not isinstance(profile, dict):
        raise ValueError('能力资料必须为JSON对象')
    skills = profile.get('skills', {})
    if not isinstance(skills, dict) or any(k not in DIMENSIONS for k in skills):
        raise ValueError('skills仅支持programming、ai、data、delivery、product、presentation')
    for name, value in skills.items():
        if not isinstance(value, dict):
            raise ValueError(f'{name}必须含level和evidence')
        level = value.get('level')
        if level is not None and (type(level) is not int or not 0 <= level <= 3):
            raise ValueError('level必须为0至3的整数，未知用null')
        if level is not None and not str(value.get('evidence', '')).strip():
            raise ValueError('已评估的能力必须附经验依据，不能凭空打分')
    for field in ('interests', 'languages', 'participation', 'registered_event_ids'):
        if field in profile and (not isinstance(profile[field], list) or any(not isinstance(v, str) for v in profile[field])):
            raise ValueError(field + '必须为字符串数组')
    if set(profile.get('participation', [])) - {'online', 'onsite', 'hybrid'}:
        raise ValueError('participation包含未知参与形式')
    if profile.get('team_size') is not None and (type(profile['team_size']) is not int or not 1 <= profile['team_size'] <= 100):
        raise ValueError('team_size必须为1至100的整数')
    hours = profile.get('hours_per_week')
    if hours is not None and (type(hours) not in (float, int) or not 0 <= hours <= 168):
        raise ValueError('hours_per_week必须为0至168之间的数字')
    return profile


def inferred_requirements(item):
    text = ' '.join(str(item.get(k) or '') for k in ('title', 'summary', 'requirements', 'limitations'))
    levels = {'programming': 1, 'ai': 1, 'data': 0, 'delivery': 1, 'product': 1, 'presentation': 1}
    clues = ['默认按应用原型准备度初筛，实际任务以赛题规则为准']
    if re.search(r'零代码|无代码|no.?code', text, re.I):
        levels['programming'] = 0
        clues.append('公开描述提及无代码')
    if re.search(r'从零训练|训练.{0,15}模型|微调|fine.?tun|算法竞赛|datathon|数据科学|machine.?learning', text, re.I):
        levels.update(programming=2, ai=2, data=2)
        clues.append('公开描述涉及训练、算法或数据处理')
    if re.search(r'机器人|硬件|FPGA|robotics', text, re.I):
        levels.update(programming=2, delivery=2)
        clues.append('公开描述涉及硬件，需要另行确认设备与队友')
    return levels, clues


def recommend(items, award_events, profile, now, limit=5):
    validate_profile(profile)
    if profile.get('hours_per_week') == 0:
        return {'basis': '用户当前没有可投入时间，暂不推荐报名', 'asOf': now.isoformat(),
                'candidates': [], 'needsDateCheck': [], 'candidateCount': 0,
                'nextStep': '可先查阅课程或历史作品，待时间安排明确后再匹配比赛'}
    skills = profile.get('skills', {})
    known = {k: v['level'] for k, v in skills.items() if v.get('level') is not None}
    registered = set(profile.get('registered_event_ids', []))
    modes, interests = profile.get('participation', []), profile.get('interests', [])
    candidates, needs_dates = [], []
    excluded = {'ended': 0, 'registration_closed': 0, 'participation': 0}
    for item in items:
        if item.get('category') != 'hackathon':
            continue
        calendar = event_state(item, now, item['id'] in award_events)
        if calendar['group'] == 'ended':
            excluded['ended'] += 1
            continue
        if calendar['joinability'] == 'registered_only' and item['id'] not in registered:
            excluded['registration_closed'] += 1
            continue
        mode = item.get('participation')
        # Hybrid does not establish that remote attendance is sufficient.
        if modes and mode in {'online', 'onsite'} and mode not in modes:
            excluded['participation'] += 1
            continue
        required, clues = inferred_requirements(item)
        gaps = [{'dimension': k, 'label': LABELS[k], 'current': known[k], 'suggested': v}
                for k, v in required.items() if k in known and known[k] < v]
        unknown = [LABELS[k] for k in DIMENSIONS if k not in known]
        text = ' '.join(str(item.get(k) or '') for k in ('title', 'summary', 'tags')).casefold()
        matched = [word for word in interests if word.casefold() in text]
        fit = '需补充能力信息' if len(unknown) >= 3 else '需要较多准备或队友' if len(gaps) >= 3 else '补强后考虑' if gaps else '可进一步核对'
        minutes = None
        close = boundaries(calendar.get('submissionClose'))
        if close:
            minutes = max(0, int((close[0] - now).total_seconds() / 60))
        entry = {'id': item['id'], 'title': item['title'], 'url': item.get('url'),
                 'summary': item.get('summary'), 'calendar': calendar, 'fit': fit,
                 'fitBasis': clues, 'skillGaps': gaps, 'unassessedSkills': unknown,
                 'matchedInterests': matched, 'minimumMinutesToSubmission': minutes,
                 'eligibility': 'not_checked', 'requirements': item.get('requirements'),
                 'publicDetails': item.get('publicDetails'), 'rulesUrl': item.get('rulesUrl'),
                 'participation': mode, 'checkedDate': item.get('checkedDate'),
                 '_sort': (-len(matched), len(gaps), len(unknown), calendar['freshness'] != 'current', item['title'])}
        if calendar['group'] == 'unknown':
            needs_dates.append(entry)
        else:
            candidates.append(entry)
    def trim(rows):
        rows.sort(key=lambda i: i['_sort'])
        return [{k: v for k, v in row.items() if k != '_sort'} for row in rows[:limit]]
    return {'basis': '只读公开资料的候选初筛；AI须复核真实规则后再给出报名建议',
            'asOf': now.isoformat(), 'candidates': trim(candidates), 'needsDateCheck': trim(needs_dates),
            'excluded': excluded, 'candidateCount': len(candidates),
            'profileConfidence': 'limited' if len(known) < 4 else 'based_on_supplied_evidence',
            'availability': {'hoursPerWeek': profile.get('hours_per_week'), 'teamSize': profile.get('team_size'),
                             'note': '周时间不能直接等同比赛窗口内可投入时间，需结合本场日期确认'}}
