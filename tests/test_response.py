import unittest

import uwebserver


class TestResponse(unittest.TestCase):
    def setUp(self) -> None:
        self.response = uwebserver.Response()

    def test_default_fields(self):
        self.assertEqual(self.response.body, None)
        self.assertEqual(self.response.headers.get("connection"), "close")
        self.assertEqual(self.response.headers.get("content-type"), "text/plain")
        self.assertEqual(self.response.status, b"200 OK")

    def test_set_body(self):
        cases = (
            (b"test", b"test", "Set body bytes"),
            ("test", b"test", "Set body str"),
            (bi := map(str.encode, "bytes iter test".split()), bi, "Set body iterable"),
        )

        for case, result, msg in cases:
            with self.subTest(msg):
                self.response.set_body(case)
                self.assertEqual(self.response.body, result)

    def test_set_status(self):
        cases = (
            (b"204 No Content", b"204 No Content", "Set status bytes"),
            ("303 See Other", b"303 See Other", "Set status str"),
        )
        for case, result, msg in cases:
            with self.subTest(msg):
                self.response.set_status(case)
                self.assertEqual(self.response.status, result)

    def test_set_header(self):
        self.response.set_header("content-length", "303")
        self.response.set_header("content-encoding", "gzip")
        self.response.set_header("server", "uWebServer")

        self.assertEqual(self.response.headers.get("content-length"), "303")
        self.assertEqual(self.response.headers.get("content-encoding"), "gzip")
        self.assertEqual(self.response.headers.get("server"), "uWebServer")

    def test_set_content_type(self):
        cases = (
            "text/html",
            "application/json",
        )
        for ct in cases:
            with self.subTest():
                self.response.set_content_type(ct)
                self.assertEqual(self.response.headers.get("content-type"), ct)


if __name__ == "__main__":
    unittest.main()
