"""Local usage accounting cannot reject requests after old daily quotas."""
from dataclasses import replace
from config.settings import settings
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from core.usage import UsageTracker
from core.rate_limit import RateLimiter


class UnlimitedUsage(unittest.TestCase):
    def test_old_daily_count_does_not_block_and_statistics_still_increment(self):
        with TemporaryDirectory() as tmp:
            tracker = UsageTracker(Path(tmp)/'usage.json')
            tracker.increment('user', 'chat', 100)
            with patch('core.rate_limit.settings', replace(settings, rate_limit_enabled=True, daily_chat_limit=100)):
                result = RateLimiter(tracker).check_and_increment('user', 'chat')
            self.assertTrue(result['allowed'])
            self.assertIsNone(result['limit'])
            self.assertIsNone(result['remaining'])
            self.assertEqual(tracker.get_usage('user')['chat'], 101)

    def test_test_data_root_isolates_usage_storage(self):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, HORIZON_USER_DATA_ROOT=tmp):
            tracker = UsageTracker()
            self.assertTrue(tracker.usage_path.is_relative_to(Path(tmp).resolve()))


if __name__ == '__main__': unittest.main()
