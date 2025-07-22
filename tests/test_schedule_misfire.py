import time
import unittest
from freezegun import freeze_time
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger


class ScheduleMisfireTests(unittest.TestCase):
    def test_daily_misfire_skipped_on_startup(self):
        executed = []

        def job():
            executed.append(True)

        with freeze_time("2024-01-01 09:00:00", tick=True):
            scheduler = BackgroundScheduler()
            scheduler.add_job(
                job,
                CronTrigger(hour=8, minute=0),
                misfire_grace_time=30,
            )
            scheduler.start()
            time.sleep(0.1)
            scheduler.shutdown(wait=False)

        self.assertEqual(executed, [])


if __name__ == "__main__":
    unittest.main()
