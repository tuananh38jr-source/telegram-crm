import os
import aiohttp
from sqlalchemy.orm import Session
from app.models import Channel, ChannelGrowth
from datetime import datetime


BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")


async def fetch_channel_member_count(channel: Channel) -> dict:
    """
    Lấy số thành viên của Telegram channel qua Bot API.
    Hỗ trợ cả @username và chat_id.
    """
    if not BOT_TOKEN:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN chưa được cấu hình"}

    chat_id = channel.telegram_id or channel.username or channel.invite_link
    if not chat_id:
        return {"ok": False, "error": "Channel chưa có telegram_id, username hoặc invite_link"}

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMemberCount"
    payload = {"chat_id": chat_id}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                data = await resp.json()
                if data.get("ok"):
                    return {"ok": True, "count": data["result"]}
                return {"ok": False, "error": data.get("description", "Lỗi không xác định")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def sync_channel_members(db: Session, channel_id: int) -> dict:
    """
    Đồng bộ số thành viên channel và lưu vào bảng channel_growth.
    Tính delta so với lần đo trước.
    """
    channel = db.query(Channel).filter(Channel.id == channel_id).first()
    if not channel:
        return {"ok": False, "error": "Không tìm thấy channel"}

    result = await fetch_channel_member_count(channel)
    if not result["ok"]:
        return result

    current_count = result["count"]
    previous = (
        db.query(ChannelGrowth)
        .filter(ChannelGrowth.channel_id == channel_id)
        .order_by(ChannelGrowth.recorded_at.desc())
        .first()
    )

    previous_count = previous.total_members if previous else current_count
    delta = current_count - previous_count

    growth = ChannelGrowth(
        channel_id=channel_id,
        total_members=current_count,
        new_members=max(delta, 0),
        left_members=max(-delta, 0),
        paid_joins=0,
    )
    db.add(growth)

    channel.current_members = current_count
    db.commit()
    db.refresh(growth)

    return {
        "ok": True,
        "channel_id": channel_id,
        "total_members": current_count,
        "delta": delta,
        "recorded_at": growth.recorded_at,
    }


async def sync_all_channels(db: Session) -> list:
    """Đồng bộ tất cả channel đang active."""
    channels = db.query(Channel).filter(Channel.is_active == True).all()
    results = []
    for channel in channels:
        result = await sync_channel_members(db, channel.id)
        results.append(result)
    return results
