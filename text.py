import requests

headers = {
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
}

url = "https://search.jd.com/Search?keyword=%E5%A6%99%E6%B4%81%E7%AB%99%E7%AB%8B%E5%AF%86%E5%B0%81%E8%A2%8B%E5%A4%A7%E4%B8%AD%E5%B0%8F%E5%8F%B7%E5%85%B145%E5%8F%AA%E5%AF%86%E5%AE%9E%E5%B0%81%E5%8F%A3%E8%A2%8B%E9%A3%9F%E5%93%81%E7%BA%A7%E4%BF%9D%E9%B2%9C%E8%87%AA%E5%B0%81%E5%8F%A3%E5%A1%91%E6%8B%89%E9%93%BE%E5%88%86%E8%A3%85&enc=utf-8"
resp = requests.get(url, headers=headers)
print(resp.status_code)
print(resp.text)