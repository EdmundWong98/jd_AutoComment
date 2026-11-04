"""普通评价单次提交；结果不确定时由调用方停止本轮任务。"""

import json
import re

from curl_cffi import requests


def submit_product_comment(url, headers, data, logger):
    """返回 True（成功）、False（明确拒绝）、None（结果未知），不重试 POST。"""
    order_id = data.get("orderId")
    product_id = data.get("productId")
    try:
        response = requests.post(
            url, headers=headers, data=data, timeout=30,
            allow_redirects=False, verify=True,
        )
    except requests.RequestsError as exc:
        logger.error(
            "评价提交网络异常，结果未知：订单=%s 商品=%s 异常类型=%s；"
            "停止本轮且不自动重试，请到京东核对是否已评价。错误：%s",
            order_id, product_id, type(exc).__name__, exc,
        )
        return None

    content_type = response.headers.get("Content-Type", "")
    charset = re.search(r"charset\s*=\s*[\"']?([\w.-]+)", content_type, re.I)
    encoding = charset.group(1) if charset else "utf-8-sig"
    try:
        text = response.content.decode(encoding).strip().lstrip("\ufeff")
        payload = json.loads(text)
    except (ValueError, LookupError):
        payload = None

    if response.status_code == 200 and isinstance(payload, dict):
        if payload.get("success") is True:
            return True
        if payload.get("success") is False:
            logger.warning(
                "京东拒绝评价：订单=%s 商品=%s HTTP=%s 响应=%r",
                order_id, product_id, response.status_code, text[:200],
            )
            return False

    logger.error(
        "评价提交响应无法确认成功：订单=%s 商品=%s HTTP=%s "
        "Content-Type=%r bytes=%s body_prefix=%r；"
        "停止本轮且不自动重试，请到京东核对是否已评价。",
        order_id, product_id, response.status_code, content_type,
        len(response.content), response.content[:200],
    )
    return None
