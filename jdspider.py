# @Time : 2022/2/8 20:50
# @Author :@Zhang Jiale and @Dimlitter
# @File : jdspider.py

import json
import logging
import random
import re
import sys
import time
from contextlib import nullcontext
from urllib.parse import quote, urlencode
import openai

import requests
import yaml
import zhon.hanzi
from lxml import etree

# 加载配置文件
with open("./config.user.yml", "r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

# 获取用户的 cookie
cookie = cfg["user"]["cookie"]
apikey = cfg["user"]["api_key"]

# 配置日志输出到标准错误流
log_console = logging.StreamHandler(sys.stderr)
default_logger = logging.getLogger("jdspider")
default_logger.setLevel(logging.DEBUG)
default_logger.addHandler(log_console)

# 定义基础请求头，避免重复代码
BASE_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,"
    "*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
    "accept-encoding": "gzip, deflate, br",
    "accept-language": "zh-CN,zh;q=0.9",
    "cache-control": "max-age=0",
    "dnt": "1",
    "sec-ch-ua": '" Not A;Brand";v="99", "Chromium";v="98", "Google Chrome";v="98"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/98.0.4758.82 Safari/537.36",
}


class JDSpider:
    """
    京东爬虫类，用于爬取指定商品类别的评论信息。
    传入商品类别（如手机、电脑）构造实例，然后调用 getData 方法爬取数据。
    """

    def __init__(self, categlory):
        # 京东搜索商品的起始页面 URL
        self.startUrl = "https://search.jd.com/Search?keyword=%s&enc=utf-8" % (
            quote(categlory)
        )
        # 评论接口的基础 URL
        self.commentBaseUrl = "https://club.jd.com"
        # 基础请求头
        self.headers = BASE_HEADERS.copy()
        # 带 cookie 的请求头
        self.headers2 = {
            **BASE_HEADERS,
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "accept-language": "en,zh-CN;q=0.9,zh;q=0.8",
            "Cookie": cookie,
            "priority": "u=0, i",
            "sec-ch-ua": '"Microsoft Edge";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "sec-fetch-mode": "navigate",
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
        }
        # 获取商品 ID 列表
        self.productsId = self.getId()
        # 评论类型映射，1 差评，2 中评，3 好评
        self.comtype = {1: "negative", 2: "medium", 3: "positive"}  # 修正拼写错误
        # 商品类别
        self.categlory = categlory
        # IP 列表，用于代理（当前为空）
        self.iplist = {"http": [], "https": []}

    def getParamUrl(self, productid: str, page: str, score: str):
        """
        生成评论接口的请求参数和完整 URL。
        :param productid: 商品 ID
        :param page: 评论页码
        :param score: 评论类型（1 差评，2 中评，3 好评）
        :return: 请求参数和完整 URL
        """
        path = (
            "/discussion/getProductPageImageCommentList.action?productId=" + productid
        )
        params = {}
        # params = {
        #     "appid": "item-v3",
        #     "functionId": "pc_club_productPageComments",
        #     "client": "pc",
        #     "body": {
        #         "productId": productid,
        #         "score": score,
        #         "sortType": "5",
        #         "page": page,
        #         "pageSize": "10",
        #         "isShadowSku": "0",
        #         "rid": "0",
        #         "fold": "1",
        #     },
        # }
        # default_logger.info("请求参数: " + str(params))
        url = self.commentBaseUrl + path
        default_logger.info("请求 URL: " + str(url))
        return params, url

    def getHeaders(self, productid: str) -> dict:
        """
        生成爬取指定商品评论时所需的请求头。
        :param productid: 商品 ID
        :return: 请求头字典
        """
        return {
            "Referer": f"https://item.jd.com/{productid}.html",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/75.0.3770.142 Safari/537.36",
            # "cookie": cookie,
        }

    def getId(self) -> list:
        """
        从京东搜索页面获取商品 ID 列表。
        :return: 商品 ID 列表
        """
        try:
            response = requests.get(self.startUrl, headers=self.headers2)
            response.raise_for_status()  # 检查响应状态码
            default_logger.info("获取同类产品的搜索 URL 结果：" + self.startUrl)
        except requests.RequestException as e:
            default_logger.warning(f"请求异常，状态码错误，爬虫连接异常！错误信息: {e}")
            return []

        html = etree.HTML(response.text)
        return html.xpath('//li[@class="gl-item"]/@data-sku')

    def getData(self, maxPage: int, score: int):
        """
        爬取指定商品类别的评论信息。
        :param maxPage: 最大爬取页数，每页 10 条评论
        :param score: 评论类型（1 差评，2 中评，3 好评）
        :return: 处理后的评论列表
        """
        comments = []
        scores = []
        default_logger.info(
            "爬取商品数量最多为 8 个，请耐心等待，也可以自行修改 jdspider 文件"
        )

        # 确定要爬取的商品数量
        product_count = min(len(self.productsId), 3) if self.productsId else 0
        if product_count == 0:
            default_logger.warning("self.productsId 为空，将使用默认评价")
        default_logger.info("要爬取的商品数量: " + str(product_count))

        for j in range(product_count):
            product_id = self.productsId[j]
            for i in range(1, maxPage):
                params, url = self.getParamUrl(product_id, str(i), str(score))
                default_logger.info(f"正在爬取第 {j + 1} 个商品的第 {i} 页评论信息")

                try:
                    default_logger.info(
                        f"爬取商品评价的 URL 链接是 {url}，商品的 ID 是：{product_id}"
                    )
                    response = requests.get(url, headers=self.getHeaders(product_id))
                    response.raise_for_status()  # 检查响应状态码
                except requests.RequestException as e:
                    default_logger.warning(f"请求异常: {e}")
                    continue

                time.sleep(random.randint(5, 10))  # 设置时延，防止被封 IP

                if not response.text:
                    default_logger.warning("未爬取到信息")
                    continue

                try:
                    res_json = json.loads(response.text)
                except json.JSONDecodeError as e:
                    default_logger.warning(f"JSON 解析异常: {e}")
                    continue

                if res_json["imgComments"]["imgCommentCount"] == 0:
                    default_logger.warning(
                        f"爬取到的商品评价数量为 0，可能是最后一页或请求失败"
                    )
                    break

                for comment_data in res_json["imgComments"]["imgList"]:
                    comment = (
                        comment_data["commentVo"]["content"]
                        .replace("\n", " ")
                        .replace("\r", " ")
                    )
                    comments.append(comment)
                    scores.append(comment_data["commentVo"]["score"])

        default_logger.info(f"已爬取 {len(comments)} 条 {self.comtype[score]} 评价信息")

        # 处理评论，拆分成句子
        remarks = []
        for comment in comments:
            sentences = re.findall(zhon.hanzi.sentence, comment)
            if not sentences or sentences in [
                ["。"],
                ["？"],
                ["！"],
                ["."],
                [","],
                ["?"],
                ["!"],
            ]:
                default_logger.warning(
                    f"拆分失败或结果不符(去除空格和标点符号)：{sentences}"
                )
            else:
                remarks.append(sentences)

        sentences = self.solvedata(remarks=remarks)
        result = self.generate_single_review(sentences=sentences)
        default_logger.info("生成的评价result为：" + str(result))

        return result

    def solvedata(self, remarks) -> list:
        """
        将评论拆分成句子列表。
        :param remarks: 包含评论句子列表的列表
        :return: 所有评论句子组成的列表
        """
        sentences = []
        for item in remarks:
            for sentence in item:
                sentences.append(sentence)
        default_logger.info("爬取的评价结果：" + str(sentences))
        return sentences

    def generate_single_review(self, sentences: list[str]) -> str:
        """
        使用 DeepSeek 模型生成一条自然、有真实感、口语化的总结评论，控制在 80 字以内
        """
        client = openai.OpenAI(
            api_key=apikey,
            base_url="https://api.deepseek.com"
        )

        prompt_text = "。".join(sentences[:15])  # 取前 15 条，控制上下文长度
        prompt = f"""
    以下是一些用户关于商品的评价句子，请你总结这些内容，生成一条自然、有真实感、口语化的评论，控制在 60至80 字左右。只输出一句话评论，不要带任何解释：

    评论内容：
    {prompt_text}

    请输出一条总结性评价：
    """

        try:
            # default_logger.info("prompt为：" + str(prompt))
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                temperature=1.0,
                max_tokens=100,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            default_logger.warning(f"调用 DeepSeek 接口失败: {e}")
            default_logger.warning("当前商品没有评价，使用默认评价")
            return self.random_comment()

    def random_comment(self) -> str:
        default_reviews = [
            "商品包装得很好，没有破损，物流速度也很快，第二天就到了。实物质量很好，跟描述一致，用起来很顺手。客服服务也很周到，耐心解答了我的问题，购物体验很满意。",
            "物流特别快，两天就收到货了，包装也很结实。商品本身质量没得说，很有质感。客服态度很友好，细心帮我确认了信息，整个过程都很顺利，非常省心的一次购物。",
            "收货比预期快，物流小哥服务也不错。打开包装后发现产品质量很好，没有任何瑕疵。客服反应很快，态度特别好，主动提醒我注意事项，感觉非常贴心和专业。",
            "物流送达很及时，收到的时候包装完好无损。商品用起来感觉不错，做工也挺精细的。客服沟通很顺畅，态度友善又专业，整个购买过程让我非常安心和愉快。",
            "下单后很快就收到货了，物流速度真的很给力。商品质量出乎意料地好，手感和做工都很满意。客服态度也很好，有问必答，回复很及时，非常贴心，整体购物体验非常棒！"
        ]
        return random.choice(default_reviews)

# 测试用例
if __name__ == "__main__":
    jdlist = ["得宝纸巾"]
    for item in jdlist:
        spider = JDSpider(item)
        spider.getData(2, 3)
