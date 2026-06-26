"""
AI content generation using Groq (Llama 3).
Generates Vietnamese video scripts, captions, and hashtags for TikTok affiliate marketing.
"""
from groq import AsyncGroq
from backend.config import settings
from backend.models import ProductInfo, ContentResult


SYSTEM_PROMPT = """Bạn là chuyên gia marketing TikTok người Việt Nam, chuyên tạo nội dung bán hàng affiliate viral.
Bạn tạo script video ngắn hấp dẫn, caption TikTok chuyên nghiệp và hashtag phù hợp.
Luôn viết bằng tiếng Việt tự nhiên, gần gũi, dùng ngôn ngữ Gen Z khi phù hợp.
Nội dung phải trung thực, không phóng đại quá mức."""


def build_prompt(product_info: ProductInfo, notes: str = "") -> str:
    price_str = f"{product_info.price} {product_info.currency}" if product_info.price else "Liên hệ để biết giá"
    notes_section = f"\nGhi chú đặc biệt từ người dùng: {notes}" if notes else ""

    return f"""Tạo nội dung TikTok affiliate marketing cho sản phẩm sau:

**Tên sản phẩm:** {product_info.name}
**Giá:** {price_str}
**Mô tả:** {product_info.description[:500] if product_info.description else "Không có mô tả"}
**Loại shop:** {product_info.shop_type}{notes_section}

Hãy tạo JSON với cấu trúc sau (không có text ngoài JSON):
{{
  "hook": "Câu mở đầu cực kỳ hấp dẫn để thu hút người xem trong 3 giây đầu (1-2 câu)",
  "video_script": "Script đầy đủ cho video 30-60 giây, viết như đang nói chuyện tự nhiên, có điểm nhấn về lợi ích sản phẩm, kêu gọi hành động mua hàng. Không có dấu ngoặc hoặc chú thích kỹ thuật, chỉ là lời thoại thuần túy.",
  "audio_text": "Bản script sạch chỉ dùng cho TTS — không có emoji, không có ký tự đặc biệt, không có dấu gạch ngang hay dấu sao, chỉ là văn bản thuần. Dài 30-60 giây khi đọc với tốc độ bình thường.",
  "caption": "Caption TikTok hấp dẫn có emoji, kể câu chuyện ngắn về sản phẩm, kêu gọi comment/like, link affiliate ở phần mô tả. Tối đa 150 từ.",
  "hashtags": ["hashtag1", "hashtag2", "hashtag3", "hashtag4", "hashtag5", "hashtag6", "hashtag7", "hashtag8", "hashtag9", "hashtag10"]
}}

Lưu ý hashtags:
- Mix tiếng Việt và tiếng Anh
- Bao gồm: hashtag sản phẩm cụ thể, hashtag danh mục (ví dụ: #muasắm), hashtag TikTok phổ biến (#tiktokviral, #xuhuong), hashtag affiliate (#affiliate #review)
- Không có dấu # trong mảng, chỉ tên hashtag
- Tối đa 10 hashtags"""


async def generate_content(product_info: ProductInfo, notes: str = "") -> ContentResult:
    """Call Groq API to generate video content."""
    client = AsyncGroq(api_key=settings.GROQ_API_KEY)

    prompt = build_prompt(product_info, notes)

    message = await client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        max_tokens=2048,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )
    response_text = message.choices[0].message.content.strip()

    # Parse JSON response
    import json
    import re

    # Extract JSON if wrapped in markdown code blocks
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        # Try to find raw JSON
        json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
        json_str = json_match.group(0) if json_match else response_text

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        # Fallback: create minimal content from raw response
        data = {
            "hook": "Sản phẩm này thực sự tuyệt vời!",
            "video_script": response_text,
            "audio_text": response_text,
            "caption": f"Review {product_info.name} - Sản phẩm đáng mua!\n\nLink mua hàng ở bio nhé!",
            "hashtags": ["muasắm", "review", "affiliate", "tiktokviral", "xuhuong"],
        }

    hashtags = data.get("hashtags", [])
    if isinstance(hashtags, str):
        hashtags = [h.strip().lstrip("#") for h in hashtags.split() if h.strip()]

    return ContentResult(
        video_script=data.get("video_script", ""),
        caption=data.get("caption", ""),
        hashtags=hashtags[:10],
        hook=data.get("hook", ""),
        audio_text=data.get("audio_text", data.get("video_script", "")),
    )
