"""Offline checks for build_feed.py. Run: python3 -m unittest -v"""
import unittest
from datetime import timedelta

import build_feed as b


class Filters(unittest.TestCase):
    def test_roles(self):
        for t in ["Social Media Video Producer", "Video Editor", "Videographer / Editor",
                  "Motion Designer", "Senior Multimedia Producer", "Social Media Producer"]:
            self.assertTrue(b.role_ok(t), t)
        for t in ["Video Game Designer", "Video Remote Interpreter", "Software Engineer, Video",
                  "Photography/Videography Intern", "Communications Manager"]:
            self.assertFalse(b.role_ok(t), t)

    def test_locations(self):
        self.assertEqual(b.classify_location("Hybrid, New York, NY"), "NYC")
        self.assertEqual(b.classify_location("Brooklyn, New York"), "NYC")
        self.assertEqual(b.classify_location("Remote, United States"), "Remote (US)")
        self.assertIsNone(b.classify_location("Remote, Canada"))
        self.assertIsNone(b.classify_location("Albany, NY"))
        self.assertIsNone(b.classify_location("Remote", restrictions=["United Kingdom"]))
        self.assertEqual(b.classify_location("Remote", restrictions=["Canada", "United States"]), "Remote (US)")


class Merge(unittest.TestCase):
    def test_duplicates_collapse_to_best_source(self):
        d = b.NOW - timedelta(days=2)
        jobs = [
            b.job("Video Producer", "ACLU - National Office", "New York, NY", d, "https://idealist/x", "Idealist", mission=True),
            b.job("Video Producer", "American Civil Liberties Union", "New York, NY", d, "https://greenhouse/x", "Greenhouse", mission=True),
            b.job("Video Editor", "Some Co", "Remote", None, "https://himalayas/y", "Himalayas"),
        ]
        for j in jobs:
            j["where"] = "NYC"
        items = b.merge(jobs, {"seen": {}})
        self.assertEqual(len(items), 2)
        top = [i for i in items if i["title"] == "Video Producer"][0]
        self.assertEqual(top["url"], "https://greenhouse/x")
        self.assertEqual(top["also"], ["Idealist"])
        self.assertTrue(top["preferred"])

    def test_rss_is_valid_xml(self):
        j = b.job("Video Producer & Editor", "Org <A>", "New York, NY", b.NOW, "https://x.org/?a=1&b=2", "Idealist")
        j["where"] = "NYC"
        xml = b.build_rss(b.merge([j], {"seen": {}}))
        b.ET.fromstring(xml.encode())


if __name__ == "__main__":
    unittest.main()
