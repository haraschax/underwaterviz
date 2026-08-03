import csv
from datetime import date, datetime
from pathlib import Path
import tempfile
import unittest

from backfill_tides import (
    backfill_tides,
    find_snapshot_timestamps,
    load_tides,
    nearest_observation,
    write_tides,
)


class TideBackfillTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.snapshots = self.root / "snapshots"
        self.csv_path = self.root / "docs" / "tides.csv"

    def tearDown(self):
        self.temp_dir.cleanup()

    def add_snapshot(self, timestamp):
        image_path = self.snapshots / timestamp.strftime("%Y/%m/%d/%H.png")
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.touch()

    def test_finds_only_well_formed_snapshot_paths(self):
        self.add_snapshot(datetime(2026, 1, 2, 6))
        invalid_path = self.snapshots / "2026/01/bad/06.png"
        invalid_path.parent.mkdir(parents=True, exist_ok=True)
        invalid_path.touch()

        self.assertEqual(
            find_snapshot_timestamps(self.snapshots),
            {"2026-01-02 06:00"},
        )

    def test_nearest_observation_enforces_thirty_minute_limit(self):
        target = datetime(2026, 1, 2, 6)
        self.assertEqual(
            nearest_observation(target, {datetime(2026, 1, 2, 6, 6): 2.75}),
            2.75,
        )
        self.assertIsNone(
            nearest_observation(target, {datetime(2026, 1, 2, 6, 31): 2.75})
        )

    def test_backfill_batches_missing_snapshots_and_preserves_existing_values(self):
        jan_existing = datetime(2026, 1, 2, 6)
        jan_missing = datetime(2026, 1, 2, 7)
        feb_missing = datetime(2026, 2, 3, 8)
        for timestamp in (jan_existing, jan_missing, feb_missing):
            self.add_snapshot(timestamp)

        write_tides(self.csv_path, {"2026-01-02 06:00": 1.25})
        calls = []

        def fake_fetcher(begin_date, end_date):
            calls.append((begin_date, end_date))
            if begin_date.month == 1:
                return {datetime(2026, 1, 2, 7): 2.5}
            return {datetime(2026, 2, 3, 8, 6): 3.75}

        added, unresolved, errors = backfill_tides(
            self.snapshots,
            self.csv_path,
            fetcher=fake_fetcher,
        )

        self.assertEqual(added, 2)
        self.assertEqual(unresolved, 0)
        self.assertEqual(errors, [])
        self.assertEqual(
            calls,
            [
                (date(2026, 1, 2), date(2026, 1, 2)),
                (date(2026, 2, 3), date(2026, 2, 3)),
            ],
        )
        self.assertEqual(
            load_tides(self.csv_path),
            {
                "2026-01-02 06:00": 1.25,
                "2026-01-02 07:00": 2.5,
                "2026-02-03 08:00": 3.75,
            },
        )

        with self.csv_path.open(newline="") as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(rows[0], ["timestamp", "tide_ft"])
        self.assertEqual(
            [row[0] for row in rows[1:]],
            sorted(row[0] for row in rows[1:]),
        )


if __name__ == "__main__":
    unittest.main()
