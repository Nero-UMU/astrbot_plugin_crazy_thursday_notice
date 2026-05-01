import random
import string
from dataclasses import dataclass, field

import httpx

_API_URL = "https://m.4008823823.com.cn/delivery-portal/api/v2/init/combine/delivery"
_IMAGE_BASE = "https://www.kfc.com.cn"
_CLIENT_VERSION = "v6.306(4f1aca49)"
_FVERSION = "251029"

# 城市名 → (lat, lng)
CITY_COORDS: dict[str, tuple[float, float]] = {
    "上海市": (31.2304, 121.4737),
    "北京市": (39.9042, 116.4074),
    "广州市": (23.1291, 113.2644),
    "深圳市": (22.5431, 114.0579),
    "杭州市": (30.2741, 120.1551),
    "成都市": (30.5728, 104.0668),
    "武汉市": (30.5928, 114.3055),
    "南京市": (32.0603, 118.7969),
    "西安市": (34.3416, 108.9398),
    "重庆市": (29.5630, 106.5516),
    "天津市": (39.3434, 117.3616),
    "苏州市": (31.2990, 120.5853),
    "郑州市": (34.7466, 113.6254),
    "长沙市": (28.2282, 112.9388),
    "青岛市": (36.0671, 120.3826),
    "厦门市": (24.4798, 118.0894),
    "南昌市": (28.6820, 115.8579),
    "合肥市": (31.8206, 117.2272),
    "哈尔滨市": (45.8038, 126.5349),
    "沈阳市": (41.8057, 123.4315),
}


@dataclass
class MenuItem:
    name: str
    price: float  # 元
    orig_price: float  # 原价（元），0 表示无折扣
    category: str
    description: str = ""
    image_url: str = ""
    available: bool = True
    popular: bool = False


@dataclass
class MenuCategory:
    name: str
    items: list[MenuItem] = field(default_factory=list)


class KFCMenuFetcher:
    """KFC 外送菜单爬虫。

    通过坐标定位最近门店，拉取当前可点菜单。
    用法：
        async with KFCMenuFetcher(lat=39.9042, lng=116.4074) as fetcher:
            text = await fetcher.get_menu_text()
    """

    def __init__(
        self,
        city: str = "",
        lat: float = 31.2304,
        lng: float = 121.4737,
        timeout: float = 15.0,
    ):
        if city and city in CITY_COORDS:
            lat, lng = CITY_COORDS[city]
        elif city:
            raise ValueError(
                f"不支持的城市名 '{city}'，请改用经纬度，或从以下城市选择：{', '.join(CITY_COORDS)}"
            )
        self.lat = lat
        self.lng = lng
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                    "Mobile/15E148 Safari/604.1"
                ),
                "Content-Type": "application/json",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Origin": "https://m.4008823823.com.cn",
                "Referer": "https://m.4008823823.com.cn/kfctaro/menu/menu/pages/menu/index",
            },
            timeout=timeout,
            follow_redirects=True,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self._client.aclose()

    # ── 公开接口 ─────────────────────────────────────────────────

    async def get_menu(self, include_sold_out: bool = False) -> list[MenuCategory]:
        """获取菜单，返回按分类整理的列表。

        Args:
            include_sold_out: 是否包含已售罄的商品。
        """
        raw_categories = await self._fetch_menu_data()
        result: list[MenuCategory] = []
        for raw_cat in raw_categories:
            cat_name = raw_cat.get("topName") or raw_cat.get("nameCn", "未知分类")
            category = MenuCategory(name=cat_name)
            self._parse_items_into(category, raw_cat.get("menuList", []))
            if include_sold_out:
                self._parse_items_into(
                    category, raw_cat.get("disabledMenuList", []), available=False
                )
            # 部分分类下还有子分类
            for sub_cat in raw_cat.get("childClassList", []):
                self._parse_items_into(category, sub_cat.get("menuList", []))
                if include_sold_out:
                    self._parse_items_into(
                        category, sub_cat.get("disabledMenuList", []), available=False
                    )
            if category.items:
                result.append(category)
        return result

    async def get_menu_text(self, include_sold_out: bool = False) -> str:
        """返回适合发送到群组的纯文本菜单摘要。"""
        categories = await self.get_menu(include_sold_out=include_sold_out)
        if not categories:
            return "暂无菜单数据"
        lines: list[str] = []
        for cat in categories:
            lines.append(f"\n【{cat.name}】")
            for item in cat.items:
                price_str = f"¥{item.price:.0f}"
                if item.orig_price:
                    price_str += f"（原¥{item.orig_price:.0f}）"
                status = "" if item.available else "【售罄】"
                hot = "🔥" if item.popular else "  "
                lines.append(f"{hot}{item.name}  {price_str}{status}")
        return "\n".join(lines).strip()

    async def get_raw_response(self) -> dict:
        """返回接口原始响应，方便调试。"""
        resp = await self._client.post(_API_URL, json=self._build_payload())
        resp.raise_for_status()
        return resp.json()

    # ── 内部方法 ─────────────────────────────────────────────────

    async def _fetch_menu_data(self) -> list[dict]:
        resp = await self._client.post(_API_URL, json=self._build_payload())
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(
                f"KFC API 返回错误：code={data.get('code')}，msg={data.get('msg')}"
            )
        return data["data"]["dataMenu"]["menuData"]

    def _build_payload(self) -> dict:
        return {
            "portalType": "kfc_delivery_h5",
            "portalSource": "KFC_WEB",
            "channelName": "MWOS_H5",
            "channelId": "13",
            "brand": "KFC",
            "business": "delivery",
            "sessionId": _rand_str(32),
            "deviceId": _rand_str(21),
            "clientVersion": _CLIENT_VERSION,
            "fversion": _FVERSION,
            "versionNum": "5",
            "body": {
                "geoLocation": {
                    "lng": self.lng,
                    "lat": self.lat,
                }
            },
            "addressAndStoreEarly": {},
            "encodeList": [],
            "isFromCustomerClient": True,
            "secretKey": "kfc",
        }

    @staticmethod
    def _parse_items_into(
        category: MenuCategory,
        raw_items: list[dict],
        available: bool = True,
    ) -> None:
        for item in raw_items:
            price_fen = int(item.get("apiPrice") or item.get("price") or 0)
            orig_fen = int(item.get("apiOrgPrice") or item.get("priceInitial") or 0)
            img = item.get("imageUrl") or ""
            if img and not img.startswith("http"):
                img = _IMAGE_BASE + img
            category.items.append(
                MenuItem(
                    name=item.get("showNameCn") or item.get("nameCn") or "",
                    price=price_fen / 100,
                    orig_price=orig_fen / 100 if orig_fen > price_fen else 0.0,
                    category=category.name,
                    description=(item.get("descCn") or "").strip(),
                    image_url=img,
                    available=available and item.get("disabledStatus") != "1",
                    popular=item.get("lightFlag") == "1",
                )
            )


def _rand_str(n: int) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=n))
