"""
Product scraper: handles Shopee, Lazada, TikTok Shop, and generic URLs.
"""
import re
import json
import asyncio
from typing import Optional
from urllib.parse import urlparse, urlencode, urljoin
import httpx
from bs4 import BeautifulSoup

from backend.models import ProductInfo

# Common browser-like headers to avoid 403s
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


def detect_shop_type(url: str) -> str:
    parsed = urlparse(url.lower())
    host = parsed.netloc
    if "shopee" in host:
        return "shopee"
    if "lazada" in host:
        return "lazada"
    if "tiktok" in host and ("shop" in url.lower() or "v=" in url.lower()):
        return "tiktok_shop"
    return "unknown"


async def follow_redirect(url: str, client: httpx.AsyncClient) -> str:
    """Follow redirect chains to get the final URL."""
    try:
        response = await client.head(url, follow_redirects=True, timeout=15)
        return str(response.url)
    except Exception:
        return url


def extract_og_tags(soup: BeautifulSoup) -> dict:
    """Extract Open Graph metadata from a parsed page."""
    og = {}
    for tag in soup.find_all("meta"):
        prop = tag.get("property", "") or tag.get("name", "")
        content = tag.get("content", "")
        if prop.startswith("og:") or prop.startswith("product:"):
            og[prop] = content
        if prop in ("description", "twitter:description", "twitter:title"):
            og[prop] = content
    return og


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    return text[:2000]  # limit length


# ---------- Shopee ----------

async def scrape_shopee(url: str, client: httpx.AsyncClient) -> ProductInfo:
    """Scrape Shopee product page."""
    # Extract item_id and shop_id from URL
    # Shopee URL pattern: /product-name-i.SHOP_ID.ITEM_ID
    match = re.search(r"-i\.(\d+)\.(\d+)", url)
    shop_id = match.group(1) if match else None
    item_id = match.group(2) if match else None

    product_name = "Sản phẩm Shopee"
    description = ""
    price = ""
    currency = "VND"
    images = []

    if shop_id and item_id:
        api_url = (
            f"https://shopee.vn/api/v4/item/get?"
            f"itemid={item_id}&shopid={shop_id}"
        )
        try:
            resp = await client.get(api_url, headers=HEADERS, timeout=20)
            data = resp.json()
            item = data.get("data", {})
            if item:
                product_name = item.get("name", product_name)
                description = clean_text(item.get("description", ""))
                raw_price = item.get("price", 0) or item.get("price_min", 0)
                price = f"{int(raw_price / 100000):,}".replace(",", ".") if raw_price else ""
                # Images
                for img_hash in (item.get("images") or [])[:6]:
                    images.append(f"https://cf.shopee.vn/file/{img_hash}_tn")
        except Exception:
            pass

    # Fallback: scrape HTML
    if not images or not product_name or product_name == "Sản phẩm Shopee":
        try:
            resp = await client.get(url, headers=HEADERS, follow_redirects=True, timeout=20)
            soup = BeautifulSoup(resp.text, "lxml")
            og = extract_og_tags(soup)
            if not product_name or product_name == "Sản phẩm Shopee":
                product_name = og.get("og:title") or soup.title.string or product_name
            if not description:
                description = og.get("og:description") or og.get("description", "")
            if not images and og.get("og:image"):
                images.append(og["og:image"])
        except Exception:
            pass

    return ProductInfo(
        name=clean_text(product_name),
        description=clean_text(description),
        price=price,
        currency=currency,
        images=images[:5],
        shop_type="shopee",
        original_url=url,
    )


# ---------- Lazada ----------

async def scrape_lazada(url: str, client: httpx.AsyncClient) -> ProductInfo:
    """Scrape Lazada product page."""
    product_name = "Sản phẩm Lazada"
    description = ""
    price = ""
    currency = "VND"
    images = []

    try:
        resp = await client.get(url, headers=HEADERS, follow_redirects=True, timeout=20)
        soup = BeautifulSoup(resp.text, "lxml")
        og = extract_og_tags(soup)

        product_name = og.get("og:title") or ""

        # Try to extract JSON data from page script
        scripts = soup.find_all("script", type="application/ld+json")
        for script in scripts:
            try:
                ld = json.loads(script.string or "")
                if isinstance(ld, dict):
                    product_name = product_name or ld.get("name", "")
                    description = description or ld.get("description", "")
                    offers = ld.get("offers", {})
                    if isinstance(offers, dict):
                        price = str(offers.get("price", ""))
                        currency = offers.get("priceCurrency", "VND")
                    imgs = ld.get("image", [])
                    if isinstance(imgs, list):
                        images.extend(imgs[:5])
                    elif isinstance(imgs, str):
                        images.append(imgs)
            except Exception:
                pass

        # Try __NEXT_DATA__ or window.__data
        for script in soup.find_all("script"):
            text = script.string or ""
            if "pdpCoreLite" in text or "skuInfos" in text:
                # Try to extract price from JS object
                price_match = re.search(r'"price[^"]*":\s*"?(\d[\d,\.]+)"?', text)
                if price_match and not price:
                    price = price_match.group(1)
                break

        if not images and og.get("og:image"):
            images.append(og["og:image"])
        if not description:
            description = og.get("og:description") or og.get("description", "")
        if not product_name:
            product_name = soup.title.string or "Sản phẩm Lazada"

    except Exception:
        pass

    return ProductInfo(
        name=clean_text(product_name) or "Sản phẩm Lazada",
        description=clean_text(description),
        price=price,
        currency=currency,
        images=images[:5],
        shop_type="lazada",
        original_url=url,
    )


# ---------- TikTok Shop ----------

async def scrape_tiktok_shop(url: str, client: httpx.AsyncClient) -> ProductInfo:
    """Scrape TikTok Shop product page."""
    product_name = "Sản phẩm TikTok Shop"
    description = ""
    price = ""
    currency = "VND"
    images = []

    try:
        resp = await client.get(url, headers=HEADERS, follow_redirects=True, timeout=20)
        soup = BeautifulSoup(resp.text, "lxml")
        og = extract_og_tags(soup)

        product_name = og.get("og:title") or ""

        # Try ld+json
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                ld = json.loads(script.string or "")
                if isinstance(ld, dict) and ld.get("@type") == "Product":
                    product_name = product_name or ld.get("name", "")
                    description = description or ld.get("description", "")
                    imgs = ld.get("image", [])
                    if isinstance(imgs, list):
                        images.extend(imgs[:5])
                    elif isinstance(imgs, str):
                        images.append(imgs)
            except Exception:
                pass

        if not images and og.get("og:image"):
            images.append(og["og:image"])
        if not description:
            description = og.get("og:description", "")
        if not product_name:
            product_name = soup.title.string or "Sản phẩm TikTok Shop"

    except Exception:
        pass

    return ProductInfo(
        name=clean_text(product_name) or "Sản phẩm TikTok Shop",
        description=clean_text(description),
        price=price,
        currency=currency,
        images=images[:5],
        shop_type="tiktok_shop",
        original_url=url,
    )


# ---------- Generic fallback ----------

async def scrape_generic(url: str, client: httpx.AsyncClient) -> ProductInfo:
    """Generic scraper using Open Graph tags and common selectors."""
    product_name = ""
    description = ""
    price = ""
    currency = "VND"
    images = []

    try:
        resp = await client.get(url, headers=HEADERS, follow_redirects=True, timeout=20)
        soup = BeautifulSoup(resp.text, "lxml")
        og = extract_og_tags(soup)

        product_name = og.get("og:title") or ""
        description = og.get("og:description") or og.get("description", "")

        if og.get("og:image"):
            images.append(og["og:image"])

        # Try product price selectors
        for selector in [
            "[itemprop='price']",
            ".price",
            ".product-price",
            "[class*='price']",
        ]:
            el = soup.select_one(selector)
            if el:
                price_text = el.get_text(strip=True)
                price_match = re.search(r"[\d,\.]+", price_text)
                if price_match:
                    price = price_match.group()
                    break

        # Try ld+json
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                ld = json.loads(script.string or "")
                if isinstance(ld, list):
                    ld = ld[0]
                if isinstance(ld, dict):
                    product_name = product_name or ld.get("name", "")
                    description = description or ld.get("description", "")
                    offers = ld.get("offers", {})
                    if isinstance(offers, dict):
                        price = price or str(offers.get("price", ""))
                        currency = offers.get("priceCurrency", "VND")
                    imgs = ld.get("image", [])
                    if isinstance(imgs, str):
                        imgs = [imgs]
                    images.extend(imgs[:5])
            except Exception:
                pass

        if not product_name:
            product_name = soup.title.string or urlparse(url).netloc

        # Collect more images from og:image:*
        for tag in soup.find_all("meta", property=re.compile("og:image")):
            img = tag.get("content", "")
            if img and img not in images:
                images.append(img)

    except Exception as e:
        raise ValueError(f"Không thể tải trang sản phẩm: {e}")

    if not product_name:
        raise ValueError("Không tìm thấy thông tin sản phẩm từ URL này")

    return ProductInfo(
        name=clean_text(product_name),
        description=clean_text(description),
        price=price,
        currency=currency,
        images=list(dict.fromkeys(images))[:5],  # deduplicate
        shop_type="unknown",
        original_url=url,
    )


# ---------- Main entry ----------

async def scrape_product(url: str) -> ProductInfo:
    """Detect shop type and scrape product info."""
    shop_type = detect_shop_type(url)

    limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
    async with httpx.AsyncClient(
        limits=limits,
        follow_redirects=True,
        timeout=30,
    ) as client:
        if shop_type == "shopee":
            return await scrape_shopee(url, client)
        elif shop_type == "lazada":
            return await scrape_lazada(url, client)
        elif shop_type == "tiktok_shop":
            return await scrape_tiktok_shop(url, client)
        else:
            return await scrape_generic(url, client)
