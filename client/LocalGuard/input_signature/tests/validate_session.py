"""Validate export shape/timing, not the truth of a tester's normal/cheat labels."""
import argparse
import json
import math
from pathlib import Path

FIELDS = {'session_id','player_id','module','timestamp_ms','evidence','reasons','raw_score'}


def require(condition, message):
    if not condition: raise ValueError(message)


def validate(path):
    path = Path(path)
    manifest = json.loads((path / 'manifest.json').read_text(encoding='utf-8-sig'))
    errors = []
    warnings = []
    count = 0
    zeros = 0
    maximum = 0
    duration = manifest.get('duration_ms')
    if type(duration) is not int or duration < 0: errors.append('Session is not finalized / invalid duration_ms')
    if not (path / 'raw').is_dir() or not any((path / 'raw').iterdir()): errors.append('Missing raw logs')
    last = {}
    counts = {}
    with (path / 'events.jsonl').open(encoding='utf-8-sig') as stream:
        for line_no, line in enumerate(stream, 1):
            try:
                event = json.loads(line)
                require(isinstance(event,dict) and set(event) == FIELDS, 'wrong event fields')
                require(event['session_id'] == manifest['session_id'], 'wrong session_id')
                require(all(isinstance(event[k],str) and event[k] for k in ('player_id','module')), 'invalid identity')
                t = event['timestamp_ms']
                require(type(t) is int and t >= 0, 'invalid timestamp_ms')
                require(type(duration) is int and t <= duration, 'event after session end')
                score = event['raw_score']
                require(type(score) in (int,float) and math.isfinite(score) and score >= 0, 'invalid score')
                require(isinstance(event['evidence'],dict), 'invalid evidence')
                require(isinstance(event['reasons'],list) and all(isinstance(r,str) for r in event['reasons']), 'invalid reasons')
                require(event['module'] in manifest['modules'], 'undeclared module')
                key = (event['module'],event['player_id'])
                require(t >= last.get(key,0), 'timestamps out of order')
                last[key] = t
                counts[event['module']] = counts.get(event['module'],0) + 1
                count += 1; zeros += int(score == 0); maximum = max(maximum,score)
            except (AssertionError,ValueError,KeyError,TypeError) as exc:
                errors.append(f'line {line_no}: {exc}')
    if counts != manifest.get('event_counts'): errors.append('Event counts differ from manifest')
    previous = 0
    for interval in manifest.get('cheat_intervals',[]):
        on = interval.get('on_ms'); off = interval.get('off_ms')
        if type(on) is not int or type(off) is not int or not 0 <= previous <= on < off <= (duration or 0):
            errors.append('Missing/invalid/overlapping cheat ON/OFF interval')
        else: previous = off
    if manifest.get('label') == 'normal' and manifest.get('cheat_intervals'): errors.append('Normal test has cheat intervals')
    if manifest.get('label') == 'cheat' and not manifest.get('cheat_intervals'): errors.append('Cheat test missing ON/OFF markers')
    if manifest.get('data_origin') != 'live_game': warnings.append('NOT real-game normal/cheat validation')
    if manifest.get('label') == 'unknown': warnings.append('Ground-truth label is unknown')
    if manifest.get('collection_errors'): warnings.append('Collection failures exist; do not fill gaps with zeros')
    if not count: warnings.append('No scored observations; this is not a clean result')
    if not manifest.get('comparison_ready'): warnings.append('Not marked ready for real normal/cheat comparison')
    return {'valid_format': not errors, 'event_count': count, 'zero_score_count': zeros,
            'maximum_score': maximum, 'errors': errors, 'warnings': warnings,
            'comparison_ready': bool(manifest.get('comparison_ready')) and not errors}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('session',type=Path)
    args = parser.parse_args()
    try:
        result = validate(args.session)
    except Exception as exc:
        result = {'valid_format':False,'errors':[str(exc)]}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['valid_format'] else 1


if __name__ == '__main__': raise SystemExit(main())
