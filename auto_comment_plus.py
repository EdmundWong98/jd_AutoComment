# -*- coding: utf-8 -*-
# @Time : 2022/2/8 20:50
# @Author : @qiu-lzsnmb and @Dimlitter
# @File : auto_comment_plus.py

import argparse
import copy
import hashlib
import io
import json
import logging
import os
import random
import sys
import time
import urllib
import uuid

import jieba  # just for linting
import jieba.analyse
import requests
import yaml
from lxml import etree

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

import jdspider

# from http2_adapter import Http2Adapter

# constants
CONFIG_PATH = "./config.yml"
USER_CONFIG_PATH = "./config.user.yml"
ORDINARY_SLEEP_SEC = 10
SUNBW_SLEEP_SEC = 5
REVIEW_SLEEP_SEC = 10
SERVICE_RATING_SLEEP_SEC = 15

# 图片处理配置
IMAGE_CONFIG = {
    "max_size": 2 * 1024 * 1024,  # 2MB
    "max_dimension": 1200,  # 最大边长
    "quality": 90,  # 默认图片质量
    "retry": {
        "max_attempts": 3,
        "initial_delay": 1,
    }
}

# 已使用的图片指纹集合，用于去重
_used_fingerprints = set()

# logging with styles
# Reference: https://stackoverflow.com/a/384125/12002560
_COLORS = {
    "black": 0,
    "red": 1,
    "green": 2,
    "yellow": 3,
    "blue": 4,
    "magenta": 5,
    "cyan": 6,
    "white": 7,
}

_RESET_SEQ = "\033[0m"
_COLOR_SEQ = "\033[1;%dm"
_BOLD_SEQ = "\033[1m"
_ITALIC_SEQ = "\033[3m"
_UNDERLINED_SEQ = "\033[4m"

_FORMATTER_COLORS = {
    "DEBUG": _COLORS["blue"],
    "INFO": _COLORS["green"],
    "WARNING": _COLORS["yellow"],
    "ERROR": _COLORS["red"],
    "CRITICAL": _COLORS["red"],
}


def format_style_seqs(msg: str, use_style: bool = True):
    if use_style:
        msg = msg.replace("$RESET", _RESET_SEQ)
        msg = msg.replace("$BOLD", _BOLD_SEQ)
        msg = msg.replace("$ITALIC", _ITALIC_SEQ)
        msg = msg.replace("$UNDERLINED", _UNDERLINED_SEQ)
    else:
        msg = msg.replace("$RESET", "")
        msg = msg.replace("$BOLD", "")
        msg = msg.replace("$ITALIC", "")
        msg = msg.replace("$UNDERLINED", "")


class StyleFormatter(logging.Formatter):
    def __init__(self, fmt=None, datefmt=None, use_style=True):
        logging.Formatter.__init__(self, fmt, datefmt)
        self.use_style = use_style

    def format(self, record):
        rcd = copy.copy(record)
        levelname = rcd.levelname
        if self.use_style and levelname in _FORMATTER_COLORS:
            levelname_with_color = "%s%s%s" % (
                _COLOR_SEQ % (30 + _FORMATTER_COLORS[levelname]),
                levelname,
                _RESET_SEQ,
            )
            rcd.levelname = levelname_with_color
        return logging.Formatter.format(self, rcd)


# 生成图片内容指纹用于去重
def generate_image_fingerprint(image_data: bytes) -> str:
    """生成图片MD5指纹"""
    return hashlib.md5(image_data).hexdigest()


# 处理图片确保符合京东上传要求
def process_image(image_data: bytes, logger=None) -> bytes:
    """处理图片：格式转换、尺寸调整、质量压缩、添加水印防重复"""
    if not PIL_AVAILABLE:
        return image_data

    try:
        img = Image.open(io.BytesIO(image_data))

        # 格式转换：统一转为JPEG
        if img.format != 'JPEG':
            img = img.convert('RGB')

        # 尺寸调整（最长边不超过 max_dimension）
        max_size = IMAGE_CONFIG["max_dimension"]
        width, height = img.size
        if max(width, height) > max_size:
            ratio = max_size / max(width, height)
            new_size = (int(width * ratio), int(height * ratio))
            img = img.resize(new_size, Image.LANCZOS)

        # 添加随机水印防止重复
        draw = ImageDraw.Draw(img)
        watermark = str(random.getrandbits(64))
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 12)
        except Exception:
            font = ImageFont.load_default()
        # 在角落添加半透明水印
        draw.text((10, 10), watermark, font=font, fill=(200, 200, 200, 128))

        # 质量压缩控制在 max_size 以内
        output = io.BytesIO()
        quality = IMAGE_CONFIG["quality"]
        while quality > 10:
            output.seek(0)
            output.truncate()
            img.save(output, format='JPEG', quality=quality)
            if output.tell() < IMAGE_CONFIG["max_size"]:
                break
            quality -= 5

        result = output.getvalue()
        if logger:
            logger.debug(f"图片处理完成: 原始大小 {len(image_data)/1024:.1f}KB -> 处理后 {len(result)/1024:.1f}KB")
        return result

    except Exception as e:
        if logger:
            logger.warning(f"图片处理失败: {str(e)}，使用原始图片")
        return image_data


# 生成随机文件名
def generate_unique_filename():
    # 获取当前时间戳的最后4位
    timestamp = str(int(time.time()))[-4:]

    # 生成 UUID 的前4位
    unique_id = str(uuid.uuid4().int)[:4]

    # 组合生成10位的唯一文件名
    # unique_filename = f"{timestamp}{unique_id}.jpg"
    # 组合生成类似iPhone的唯一文件名
    unique_filename = f"IMG_{timestamp}.jpg"

    return unique_filename


# 增强型下载图片（带处理）
def download_image(img_url, file_name, logger=None):
    fullUrl = f"https:{img_url}"
    try:
        response = requests.get(fullUrl, timeout=30)
        if response.status_code == 200:
            image_data = response.content

            # 生成指纹进行去重检查
            fingerprint = generate_image_fingerprint(image_data)
            if fingerprint in _used_fingerprints:
                if logger:
                    logger.debug(f"图片已使用过 (fingerprint: {fingerprint[:8]}...), 尝试获取其他图片")
                return None
            _used_fingerprints.add(fingerprint)

            # 处理图片
            processed_data = process_image(image_data, logger)

            directory = "img"
            if not os.path.exists(directory):
                os.makedirs(directory)
            file_path = os.path.join(directory, file_name)
            with open(file_path, "wb") as file:
                file.write(processed_data)
            return file_path
        else:
            if logger:
                logger.warning(f"图片下载失败，HTTP状态码: {response.status_code}")
            return None
    except Exception as e:
        if logger:
            logger.warning(f"图片下载异常: {str(e)}")
        return None


# 获取增强型上传请求头
def get_upload_headers(base_headers: dict) -> dict:
    """构建完整的上传请求头"""
    enhanced = base_headers.copy()
    enhanced.update({
        'Referer': 'https://club.jd.com/myJdcomments/myJdcomment.action',
        'Origin': 'https://club.jd.com',
        'X-Requested-With': 'XMLHttpRequest',
        'Accept': '*/*',
    })
    return enhanced


# 带重试机制的上传图片到JD接口
def upload_image_with_retry(filename, file_path, session, headers, logger=None, max_retries=None):
    """带指数退避重试的图片上传"""
    if max_retries is None:
        max_retries = IMAGE_CONFIG["retry"]["max_attempts"]

    retry_delay = IMAGE_CONFIG["retry"]["initial_delay"]
    enhanced_headers = get_upload_headers(headers)

    for attempt in range(max_retries):
        try:
            with open(file_path, 'rb') as f:
                files = {
                    'name': (None, filename),
                    'Filedata': (filename, f, 'image/jpeg'),
                    'upload': (None, 'Submit Query'),
                }

                response = session.post(
                    "https://club.jd.com/myJdcomments/ajaxUploadImage.action",
                    headers=enhanced_headers,
                    files=files,
                    timeout=30
                )

            if response.status_code == 200 and '.jpg' in response.text:
                if logger:
                    logger.debug(f"图片上传成功: {filename}")
                return response

            if logger:
                logger.warning(f"上传尝试 {attempt + 1} 失败: HTTP {response.status_code}, 响应: {response.text[:100]}")

            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                retry_delay *= 2  # 指数退避

        except requests.RequestException as e:
            if logger:
                logger.warning(f"上传尝试 {attempt + 1} 异常: {str(e)}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                retry_delay *= 2

    if logger:
        logger.error(f"图片上传最终失败: {filename}，已达到最大重试次数")
    return None


# 兼容旧接口的上传函数
def upload_image(filename, file_path, session, headers, logger=None):
    return upload_image_with_retry(filename, file_path, session, headers, logger)


# 评价生成
def generation(pname: str, product_id: str = None, _class: int = 0, _type: int = 1, opts: object = None):
    """
    生成评价内容
    :param pname: 商品名称
    :param product_id: 商品ID（可选，直接传入可避免搜索反爬）
    :param _class: 0=评价，1=提取关键词
    :param _type: 1=好评相关，0=追评相关
    :param opts: 日志等选项
    """
    result = []
    opts = opts or {}

    # 增加对增值服务的评价鉴别
    if "赠品" in pname or "非实物" in pname or "增值服务" in pname:
        result = [
            "赠品挺好的。",
            "很贴心，能有这样免费赠送的赠品!",
            "正好想着要不要多买一份增值服务，没想到还有这样的赠品。",
            "赠品正合我意。",
            "赠品很好，挺不错的。",
            "本来买了产品以后还有些担心。但是看到赠品以后就放心了。",
            "不论品质如何，至少说明店家对客的态度很好！",
            "我很喜欢这些商品！",
            "我对于商品的附加值很在乎，恰好这些赠品为这件商品提供了这样的附加值，这令我很满意。",
            "感觉现在的网购环境越来越好了，以前网购的时候还没有这么多贴心的赠品和增值服务。",
            "第一次用京东，被这种赠品和增值服务的良好态度感动到了。",
            "赠品还行。",
        ]
    else:
        # 使用 product_id 直接获取评论，避免搜索接口反爬
        spider = jdspider.JDSpider(product_id=product_id, categlory=pname)
        opts["logger"].debug("Successfully created a JDSpider instance")
        result = spider.getData(2, 3)  # 这里可以自己改

        # 如果爬取失败，返回 None 让调用方使用默认评价
        if result is None:
            opts["logger"].warning("评论爬取失败，将使用默认评价")
            return None

    opts["logger"].debug("Result: %s", result)

    # class 0是评价 1是提取id
    if _class == 1:
        try:
            keywords = jieba.analyse.textrank(pname, topK=5, allowPOS="n")
            if keywords:
                name = keywords[0]
                opts["logger"].debug("Name: %s", name)
            else:
                name = "宝贝"
        except Exception as e:
            opts["logger"].warning('jieba textrank analysis error: %s, fallback to "宝贝"', e)
            name = "宝贝"
        opts["logger"].debug("_class is 1. Directly return name")
        return name
    else:
        return 5, str(result)


# 查询全部评价
def all_evaluate(opts=None):
    opts = opts or {}
    N = {}
    url = "https://club.jd.com/myJdcomments/myJdcomment.action?"
    opts["logger"].info("URL: %s", url)
    opts["logger"].debug("Fetching website data")
    req = requests.get(url, headers=headers)
    opts["logger"].debug(
        "Successfully accepted the response with status code %d", req.status_code
    )
    if not req.ok:
        opts["logger"].debug(
            "Status code of the response is %d, not 200", req.status_code
        )
    req_et = etree.HTML(req.text)
    opts["logger"].debug("Successfully parsed an XML tree")
    evaluate_data = req_et.xpath('//*[@id="main"]/div[2]/div[1]/div/ul/li')
    # print(evaluate)
    loop_times = len(evaluate_data)
    opts["logger"].debug("Total loop times: %d", loop_times)
    for i, ev in enumerate(evaluate_data):
        opts["logger"].debug("Loop: %d / %d", i + 1, loop_times)
        na = ev.xpath("a/text()")[0]
        opts["logger"].debug("na: %s", na)
        try:
            num = ev.xpath("b/text()")[0]
            opts["logger"].debug("num: %s", num)
        except IndexError:
            opts["logger"].info("Can't find num content in XPath, fallback to 0")
            num = 0
        N[na] = int(num)
    return N


def delete_jpg():
    current_directory = os.getcwd()
    files = os.listdir(current_directory)
    for file in files:
        if file.lower().endswith(".jpg"):
            # 构建完整的文件路径
            file_path = os.path.join(current_directory, file)
            # 删除文件
            os.remove(file_path)


# 普通评价
def ordinary(N, opts=None):
    time.sleep(3)
    opts = opts or {}
    Order_data = []
    req_et = []
    imgCommentCount_bool = True
    loop_times = N["待评价订单"] // 20
    opts["logger"].debug("Fetching website data")
    opts["logger"].debug("Total loop times: %d", loop_times)
    for i in range(loop_times + 1):
        url = (
            f"https://club.jd.com/myJdcomments/myJdcomment.action?sort=0&"
            f"page={i + 1}"
        )
        opts["logger"].debug("URL: %s", url)
        req = requests.get(url, headers=headers)
        opts["logger"].debug(
            "Successfully accepted the response with status code %d", req.status_code
        )
        if not req.ok:
            opts["logger"].warning(
                "Status code of the response is %d, not 200", req.status_code
            )
        req_et.append(etree.HTML(req.text))
        opts["logger"].debug("Successfully parsed an XML tree")
    opts["logger"].debug("Fetching data from XML trees")
    opts["logger"].debug("Total loop times: %d", loop_times)
    for idx, i in enumerate(req_et):
        opts["logger"].debug("Loop: %d / %d", idx + 1, loop_times)
        opts["logger"].debug("Fetching order data in the default XPath")
        elems = i.xpath('//*[@id="main"]/div[2]/div[2]/table/tbody')
        opts["logger"].debug("Count of fetched order data: %d", len(elems))
        Order_data.extend(elems)
    if len(Order_data) != N["待评价订单"]:
        opts["logger"].debug(
            'Count of fetched order data doesn\'t equal N["待评价订单"]'
        )
        opts["logger"].debug("Clear the list Order_data")
        Order_data = []
        opts["logger"].debug("Total loop times: %d", loop_times)
        for idx, i in enumerate(req_et):
            opts["logger"].debug("Loop: %d / %d", idx + 1, loop_times)
            opts["logger"].debug("Fetching order data in another XPath")
            elems = i.xpath('//*[@id="main"]/div[2]/div[2]/table')
            opts["logger"].debug("Count of fetched order data: %d", len(elems))
            Order_data.extend(elems)

    opts["logger"].info(f"当前共有{N['待评价订单']}个评价。")
    opts["logger"].debug("Commenting on items")
    for i, Order in enumerate(Order_data):
        try:
            oid = Order.xpath('tr[@class="tr-th"]/td/span[3]/a/text()')[0]
            opts["logger"].debug("oid: %s", oid)
            oname_data = Order.xpath(
                'tr[@class="tr-bd"]/td[1]/div[1]/div[2]/div/a/text()'
            )
            opts["logger"].debug("oname_data: %s", oname_data)
            pid_data = Order.xpath('tr[@class="tr-bd"]/td[1]/div[1]/div[2]/div/a/@href')
            opts["logger"].debug("pid_data: %s", pid_data)
        except IndexError:
            opts["logger"].warning(f"第{i + 1}个订单未查找到商品，跳过。")
            continue
        loop_times1 = min(len(oname_data), len(pid_data))
        opts["logger"].debug("Commenting on orders")
        opts["logger"].debug("Total loop times: %d", loop_times1)
        idx = 0
        for oname, pid in zip(oname_data, pid_data):
            opts["logger"].debug("Loop: %d / %d", idx + 1, loop_times1)
            # 使用正则表达式提取纯数字的商品ID（处理带额外参数的情况如 ?bbtf=null）
            import re
            pid_match = re.search(r'/(\d+)(?:\.html|/|$)', pid)
            if pid_match:
                pid = pid_match.group(1)
            else:
                pid = pid.replace("//item.jd.com/", "").replace(".html", "").split("?")[0].split("&")[0]
            opts["logger"].debug("pid: %s", pid)
            if "javascript" in pid:
                opts["logger"].error(
                    "pid_data: %s,这个订单估计是京东外卖的，会导致此次评价失败，请把该 %s 商品手工评价后再运行程序。"
                    % (pid, oname),
                )
                continue
            opts["logger"].info(f"\t{i}.开始评价订单\t{oname}[{oid}]并晒图")
            url2 = "https://club.jd.com/myJdcomments/saveProductComment.action"
            opts["logger"].debug("URL: %s", url2)

            # 直接使用 product_id 获取评论，避免搜索接口反爬
            xing, Str = generation(oname, product_id=pid, opts=opts)

            # 处理获取失败的情况
            if xing is None or Str is None:
                opts["logger"].warning("评论生成失败，使用默认评价")
                Str = random.choice([
                    "商品包装得很好，没有破损，物流速度也很快，第二天就到了。实物质量很好，跟描述一致，用起来很顺手。",
                    "物流特别快，两天就收到货了，包装也很结实。商品本身质量没得说，很有质感。客服态度很友好。",
                    "收货比预期快，物流小哥服务也不错。打开包装后发现产品质量很好，没有任何瑕疵。",
                ])
                xing = 5

            opts["logger"].info(f"\t\t评价内容,星级{xing}：" + Str)
            # 获取图片
            if opts.get("comment_with_image", True):
                opts["logger"].info(f"\t\t开始获取图片")
                img_url = (
                    f"https://club.jd.com/discussion/getProductPageImageCommentList"
                    f".action?productId={pid}"
                )
                opts["logger"].debug("Fetching images using the default URL")
                opts["logger"].debug("URL: %s", img_url)
                img_headers = headers.copy()
                img_headers["Referer"] = f"https://item.jd.com/{pid}.html"
                img_resp = requests.get(img_url, headers=img_headers)
                opts["logger"].debug(
                    "Successfully accepted the response with status code %d",
                    img_resp.status_code,
                )
                if not img_resp.ok:
                    opts["logger"].warning(
                        "Status code of the response is %d, not 200", img_resp.status_code
                    )
                opts["logger"].info("imgdata_url:" + img_url)
                opts["logger"].debug("img_resp text: %s", img_resp.text[:500] if img_resp.text else "empty")
                try:
                    if img_resp.text.strip().startswith('('):
                        #京东接口返回的是JSONP格式，外层有括号
                        json_text = img_resp.text.strip()[1:-1]
                        imgdata = json.loads(json_text)
                    else:
                        imgdata = img_resp.json()
                except Exception as e:
                    opts["logger"].error("解析图片数据失败: %s, 响应状态码: %d, 响应内容: %s",
                                        e, img_resp.status_code, img_resp.text[:200] if img_resp.text else "empty")
                    # 没有图片就跳过晒图
                    opts["logger"].warning("获取图片失败，跳过晒图环节")
                    imgCommentCount_bool = False
                    continue
                opts["logger"].debug("Image data: %s", imgdata)
                if imgdata["imgComments"]["imgCommentCount"] == 0:
                    opts["logger"].warning("这单没有图片数据，所以直接默认五星好评！！")
                    imgCommentCount_bool = False
                elif imgdata["imgComments"]["imgCommentCount"] > 0:
                    img_list = imgdata["imgComments"]["imgList"]
                    selected_imgs = random.sample(img_list, 2)
                    imgurl1 = selected_imgs[0]["imageUrl"]
                    opts["logger"].info("imgurl1 url: %s", imgurl1)
                    imgurl2 = selected_imgs[1]["imageUrl"]
                    opts["logger"].info("imgurl2 url: %s", imgurl2)
                    session = requests.Session()
                    imgBasic = "//img20.360buyimg.com/shaidan/s645x515_"
                    imgName1 = generate_unique_filename()
                    opts["logger"].debug(f"Image :{imgName1}")

                    # 下载并处理图片
                    imgurl1t = ""
                    downloaded_file1 = download_image(imgurl1, imgName1, opts.get("logger"))
                    # 上传图片（带重试机制）
                    if downloaded_file1:
                        imgPart1 = upload_image(
                            imgName1, downloaded_file1, session, headers, opts.get("logger")
                        )
                        if imgPart1 and imgPart1.status_code == 200 and ".jpg" in imgPart1.text:
                            imgurl1t = f"{imgBasic}{imgPart1.text}"
                        else:
                            opts["logger"].warning("图片1上传失败，将跳过该图片")
                    else:
                        opts["logger"].warning("图片1下载失败，将跳过该图片")

                    imgName2 = generate_unique_filename()
                    opts["logger"].debug(f"Image :{imgName2}")
                    imgurl2t = ""
                    downloaded_file2 = download_image(imgurl2, imgName2, opts.get("logger"))
                    # 上传图片（带重试机制）
                    if downloaded_file2:
                        imgPart2 = upload_image(
                            imgName2, downloaded_file2, session, headers, opts.get("logger")
                        )
                        if imgPart2 and imgPart2.status_code == 200 and ".jpg" in imgPart2.text:
                            imgurl2t = f"{imgBasic}{imgPart2.text}"
                        else:
                            opts["logger"].warning("图片2上传失败，将跳过该图片")
                    else:
                        opts["logger"].warning("图片2下载失败，将跳过该图片")

                    # 组合图片URL（如果任一图片上传失败则只用成功的那个）
                    imgurl_parts = []
                    if imgurl1t:
                        imgurl_parts.append(imgurl1t)
                    if imgurl2t:
                        imgurl_parts.append(imgurl2t)
                    imgurl = ",".join(imgurl_parts) if imgurl_parts else ""
                    opts["logger"].debug("Image URL: %s", imgurl)
                    opts["logger"].info(f"\t\t图片url={imgurl}")

                    # 如果两张图片都上传失败，跳过晒图环节
                    if not imgurl:
                        opts["logger"].warning("图片全部上传失败，跳过晒图环节")
                        imgCommentCount_bool = False
            Str: str = urllib.parse.quote(Str, safe="/", encoding=None, errors=None)
            Comment_data = {
                "orderId": oid,
                "productId": pid,  # 商品id
                "score": str(xing),  # 商品几星
                "content": Str,  # 评价内容
                "saveStatus": "1",
                "anonymousFlag": "1",  # 是否匿名
            }
            if opts.get("comment_with_image", True) and imgCommentCount_bool:
                Comment_data["imgs"] = imgurl  # 图片url
            opts["logger"].debug("Data: %s", Comment_data)
            if not opts.get("dry_run"):
                opts["logger"].debug("Sending comment request")
                Comment_resp = requests.post(url2, headers=headers2, data=Comment_data)
                opts["logger"].info(
                    "发送请求后的状态码:{},text:{}".format(
                        Comment_resp.status_code, Comment_resp.text
                    )
                )
            else:
                opts["logger"].debug("Skipped sending comment request in dry run")
            if Comment_resp.status_code == 200 and Comment_resp.json()["success"]:
                # 当发送后的状态码 200，并且返回值里的 success 是 true 才是晒图成功，此外所有状态均为晒图失败
                opts["logger"].info(f"\t{i}.评价订单\t{oname}[{oid}]评论成功")
            else:
                opts["logger"].warning(f"\t{i}.评价订单\t{oname}[{oid}]评论失败")
            opts["logger"].debug("Sleep time (s): %.1f", ORDINARY_SLEEP_SEC)
            time.sleep(ORDINARY_SLEEP_SEC)
            idx += 1
    N["待评价订单"] -= 1
    # 删除当前目录下的所有 jpg 图片
    # delete_jpg()
    return N


"""
# 晒单评价
def sunbw(N, opts=None):
    opts = opts or {}
    Order_data = []
    loop_times = N['待晒单'] // 20
    opts['logger'].debug('Fetching website data')
    opts['logger'].debug('Total loop times: %d', loop_times)
    for i in range(loop_times + 1):
        opts['logger'].debug('Loop: %d / %d', i + 1, loop_times)
        url = (f'https://club.jd.com/myJdcomments/myJdcomment.action?sort=1'
               f'&page={i + 1}')
        opts['logger'].debug('URL: %s', url)
        req = requests.get(url, headers=headers)
        opts['logger'].debug(
            'Successfully accepted the response with status code %d',
            req.status_code)
        if not req.ok:
            opts['logger'].warning(
                'Status code of the response is %d, not 200', req.status_code)
        req_et = etree.HTML(req.text)
        opts['logger'].debug('Successfully parsed an XML tree')
        opts['logger'].debug('Fetching data from XML trees')
        elems = req_et.xpath(
            '//*[@id="evalu01"]/div[2]/div[1]/div[@class="comt-plist"]/div[1]')
        opts['logger'].debug('Count of fetched order data: %d', len(elems))
        Order_data.extend(elems)
    opts['logger'].info(f"当前共有{N['待晒单']}个需要晒单。")
    opts['logger'].debug('Commenting on items')
    for i, Order in enumerate(Order_data):
        oname = Order.xpath('ul/li[1]/div/div[2]/div[1]/a/text()')[0]
        pid = Order.xpath('@pid')[0]
        oid = Order.xpath('@oid')[0]
        opts['logger'].info(f'\t开始第{i+1}，{oname}')
        opts['logger'].debug('pid: %s', pid)
        opts['logger'].debug('oid: %s', oid)
        # 获取图片
        url1 = (f'https://club.jd.com/discussion/getProductPageImageCommentList'
                f'.action?productId={pid}')
        opts['logger'].debug('Fetching images using the default URL')
        opts['logger'].debug('URL: %s', url1)
        req1 = requests.get(url1, headers=headers)
        opts['logger'].debug(
            'Successfully accepted the response with status code %d',
            req1.status_code)
        if not req.ok:
            opts['logger'].warning(
                'Status code of the response is %d, not 200', req1.status_code)
        imgdata = req1.json()
        opts['logger'].debug('Image data: %s', imgdata)
        if imgdata["imgComments"]["imgCommentCount"] == 0:
            opts['logger'].debug('Count of fetched image comments is 0')
            opts['logger'].debug('Fetching images using another URL')
            url1 = ('https://club.jd.com/discussion/getProductPageImage'
                    'CommentList.action?productId=1190881')
            opts['logger'].debug('URL: %s', url1)
            req1 = requests.get(url1, headers=headers)
            opts['logger'].debug(
                'Successfully accepted the response with status code %d',
                req1.status_code)
            if not req.ok:
                opts['logger'].warning(
                    'Status code of the response is %d, not 200',
                    req1.status_code)
            imgdata = req1.json()
            opts['logger'].debug('Image data: %s', imgdata)
        imgurl = imgdata["imgComments"]["imgList"][0]["imageUrl"]
        opts['logger'].debug('Image URL: %s', imgurl)

        opts['logger'].info(f'\t\t图片url={imgurl}')
        # 提交晒单
        opts['logger'].debug('Preparing for commenting')
        url2 = "https://club.jd.com/myJdcomments/saveShowOrder.action"
        opts['logger'].debug('URL: %s', url2)
        headers['Referer'] = ('https://club.jd.com/myJdcomments/myJdcomment.'
                              'action?sort=1')
        headers['Origin'] = 'https://club.jd.com'
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
        opts['logger'].debug('New header for this request: %s', headers)
        data = {
            'orderId': oid,
            'productId': pid,
            'imgs': imgurl,
            'saveStatus': 3
        }
        opts['logger'].debug('Data: %s', data)
        if not opts.get('dry_run'):
            opts['logger'].debug('Sending comment request')
            req_url2 = requests.post(url2, data=data, headers=headers)
        else:
            opts['logger'].debug('Skipped sending comment request in dry run')
        opts['logger'].info('完成')
        opts['logger'].debug('Sleep time (s): %.1f', SUNBW_SLEEP_SEC)
        time.sleep(SUNBW_SLEEP_SEC)
        N['待晒单'] -= 1
    return N
"""

# 追评


def review(N, opts=None):
    opts = opts or {}
    req_et = []
    Order_data = []
    loop_times = N["待追评"] // 20
    opts["logger"].debug("Fetching website data")
    opts["logger"].debug("Total loop times: %d", loop_times)
    for i in range(loop_times + 1):
        opts["logger"].debug("Loop: %d / %d", i + 1, loop_times)
        url = (
            f"https://club.jd.com/myJdcomments/myJdcomment.action?sort=3"
            f"&page={i + 1}"
        )
        opts["logger"].debug("URL: %s", url)
        req = requests.get(url, headers=headers)
        opts["logger"].debug(
            "Successfully accepted the response with status code %d", req.status_code
        )
        if not req.ok:
            opts["logger"].warning(
                "Status code of the response is %d, not 200", req.status_code
            )
        req_et.append(etree.HTML(req.text))
        opts["logger"].debug("Successfully parsed an XML tree")
    opts["logger"].debug("Fetching data from XML trees")
    opts["logger"].debug("Total loop times: %d", loop_times)
    for idx, i in enumerate(req_et):
        opts["logger"].debug("Loop: %d / %d", idx + 1, loop_times)
        opts["logger"].debug("Fetching order data in the default XPath")
        elems = i.xpath('//*[@id="main"]/div[2]/div[2]/table/tr[@class="tr-bd"]')
        opts["logger"].debug("Count of fetched order data: %d", len(elems))
        Order_data.extend(elems)
    if len(Order_data) != N["待追评"]:
        opts["logger"].debug('Count of fetched order data doesn\'t equal N["待追评"]')
        # NOTE: Need them?
        # opts['logger'].debug('Clear the list Order_data')
        # Order_data = []
        opts["logger"].debug("Total loop times: %d", loop_times)
        for idx, i in enumerate(req_et):
            opts["logger"].debug("Loop: %d / %d", idx + 1, loop_times)
            opts["logger"].debug("Fetching order data in another XPath")
            elems = i.xpath(
                '//*[@id="main"]/div[2]/div[2]/table/tbody/tr[@class="tr-bd"]'
            )
            opts["logger"].debug("Count of fetched order data: %d", len(elems))
            Order_data.extend(elems)
    opts["logger"].info(f"当前共有 {N['待追评']} 个需要追评。")
    opts["logger"].debug("Commenting on items")
    for i, Order in enumerate(Order_data):
        oname = Order.xpath("td[1]/div/div[2]/div/a/text()")[0]
        _id = Order.xpath("td[3]/div/a/@href")[0]
        opts["logger"].info(f"\t开始追评第{i+1}，{oname}")
        opts["logger"].debug("_id: %s", _id)
        url1 = (
            "https://club.jd.com/afterComments/" "saveAfterCommentAndShowOrder.action"
        )
        opts["logger"].debug("URL: %s", url1)
        pid, oid = _id.replace(
            "http://club.jd.com/afterComments/productPublish.action?sku=", ""
        ).split("&orderId=")
        opts["logger"].debug("pid: %s", pid)
        if "javascript" in pid:
            opts["logger"].error(
                "pid_data: %s,这个订单估计是京东外卖的，会导致此次评价失败，请把该 %s 商品手工评价后再运行程序。"
                % (pid, oname),
            )
            exit(0)
        opts["logger"].debug("oid: %s", oid)
        _, context = generation(oname, product_id=pid, _type=0, opts=opts)
        if context is None:
            context = "商品质量很好，使用效果不错，满意的一次购物体验！"
        opts["logger"].info(f"\t\t追评内容：{context}")
        context = urllib.parse.quote(context, safe="/", encoding=None, errors=None)
        data1 = {
            "orderId": oid,
            "productId": pid,
            "content": context,
            "anonymousFlag": 1,
            "score": 5,
            "imgs": "",
        }
        opts["logger"].debug("Data: %s", data1)
        if not opts.get("dry_run"):
            opts["logger"].debug("Sending comment request")
            pj1 = requests.post(url1, headers=headers2, data=data1)
            opts["logger"].debug(
                "发送请求后的状态码:{},text:{}".format(pj1.status_code, pj1.text)
            )
        else:
            opts["logger"].debug("Skipped sending comment request in dry run")
        opts["logger"].info("完成")
        opts["logger"].debug("Sleep time (s): %.1f", REVIEW_SLEEP_SEC)
        time.sleep(REVIEW_SLEEP_SEC)
        N["待追评"] -= 1
    return N


# 服务评价
def Service_rating(N, opts=None):
    opts = opts or {}
    Order_data = []
    req_et = []
    loop_times = N["服务评价"] // 20
    opts["logger"].debug("Fetching website data")
    opts["logger"].debug("Total loop times: %d", loop_times)
    for i in range(loop_times + 1):
        opts["logger"].debug("Loop: %d / %d", i + 1, loop_times)
        url = (
            f"https://club.jd.com/myJdcomments/myJdcomment.action?sort=4"
            f"&page={i + 1}"
        )
        opts["logger"].debug("URL: %s", url)
        req = requests.get(url, headers=headers)
        opts["logger"].debug(
            "Successfully accepted the response with status code %d", req.status_code
        )
        if not req.ok:
            opts["logger"].warning(
                "Status code of the response is %d, not 200", req.status_code
            )
        req_et.append(etree.HTML(req.text))
        opts["logger"].debug("Successfully parsed an XML tree")
    opts["logger"].debug("Fetching data from XML trees")
    opts["logger"].debug("Total loop times: %d", loop_times)
    for idx, i in enumerate(req_et):
        opts["logger"].debug("Loop: %d / %d", idx + 1, loop_times)
        opts["logger"].debug("Fetching order data in the default XPath")
        elems = i.xpath('//*[@id="main"]/div[2]/div[2]/table/tbody/tr[@class="tr-bd"]')
        opts["logger"].debug("Count of fetched order data: %d", len(elems))
        Order_data.extend(elems)
    if len(Order_data) != N["服务评价"]:
        opts["logger"].debug('Count of fetched order data doesn\'t equal N["服务评价"]')
        opts["logger"].debug("Clear the list Order_data")
        Order_data = []
        opts["logger"].debug("Total loop times: %d", loop_times)
        for idx, i in enumerate(req_et):
            opts["logger"].debug("Loop: %d / %d", idx + 1, loop_times)
            opts["logger"].debug("Fetching order data in another XPath")
            elems = i.xpath('//*[@id="main"]/div[2]/div[2]/table/tr[@class="tr-bd"]')
            opts["logger"].debug("Count of fetched order data: %d", len(elems))
            Order_data.extend(elems)
    opts["logger"].info(f"当前共有{N['服务评价']}个需要第一次服务评价。")
    opts["logger"].debug("Commenting on items")
    for i, Order in enumerate(Order_data):
        oname = Order.xpath("td[1]/div[1]/div[2]/div/a/text()")[0]
        try:
            oid = Order.xpath("td[4]/div/a[1]/@oid")[0]
        except IndexError:
            opts["logger"].warning("Failed to fetch oid")
            continue
        opts["logger"].info(f"\t开始第一次评论，{i+1}，{oname}")
        opts["logger"].debug("oid: %s", oid)
        url1 = (
            f"https://club.jd.com/myJdcomments/insertRestSurvey.action"
            f"?voteid=145&ruleid={oid}"
        )
        opts["logger"].debug("URL: %s", url1)
        data1 = {
            "oid": oid,
            "gid": "32",
            "sid": "186194",
            "stid": "0",
            "tags": "",
            "ro591": f"591A{random.randint(4, 5)}",  # 商品符合度
            "ro592": f"592A{random.randint(4, 5)}",  # 店家服务态度
            "ro593": f"593A{random.randint(4, 5)}",  # 快递配送速度
            "ro899": f"899A{random.randint(4, 5)}",  # 快递员服务
            "ro900": f"900A{random.randint(4, 5)}",  # 快递员服务
        }
        opts["logger"].debug("Data: %s", data1)
        if not opts.get("dry_run"):
            opts["logger"].debug("Sending comment request")
            pj1 = requests.post(url1, headers=headers, data=data1)
        else:
            opts["logger"].debug("Skipped sending comment request in dry run")
        opts["logger"].info("\t\t " + pj1.text)
        opts["logger"].debug("Sleep time (s): %.1f", SERVICE_RATING_SLEEP_SEC)
        time.sleep(SERVICE_RATING_SLEEP_SEC)
        N["服务评价"] -= 1
    return N


def No(opts=None):
    opts = opts or {}
    # opts["logger"].info("")
    N = all_evaluate(opts)
    s = "----".join(["{} {}".format(i, N[i]) for i in N])
    opts["logger"].info(s)
    # opts["logger"].info("")
    return N


def main(opts=None):
    opts = opts or {}
    opts["logger"].info("开始京东批量评价！")
    N = No(opts)
    opts["logger"].debug("N value after executing No(): %s", N)
    if not N:
        opts["logger"].error("Ck出现错误，请重新抓取！")
        exit()
    opts["logger"].info(f"已评价：{N['已评价']}个")
    if N["待评价订单"] != 0:
        opts["logger"].info("1.开始普通评价")
        N = ordinary(N, opts)
        opts["logger"].debug("N value after executing ordinary(): %s", N)
        N = No(opts)
        opts["logger"].debug("N value after executing No(): %s", N)
    """ "待晒单" is no longer found in N{} instead of "已评价"
    if N['待晒单'] != 0:
        opts['logger'].info("2.开始晒单评价")
        N = sunbw(N, opts)
        opts['logger'].debug('N value after executing sunbw(): %s', N)
        N = No(opts)
        opts['logger'].debug('N value after executing No(): %s', N)
    """
    if N["待追评"] != 0:
        opts["logger"].info("3.开始批量追评,注意：追评不会自动上传图片")
        N = review(N, opts)
        opts["logger"].debug("N value after executing review(): %s", N)
        N = No(opts)
        opts["logger"].debug("N value after executing No(): %s", N)
    if N["服务评价"] != 0:
        opts["logger"].info("4.开始服务评价")
        N = Service_rating(N, opts)
        opts["logger"].debug("N value after executing Service_rating(): %s", N)
        N = No(opts)
        opts["logger"].debug("N value after executing No(): %s", N)
    opts["logger"].info("全部完成啦！")
    for i in N:
        if N[i] != 0:
            opts["logger"].warning("出现了二次错误，跳过了部分，重新尝试")
            main(opts)


if __name__ == "__main__":
    # parse arguments
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        help="have a full run without comment submission",
        action="store_true",
    )
    parser.add_argument(
        "-lv",
        "--log-level",
        help="specify logging level (default: info)",
        default="INFO",
    )
    parser.add_argument(
        "-o", "--log-file", help="specify logging file", default="log.txt"
    )
    args = parser.parse_args()
    if args.log_level.upper() not in [
        "DEBUG",
        "WARN",
        "INFO",
        "ERROR",
        "FATAL",
        # NOTE: `WARN` is an alias of `WARNING`. `FATAL` is an alias of
        # `CRITICAL`. Using these aliases is for developers' and users'
        # convenience.
        # NOTE: Now there is no logging on `CRITICAL` level.
    ]:
        args.log_level = "INFO"
    else:
        args.log_level = args.log_level.upper()
    opts = {"dry_run": args.dry_run, "log_level": args.log_level}
    if hasattr(args, "log_file"):
        opts["log_file"] = args.log_file
    else:
        opts["log_file"] = None

    # logging on console
    _logging_level = getattr(logging, opts["log_level"])
    logger = logging.getLogger("comment")
    logger.setLevel(level=_logging_level)
    # NOTE: `%(levelname)s` will be parsed as the original name (`FATAL` ->
    # `CRITICAL`, `WARN` -> `WARNING`).
    # NOTE: The alignment number should set to 19 considering the style
    # controling characters. When it comes to file logger, the number should
    # set to 8.
    formatter = StyleFormatter("%(asctime)s %(levelname)-19s %(message)s")
    rawformatter = StyleFormatter(
        "%(asctime)s %(levelname)-8s %(message)s", use_style=False
    )
    console = logging.StreamHandler()
    console.setLevel(_logging_level)
    console.setFormatter(formatter)
    logger.addHandler(console)
    opts["logger"] = logger
    # It's a hack!!!
    jieba.default_logger = logging.getLogger("jieba")
    jieba.default_logger.setLevel(level=_logging_level)
    jieba.default_logger.addHandler(console)
    # It's another hack!!!
    jdspider.default_logger = logging.getLogger("spider")
    jdspider.default_logger.setLevel(level=_logging_level)
    jdspider.default_logger.addHandler(console)

    logger.debug("Successfully set up console logger")
    logger.debug("CLI arguments: %s", args)
    logger.debug("Opening the log file")
    if opts["log_file"]:
        try:
            handler = logging.FileHandler(opts["log_file"], "w")
        except Exception as e:
            logger.error("Failed to open the file handler")
            logger.error("Error message: %s", e)
            sys.exit(1)
        handler.setLevel(_logging_level)
        handler.setFormatter(rawformatter)
        logger.addHandler(handler)
        jieba.default_logger.addHandler(handler)
        jdspider.default_logger.addHandler(handler)
        logger.debug("Successfully set up file logger")
    logger.debug("Options passed to functions: %s", opts)
    logger.debug("Builtin constants:")
    logger.debug("  CONFIG_PATH: %s", CONFIG_PATH)
    logger.debug("  USER_CONFIG_PATH: %s", USER_CONFIG_PATH)
    logger.debug("  ORDINARY_SLEEP_SEC: %s", ORDINARY_SLEEP_SEC)
    logger.debug("  SUNBW_SLEEP_SEC: %s", SUNBW_SLEEP_SEC)
    logger.debug("  REVIEW_SLEEP_SEC: %s", REVIEW_SLEEP_SEC)
    logger.debug("  SERVICE_RATING_SLEEP_SEC: %s", SERVICE_RATING_SLEEP_SEC)

    # parse configurations
    logger.debug("Reading the configuration file")
    if os.path.exists(USER_CONFIG_PATH):
        logger.debug("User configuration file exists")
        _cfg_path = USER_CONFIG_PATH
    else:
        logger.debug(
            "User configuration file doesn't exist, fallback to the default one"
        )
        _cfg_path = CONFIG_PATH
    with open(_cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    logger.debug("Closed the configuration file")
    logger.debug("Configurations in Python-dict format: %s", cfg)
    ck = cfg["user"]["cookie"]
    jdspider.cookie = ck.encode("utf-8")
    comment_with_image = cfg["user"].get("comment_with_image", True)
    opts["comment_with_image"] = comment_with_image
    logger.info(f"本次评论是否带图片：{opts['comment_with_image']}")

    headers2 = {
        "Cookie": ck.encode("utf-8"),
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/114.0.5735.110 Safari/537.36",
        "Connection": "keep-alive",
        "Cache-Control": "max-age=0",
        "X-Requested-With": "XMLHttpRequest",
        "sec-ch-ua": "",
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": "",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-User": "?1",
        "Sec-Fetch-Dest": "empty",
        "Referer": "https://club.jd.com/",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "zh-CN,zh;q=0.9",
        # 'Content-Type':'application/x-www-form-urlencoded'
    }
    headers = {
        "Cookie": ck.encode("utf-8"),
        "User-Agent": '''Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0 Sec-Ch-Ua: "Chromium";v="136", "Microsoft Edge";v="136", "Not.A/Brand";v="99"''',
        "DNT": "1",
        # "Connection": "keep-alive",
        # "Cache-Control": "max-age=0",
        # "sec-ch-ua": '" Not A;Brand";v="99", "Chromium";v="98", "Google Chrome";v="98"',
        # "sec-ch-ua-mobile": "?0",
        # "sec-ch-ua-platform": '"Windows"',
        # "Upgrade-Insecure-Requests": "1",
        # "Accept": "*/*",
        # "Sec-Fetch-Site": "same-site",
        # "Sec-Fetch-Mode": "navigate",
        # "origin": "https://club.jd.com",
        # "Sec-Fetch-User": "?1",
        # "Sec-Fetch-Dest": "document",
        # "Referer": "https://order.jd.com/",
        # "Accept-Encoding": "gzip, deflate, br, zstd",
        # "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
    }
    logger.debug("Builtin HTTP request header: %s", headers)

    logger.debug("Starting main processes")
    try:
        main(opts)
    # NOTE: It needs 3,000 times to raise this exception. Do you really want to
    # do like this?
    except RecursionError:
        logger.error("多次出现未完成情况，程序自动退出")
