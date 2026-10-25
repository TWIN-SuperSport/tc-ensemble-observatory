import unittest
from verify_published_site import valid_archive_path

class PublicArchivePathTests(unittest.TestCase):
    def test_writer_basenames(self):
        for path in ("2026102512.json", "2026102512-202610JTWC30W.json", "2026102506-TARGET.json"):
            with self.subTest(path=path): self.assertTrue(valid_archive_path(path))

    def test_reject_non_basename_or_unsafe_suffix(self):
        for path in (None, 123, "", "../2026102512.json", "/2026102512.json", "https://example.com/2026102512.json", "2026102512-../x.json", "2026102512-A%2FB.json", "2026102512-A/B.json", "2026102512-A\\B.json", "2026102512.json?x", "2026102512.json#x", "2026102512.json\n", "2026102512-.json", "２０２６１０２５１２.json"):
            with self.subTest(path=path): self.assertFalse(valid_archive_path(path))

if __name__ == '__main__': unittest.main()
