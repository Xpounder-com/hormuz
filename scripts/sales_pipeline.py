#!/usr/bin/env python3
"""Private, local Hormuz lead ledger. No network calls or automatic outreach."""
import argparse
import csv
import json
import os
from collections import Counter
from datetime import date
from pathlib import Path

STAGES = ('new', 'contacted', 'review_booked', 'qualified', 'proposal', 'agreed', 'paid_pilot', 'support', 'closed_won', 'closed_lost', 'not_fit', 'qa', 'spam')
CLOSED = {'closed_won', 'closed_lost', 'not_fit', 'qa', 'spam'}
FIELDS = ('request_reference', 'received_date', 'name', 'email', 'organization', 'interest', 'campaign', 'stage', 'owner', 'next_action', 'next_action_date', 'booking_reference', 'workflow', 'business_outcome', 'success_measure', 'technical_owner', 'decision_owner', 'budget_evidence', 'timing', 'proposal_reference', 'agreement_reference', 'payment_reference', 'pilot_start', 'day_60_review', 'day_90_decision', 'notes')

def validate(row):
    if not row.get('request_reference') or row.get('stage') not in STAGES:
        raise ValueError('A unique request reference and known stage are required')
    for field in ('received_date', 'next_action_date', 'pilot_start', 'day_60_review', 'day_90_decision'):
        if row.get(field): date.fromisoformat(row[field])
    if row['stage'] not in CLOSED and not all(row.get(key) for key in ('owner', 'next_action', 'next_action_date')):
        raise ValueError('Every open inquiry needs an owner, next action, and date')
    if row['stage'] in ('qualified', 'proposal', 'agreed', 'paid_pilot', 'support', 'closed_won'):
        if not all(row.get(key) for key in ('business_outcome', 'success_measure', 'technical_owner', 'decision_owner', 'budget_evidence', 'timing')):
            raise ValueError('Qualification requires outcome, success measure, technical and decision owners, budget evidence, and timing')
    for stage, required in [('proposal', 'proposal_reference'), ('agreed', 'agreement_reference'), ('paid_pilot', 'payment_reference')]:
        if row['stage'] in STAGES[STAGES.index(stage):STAGES.index('closed_won')+1] and not row.get(required):
            raise ValueError(f'{required} is required; do not infer it from a checkout visit')
    if row['stage'] in ('paid_pilot', 'support', 'closed_won') and not all(row.get(key) for key in ('pilot_start', 'day_60_review', 'day_90_decision')):
        raise ValueError('An active pilot needs its start, day-60 review, and day-90 decision dates')

def load(path):
    if not path.exists(): return []
    with path.open(newline='') as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != FIELDS: raise ValueError('Unexpected ledger columns')
        return list(reader)

def save(path, rows):
    refs = set()
    for row in rows:
        validate(row)
        if row['request_reference'] in refs: raise ValueError('Duplicate request reference')
        refs.add(row['request_reference'])
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temp = path.with_suffix('.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        temp.replace(path)
        os.chmod(path, 0o600)
    finally:
        temp.unlink(missing_ok=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', type=Path, default=Path(__file__).resolve().parents[1] / 'marketing/private/leads.csv')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('init')
    ingest = commands.add_parser('upsert', help='Import one reviewed record from a private JSON file; merge by request_reference')
    ingest.add_argument('--input', type=Path, required=True)
    commands.add_parser('check')
    report = commands.add_parser('report', help='Counts and due references only; excludes QA and spam')
    report.add_argument('--date', default=date.today().isoformat())
    args = parser.parse_args()
    rows = load(args.file)
    if args.command == 'init':
        if args.file.exists(): raise ValueError('Ledger already exists; no changes made')
        save(args.file, [])
        print('Private ledger initialized; 0 inquiries.')
    elif args.command == 'upsert':
        patch = json.loads(args.input.read_text())
        if not isinstance(patch, dict) or set(patch) - set(FIELDS): raise ValueError('Input must contain only ledger fields')
        if any(not isinstance(value, str) for value in patch.values()): raise ValueError('All ledger fields must be strings')
        reference = patch.get('request_reference')
        if not reference: raise ValueError('request_reference is required')
        existing = next((row for row in rows if row['request_reference'] == reference), None)
        merged = {**dict.fromkeys(FIELDS, ''), **(existing or {}), **patch}
        validate(merged)
        rows = [row for row in rows if row['request_reference'] != reference] + [merged]
        save(args.file, rows)
        print('Saved one reviewed record. No email sent.')
    else:
        for row in rows: validate(row)
        if len({row['request_reference'] for row in rows}) != len(rows): raise ValueError('Duplicate request reference')
        if args.command == 'check': print(f'Ledger valid: {len(rows)} records.')
        else:
            today = date.fromisoformat(args.date)
            real = [row for row in rows if row['stage'] not in ('qa', 'spam')]
            print(json.dumps({'as_of': today.isoformat(), 'inquiries': len(real), 'stages': dict(Counter(row['stage'] for row in real)), 'due': [{'reference': row['request_reference'], 'stage': row['stage'], 'date': row['next_action_date']} for row in real if row['stage'] not in CLOSED and date.fromisoformat(row['next_action_date']) <= today]}, indent=2))

if __name__ == '__main__':
    try: main()
    except (ValueError, OSError, csv.Error) as exc: raise SystemExit(str(exc))
