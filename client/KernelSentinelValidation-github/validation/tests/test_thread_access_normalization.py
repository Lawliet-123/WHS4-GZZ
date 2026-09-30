import unittest
from validation.checks import check_handle, request_matches_callback


class ThreadAccessNormalizationTests(unittest.TestCase):
    def records(self, requested=0x802, duplicate=False, enforce=False):
        callback_access = requested | 0x1000
        after = callback_access & ~0x13 if enforce else callback_access
        flags = 8 | (128 if duplicate else 0) | (36 if enforce else 0)
        truth = dict(opened=True, error=0, query_status=0, granted=after, requested=requested,
                     thread=True, duplicate=duplicate, actor_pid=11, target_pid=22, tid=33, begin=100, end=200)
        pre = dict(type='handle_pre', actor_pid=11, target_pid=22, thread_id=33,
                   source_pid=11, recipient_pid=11, generation=7, operation_id=1,
                   timestamp_100ns=120, flags=flags, original_access=callback_access,
                   before_access=callback_access, after_access=after)
        post = dict(pre, type='handle_post', timestamp_100ns=140, status=0, granted_access=after)
        return truth, [pre, post]

    def test_observed_four_combinations(self):
        for request in (0x802, 0x81b):
            for duplicate in (False, True):
                truth, events = self.records(request, duplicate)
                result, detail = check_handle(truth, events, 'observe', 7)
                self.assertEqual(result, 'PASS')
                self.assertIn('callback original=', detail)

    def test_exact_requests_still_match(self):
        for thread in (False, True):
            for request in (0x800, 0x802, 0x1000, 0x187b):
                self.assertTrue(request_matches_callback(request, request, thread))

    def test_expansion_does_not_apply_to_processes(self):
        self.assertFalse(request_matches_callback(0x802, 0x1802, False))

    def test_without_suspend_bit_expansion_is_rejected(self):
        self.assertFalse(request_matches_callback(0x800, 0x1800, True))

    def test_extra_or_removed_bits_are_not_ignored(self):
        for original in (0x180a, 0x1800, 0x8020, 0x1803):
            self.assertFalse(request_matches_callback(0x802, original, True))

    def test_same_request_still_requires_identity_generation_time(self):
        for key, value in [('actor_pid', 12), ('target_pid', 23), ('thread_id', 34),
                           ('source_pid', 12), ('recipient_pid', 12), ('generation', 8),
                           ('timestamp_100ns', 99)]:
            truth, events = self.records()
            events[0][key] = value
            self.assertNotEqual(check_handle(truth, events, 'observe', 7)[0], 'PASS', key)

    def test_missing_or_wrong_post_does_not_pass(self):
        truth, events = self.records()
        self.assertEqual(check_handle(truth, events[:1], 'observe', 7)[0], 'FAIL')
        events[1]['operation_id'] = 2
        self.assertEqual(check_handle(truth, events, 'observe', 7)[0], 'FAIL')

    def test_independent_granted_mismatch_still_fails(self):
        truth, events = self.records()
        truth['granted'] ^= 0x1000
        self.assertEqual(check_handle(truth, events, 'observe', 7)[0], 'FAIL')

    def test_enforce_policy_is_not_relaxed(self):
        for request in (0x802, 0x81b):
            truth, events = self.records(request, enforce=True)
            self.assertEqual(check_handle(truth, events, 'enforce', 7)[0], 'PASS')
            # Matching callback input must not excuse failure to remove the actual 0x13 mask.
            truth['granted'] |= 2
            events[1]['granted_access'] |= 2
            self.assertEqual(check_handle(truth, events, 'enforce', 7)[0], 'FAIL')

    def test_lost_events_are_still_inconclusive(self):
        truth, events = self.records()
        self.assertEqual(check_handle(truth, events, 'observe', 7, lost=1)[0], 'INCONCLUSIVE')


if __name__ == '__main__':
    unittest.main()
