import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from curl_cffi.requests.exceptions import SSLError, Timeout

from jd_submission import submit_product_comment


class ProductCommentSubmissionTest(unittest.TestCase):
    def setUp(self):
        self.logger = Mock()
        self.data = {"orderId": "order-test", "productId": "product-test", "content": "测试"}
        self.headers = {"Cookie": "test-cookie"}

    def submit(self):
        return submit_product_comment(
            "https://club.jd.com/myJdcomments/saveProductComment.action",
            self.headers, self.data, self.logger,
        )

    def response(self, text, status=200, encoding="utf-8"):
        return SimpleNamespace(
            status_code=status,
            headers={"Content-Type": "application/json;charset=" + encoding},
            content=text.encode(encoding),
        )

    def test_success_submits_once_with_timeout_and_certificate_verification(self):
        with patch("jd_submission.requests.post", return_value=self.response('{"success":true}')) as post:
            self.assertIs(self.submit(), True)
            post.assert_called_once()
            self.assertEqual(post.call_args.kwargs["data"], self.data)
            self.assertEqual(post.call_args.kwargs["headers"], self.headers)
            self.assertEqual(post.call_args.kwargs["timeout"], 30)
            self.assertIs(post.call_args.kwargs["verify"], True)
            self.assertIs(post.call_args.kwargs["allow_redirects"], False)

    def test_network_failure_never_retries(self):
        for exc in (SSLError("TLS EOF"), Timeout("read timeout")):
            with self.subTest(exception=type(exc).__name__):
                with patch("jd_submission.requests.post", side_effect=exc) as post:
                    self.assertIsNone(self.submit())
                    post.assert_called_once()
                self.logger.error.assert_called()

    def test_explicit_failure_supports_gbk(self):
        text = json.dumps({"success": False, "message": "请登录"}, ensure_ascii=False)
        with patch("jd_submission.requests.post", return_value=self.response(text, encoding="gbk")):
            self.assertIs(self.submit(), False)
        self.logger.warning.assert_called_once()

    def test_unexpected_response_is_unknown_and_never_retried(self):
        cases = [
            self.response(""), self.response("系统繁忙"),
            self.response("<html>登录</html>"), self.response("[]"),
            self.response('{"message":"unknown"}'),
            self.response('{"success":"false"}'),
            self.response('{"success":true}', status=502),
            self.response("", status=302),
        ]
        for response in cases:
            with self.subTest(content=response.content, status=response.status_code):
                with patch("jd_submission.requests.post", return_value=response) as post:
                    self.assertIsNone(self.submit())
                    post.assert_called_once()

    def test_invalid_encoding_is_unknown(self):
        response = self.response("测试", encoding="gbk")
        response.headers["Content-Type"] = "application/json;charset=utf-8"
        with patch("jd_submission.requests.post", return_value=response):
            self.assertIsNone(self.submit())


if __name__ == "__main__":
    unittest.main()
