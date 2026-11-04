import json
import unittest
from types import SimpleNamespace

from jd_response import parse_image_comments


class ImageCommentsResponseTest(unittest.TestCase):
    def response(self, text, encoding="utf-8", status=200):
        return SimpleNamespace(
            status_code=status,
            headers={"Content-Type": "application/json; charset=" + encoding},
            content=text.encode(encoding),
        )

    def test_json_encodings_and_wrappers_preserve_chinese(self):
        data = {"imgComments": {"imgCommentCount": 1, "imgList": [
            {"commentVo": {"content": "安装方便，质量不错。", "score": 5}}
        ]}}
        text = json.dumps(data, ensure_ascii=False)
        for encoding in ("utf-8", "gbk", "gb18030"):
            for template in ("{}", "({})", "({});", "callback({});", "jd.callback({});"):
                with self.subTest(encoding=encoding, wrapper=template):
                    self.assertEqual(
                        parse_image_comments(self.response(template.format(text), encoding)), data
                    )

    def test_bom_and_whitespace(self):
        text = '\ufeff  {"imgComments":{"imgCommentCount":0}} \n'
        data = parse_image_comments(self.response(text))
        self.assertEqual(data["imgComments"]["imgList"], [])

    def test_non_json_and_invalid_shapes_are_diagnostic(self):
        cases = [
            ("系统繁忙", "系统繁忙"),
            ("<html>请登录</html>", "HTML"),
            ("  \n", "空内容"),
            ("undefined", "非 JSON"),
            ('{"success":false}', "imgComments"),
            ("[]", "imgComments"),
            ('{"imgComments":{"imgList":{}}}', "imgList"),
        ]
        for text, reason in cases:
            with self.subTest(text=text):
                with self.assertRaises(ValueError) as ctx:
                    parse_image_comments(self.response(text))
                self.assertIn(reason, str(ctx.exception))
                self.assertIn("HTTP 200", str(ctx.exception))
                self.assertIn("body_prefix=", str(ctx.exception))

    def test_http_error(self):
        with self.assertRaisesRegex(ValueError, "HTTP 403"):
            parse_image_comments(self.response("Forbidden", status=403))

    def test_wrong_encoding_fails_instead_of_corrupting_text(self):
        response = self.response("安装方便", "gbk")
        response.headers["Content-Type"] = "application/json; charset=utf-8"
        with self.assertRaisesRegex(ValueError, "响应解码失败"):
            parse_image_comments(response)


if __name__ == "__main__":
    unittest.main()
