"""Dependency-free publication checks for committed buyer downloads.

These catch stale URL annotations and slide/notes links. Full page rendering,
PDF parsing, and presentation fidelity remain separate authoring-time checks.
"""
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET
from zipfile import ZipFile


DOWNLOADS = Path(__file__).resolve().parents[1] / "public" / "downloads"


class DownloadTests(unittest.TestCase):
    def test_pdf_link_annotations_use_the_new_hostname(self):
        for name in ("hormuz-overview.pdf", "hormuz-pilot-brief.pdf", "hormuz-trust-brief.pdf"):
            with self.subTest(name=name):
                raw = (DOWNLOADS / name).read_bytes()
                self.assertTrue(raw.startswith(b"%PDF-"))
                self.assertIn(b"/Author (Mehrdad Zaker)", raw)
                self.assertIn(b"/URI (https://usehormuz.github.io/", raw)
                self.assertNotIn(b"xpounder-com.github.io", raw)

    def test_editable_deck_and_notes_have_no_old_website_links(self):
        with ZipFile(DOWNLOADS / "hormuz-buyer-briefing.pptx") as deck:
            self.assertIsNone(deck.testzip())
            slides = [name for name in deck.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)]
            self.assertEqual(len(slides), 7)
            for name in slides:
                xml = ET.fromstring(deck.read(name))
                text = "".join(xml.itertext())
                self.assertIn("Mehrdad Zaker", text)
            final_slide = ET.fromstring(deck.read("ppt/slides/slide7.xml"))
            self.assertIn("usehormuz.github.io", "".join(final_slide.itertext()))
            for name in deck.namelist():
                if name.endswith((".xml", ".rels")):
                    self.assertNotIn(b"xpounder-com.github.io", deck.read(name), name)


if __name__ == "__main__":
    unittest.main()
