import unittest

from progress import normalize_download_progress, parse_progress_line


class ProgressTests(unittest.TestCase):
    def test_known_total(self):
        event = parse_progress_line(
            'RECLIP_PROGRESS {"status":"downloading","downloaded_bytes":25,'
            '"total_bytes":100,"speed":10,"eta":7.5}'
        )
        progress = normalize_download_progress(event["data"], now=123.0)
        self.assertEqual(progress["percent"], 25.0)
        self.assertEqual(progress["speed_bps"], 10)
        self.assertFalse(progress["total_is_estimate"])
        self.assertEqual(progress["scope"], "current_stream")

    def test_unknown_total(self):
        progress = normalize_download_progress({"downloaded_bytes": 25}, now=123.0)
        self.assertIsNone(progress["percent"])
        self.assertIsNone(progress["eta_seconds"])

    def test_estimated_total_is_marked_and_real_total_wins(self):
        estimated = normalize_download_progress(
            {"downloaded_bytes": 25, "total_bytes_estimate": 200}, now=123.0
        )
        self.assertEqual(estimated["total_bytes"], 200)
        self.assertTrue(estimated["total_is_estimate"])
        self.assertEqual(estimated["percent"], 12.5)

        real = normalize_download_progress(
            {
                "downloaded_bytes": 25,
                "total_bytes": 100,
                "total_bytes_estimate": 200,
            },
            now=123.0,
        )
        self.assertEqual(real["total_bytes"], 100)
        self.assertFalse(real["total_is_estimate"])

    def test_invalid_numeric_values_are_unknown(self):
        progress = normalize_download_progress(
            {
                "downloaded_bytes": -1,
                "total_bytes": True,
                "speed": float("nan"),
                "eta": -2,
            },
            now=123.0,
        )
        self.assertIsNone(progress["downloaded_bytes"])
        self.assertIsNone(progress["total_bytes"])
        self.assertIsNone(progress["speed_bps"])
        self.assertIsNone(progress["eta_seconds"])

    def test_postprocess_event_and_oversized_line_are_filtered(self):
        event = parse_progress_line(
            'RECLIP_POSTPROCESS {"status":"started","postprocessor":"FFmpeg"}'
        )
        self.assertEqual(event["kind"], "postprocess")
        self.assertIsNone(parse_progress_line("RECLIP_PROGRESS " + "x" * (16 * 1024)))

    def test_malformed_or_unrelated_line(self):
        self.assertIsNone(parse_progress_line("ordinary diagnostic line"))
        self.assertIsNone(parse_progress_line("RECLIP_PROGRESS not-json"))


if __name__ == "__main__":
    unittest.main()
