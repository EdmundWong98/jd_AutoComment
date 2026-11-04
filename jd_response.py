"""解析京东图片评论接口的 JSON / JSONP 响应。"""

import json
import re


def parse_image_comments(response):
    """按响应声明的编码解码；拒绝业务错误文本和不符合预期的结构。"""
    content_type = response.headers.get("Content-Type", "")
    charset = re.search(r"charset\s*=\s*[\"']?([\w.-]+)", content_type, re.I)
    encoding = charset.group(1) if charset else "utf-8-sig"
    context = "HTTP {}; Content-Type={!r}; bytes={}".format(
        response.status_code, content_type, len(response.content)
    )
    try:
        text = response.content.decode(encoding).strip().lstrip("\ufeff").strip()
    except (UnicodeError, LookupError) as exc:
        raise ValueError("响应解码失败: {}; encoding={!r}".format(context, encoding)) from exc

    def invalid(reason):
        return ValueError("{}; {}; body_prefix={!r}".format(reason, context, text[:200]))

    if response.status_code != 200:
        raise invalid("评论接口 HTTP 异常")
    if not text:
        raise invalid("评论接口返回空内容")
    if text == "系统繁忙":
        raise invalid("评论接口返回系统繁忙，请检查 Cookie 登录态或稍后重试")
    if text.startswith("<"):
        raise invalid("评论接口返回 HTML，可能是登录页、验证页或错误页")

    # 支持 ({...})、({...}); 和 callback({...});，不执行 JavaScript。
    wrapped = re.fullmatch(
        r"(?:[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)?\s*\((.*)\)\s*;?",
        text, re.S,
    )
    payload = wrapped.group(1) if wrapped else text
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise invalid("评论接口返回非 JSON 内容: {}".format(exc)) from exc

    if not isinstance(data, dict) or not isinstance(data.get("imgComments"), dict):
        raise invalid("评论接口响应缺少 imgComments 对象")
    if not isinstance(data["imgComments"].get("imgList"), list):
        # 无评论时部分响应省略列表或返回 null。
        if data["imgComments"].get("imgCommentCount") == 0 and data["imgComments"].get("imgList") is None:
            data["imgComments"]["imgList"] = []
        else:
            raise invalid("评论接口响应 imgList 不是数组")
    return data
