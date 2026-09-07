import tempfile
import unittest
from pathlib import Path
from sales_pipeline import FIELDS, load, save, validate

class SalesPipelineTests(unittest.TestCase):
    def row(self):
        return {**dict.fromkeys(FIELDS, ''), 'request_reference': 'synthetic-test', 'stage': 'new', 'owner': 'Test owner', 'next_action': 'Review test fixture', 'next_action_date': '2026-09-08'}

    def commercial_row(self, stage):
        return {**self.row(), 'stage': stage, 'business_outcome': 'Synthetic outcome', 'success_measure': 'Synthetic measure', 'technical_owner': 'Test technical owner', 'decision_owner': 'Test decision owner', 'budget_evidence': 'Synthetic budget evidence', 'timing': 'Synthetic timing', 'proposal_reference': 'proposal-test', 'agreement_reference': 'agreement-test', 'payment_reference': 'payment-test'}

    def test_open_records_require_dated_ownership(self):
        for key in ('owner', 'next_action', 'next_action_date'):
            with self.assertRaises(ValueError): validate({**self.row(), key: ''})

    def test_payment_and_qualification_are_evidence_gates(self):
        for stage in ('qualified', 'proposal', 'agreed', 'paid_pilot'):
            with self.assertRaises(ValueError): validate({**self.row(), 'stage': stage})

    def test_standalone_support_does_not_invent_pilot_milestones(self):
        validate(self.commercial_row('support'))
        with self.assertRaisesRegex(ValueError, 'active pilot'):
            validate({**self.commercial_row('support'), 'pilot_start': '2026-09-10'})

    def test_paid_pilot_requires_chronological_milestones(self):
        row = {**self.commercial_row('paid_pilot'), 'pilot_start': '2026-09-10', 'day_60_review': '2026-11-09', 'day_90_decision': '2026-12-09'}
        validate(row)
        with self.assertRaisesRegex(ValueError, 'Pilot milestones must follow'):
            validate({**row, 'day_60_review': '2026-09-09'})
        with self.assertRaisesRegex(ValueError, 'Pilot milestones must follow'):
            validate({**row, 'day_90_decision': '2026-11-01'})

    def test_round_trip_is_private_and_duplicate_references_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'private/leads.csv'
            save(path, [self.row()])
            self.assertEqual(load(path), [self.row()])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            with self.assertRaises(ValueError): save(path, [self.row(), self.row()])
            self.assertEqual(len(load(path)), 1)

if __name__ == '__main__': unittest.main()
