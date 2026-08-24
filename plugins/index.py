import logging
import asyncio
import re

from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait
from pyrogram.errors.exceptions.bad_request_400 import (
    ChannelInvalid,
    ChatAdminRequired,
    UsernameInvalid,
    UsernameNotModified,
)

from info import ADMINS
from info import INDEX_REQ_CHANNEL as LOG_CHANNEL
from database.ia_filterdb import save_file
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from utils import temp

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

lock = asyncio.Lock()


# ============================================================
# /index COMMAND
# Usage:
# /index https://t.me/c/1234567890/25
# ============================================================

@Client.on_message(filters.command("index") & filters.private & filters.incoming)
async def index_command(bot, message):

    if message.from_user.id not in ADMINS:
        return await message.reply_text(
            "You are not authorized to use this command."
        )

    if len(message.command) < 2:
        return await message.reply_text(
            "Send the channel post link.\n\n"
            "Example:\n"
            "/index https://t.me/c/1234567890/25"
        )

    link = message.command[1].strip()

    regex = re.compile(
        r"(https://)?"
        r"(t\.me/|telegram\.me/|telegram\.dog/)"
        r"(c/)?"
        r"(\d+|[a-zA-Z_0-9]+)/"
        r"(\d+)$"
    )

    match = regex.match(link)

    if not match:
        return await message.reply_text(
            "❌ Invalid Telegram message link."
        )

    chat_id = match.group(4)
    last_msg_id = int(match.group(5))

    # Private channel ID
    if chat_id.isnumeric():
        chat_id = int("-100" + chat_id)

    # Check channel access
    try:
        await bot.get_chat(chat_id)
    except ChannelInvalid:
        return await message.reply_text(
            "❌ I cannot access this channel.\n"
            "Make sure I am an admin in the channel."
        )
    except (UsernameInvalid, UsernameNotModified):
        return await message.reply_text(
            "❌ Invalid channel username/link."
        )
    except Exception as e:
        logger.exception(e)
        return await message.reply_text(
            f"❌ Error:\n{e}"
        )

    # Check message
    try:
        target_message = await bot.get_messages(
            chat_id,
            last_msg_id
        )
    except Exception as e:
        logger.exception(e)
        return await message.reply_text(
            "❌ I cannot read this message.\n"
            "Make sure I am an admin in the channel."
        )

    if target_message.empty:
        return await message.reply_text(
            "❌ Message not found."
        )

    # Confirm indexing
    buttons = [
        [
            InlineKeyboardButton(
                "✅ Start Index",
                callback_data=(
                    f"index#accept#{chat_id}#{last_msg_id}"
                    f"#{message.from_user.id}"
                )
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data="close_data"
            )
        ]
    ]

    await message.reply_text(
        f"Do you want to index this channel?\n\n"
        f"Chat ID: <code>{chat_id}</code>\n"
        f"Message ID: <code>{last_msg_id}</code>",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.HTML
    )


# ============================================================
# CALLBACK INDEX
# ============================================================

@Client.on_callback_query(filters.regex(r"^index"))
async def index_files(bot, query):

    if query.data.startswith("index_cancel"):
        temp.CANCEL = True
        return await query.answer(
            "Cancelling Indexing..."
        )

    try:
        _, action, chat, lst_msg_id, from_user = query.data.split("#")
    except ValueError:
        return await query.answer(
            "Invalid indexing request.",
            show_alert=True
        )

    if action == "reject":
        await query.message.delete()

        await bot.send_message(
            int(from_user),
            "Your indexing request was declined.",
            reply_to_message_id=int(lst_msg_id)
        )
        return

    if lock.locked():
        return await query.answer(
            "Wait until previous indexing is complete.",
            show_alert=True
        )

    if int(from_user) not in ADMINS:
        return await query.answer(
            "You are not authorized.",
            show_alert=True
        )

    msg = query.message

    await query.answer(
        "Starting indexing...",
        show_alert=True
    )

    await msg.edit_text(
        "⏳ <b>Starting Indexing...</b>",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data="index_cancel"
                    )
                ]
            ]
        ),
        parse_mode=enums.ParseMode.HTML
    )

    try:
        chat = int(chat)
    except ValueError:
        pass

    await index_files_to_db(
        int(lst_msg_id),
        chat,
        msg,
        bot
    )


# ============================================================
# FORWARD / TELEGRAM LINK INDEX REQUEST
# ============================================================

@Client.on_message(
    (
        filters.forwarded |
        (
            filters.regex(
                r"(https://)?"
                r"(t\.me/|telegram\.me/|telegram\.dog/)"
                r"(c/)?"
                r"(\d+|[a-zA-Z_0-9]+)/"
                r"(\d+)$"
            )
            & filters.text
        )
    )
    & filters.private
    & filters.incoming
)
async def send_for_index(bot, message):

    # Telegram link
    if message.text:

        regex = re.compile(
            r"(https://)?"
            r"(t\.me/|telegram\.me/|telegram\.dog/)"
            r"(c/)?"
            r"(\d+|[a-zA-Z_0-9]+)/"
            r"(\d+)$"
        )

        match = regex.match(message.text.strip())

        if not match:
            return await message.reply_text(
                "❌ Invalid Telegram link."
            )

        chat_id = match.group(4)
        last_msg_id = int(match.group(5))

        if chat_id.isnumeric():
            chat_id = int("-100" + chat_id)

    # Forwarded channel message
    elif (
        message.forward_from_chat
        and message.forward_from_chat.type == enums.ChatType.CHANNEL
    ):

        last_msg_id = message.forward_from_message_id

        chat_id = (
            message.forward_from_chat.username
            or message.forward_from_chat.id
        )

    else:
        return

    # Check access
    try:
        await bot.get_chat(chat_id)

    except ChannelInvalid:
        return await message.reply_text(
            "❌ This may be a private channel/group.\n"
            "Make me an admin there to index the files."
        )

    except (UsernameInvalid, UsernameNotModified):
        return await message.reply_text(
            "❌ Invalid link specified."
        )

    except Exception as e:
        logger.exception(e)
        return await message.reply_text(
            f"❌ Error:\n{e}"
        )

    # Check message access
    try:
        target = await bot.get_messages(
            chat_id,
            last_msg_id
        )
    except Exception:
        return await message.reply_text(
            "❌ Make sure I am an admin in the channel."
        )

    if target.empty:
        return await message.reply_text(
            "❌ Message not found."
        )

    # Admin can directly index
    if message.from_user.id in ADMINS:

        buttons = [
            [
                InlineKeyboardButton(
                    "✅ Yes, Index",
                    callback_data=(
                        f"index#accept#{chat_id}#{last_msg_id}"
                        f"#{message.from_user.id}"
                    )
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ Close",
                    callback_data="close_data"
                )
            ]
        ]

        return await message.reply_text(
            f"Do you want to index this Channel/Group?\n\n"
            f"Chat ID: <code>{chat_id}</code>\n"
            f"Last Message ID: <code>{last_msg_id}</code>",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=enums.ParseMode.HTML
        )

    # Non-admin users
    if isinstance(chat_id, int):

        try:
            link = (
                await bot.create_chat_invite_link(
                    chat_id
                )
            ).invite_link

        except ChatAdminRequired:
            return await message.reply_text(
                "Make sure I am an admin in the channel "
                "with permission to invite users."
            )

    else:
        link = f"@{message.forward_from_chat.username}"

    buttons = [
        [
            InlineKeyboardButton(
                "✅ Accept Index",
                callback_data=(
                    f"index#accept#{chat_id}#{last_msg_id}"
                    f"#{message.from_user.id}"
                )
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Reject",
                callback_data=(
                    f"index#reject#{chat_id}#{message.id}"
                    f"#{message.from_user.id}"
                )
            )
        ]
    ]

    try:
        await bot.send_message(
            LOG_CHANNEL,
            f"#IndexRequest\n\n"
            f"By: {message.from_user.mention}\n"
            f"User ID: <code>{message.from_user.id}</code>\n"
            f"Chat ID/Username: <code>{chat_id}</code>\n"
            f"Last Message ID: <code>{last_msg_id}</code>\n"
            f"Invite Link: {link}",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=enums.ParseMode.HTML
        )

    except Exception as e:
        logger.exception(e)
        return await message.reply_text(
            f"❌ Could not send indexing request.\n\n{e}"
        )

    await message.reply_text(
        "✅ Thank you for the contribution.\n"
        "Wait for moderators to verify the files."
    )


# ============================================================
# SET SKIP
# ============================================================

@Client.on_message(
    filters.command("setskip") & filters.user(ADMINS)
)
async def set_skip_number(bot, message):

    if " " in message.text:

        _, skip = message.text.split(
            " ",
            1
        )

        try:
            skip = int(skip)
        except ValueError:
            return await message.reply_text(
                "Skip number should be an integer."
            )

        temp.CURRENT = skip

        await message.reply_text(
            f"Successfully set SKIP number as {skip}"
        )

    else:

        await message.reply_text(
            "Give me a skip number."
        )


# ============================================================
# INDEX FILES
# ============================================================

async def index_files_to_db(
    lst_msg_id,
    chat,
    msg,
    bot
):

    total_files = 0
    duplicate = 0
    errors = 0
    deleted = 0
    no_media = 0
    unsupported = 0

    async with lock:

        try:

            current = temp.CURRENT
            temp.CANCEL = False

            async for message in bot.iter_messages(
                chat,
                lst_msg_id,
                temp.CURRENT
            ):

                if temp.CANCEL:

                    await msg.edit_text(
                        f"❌ <b>Indexing Cancelled</b>\n\n"
                        f"Saved: <code>{total_files}</code>\n"
                        f"Duplicate: <code>{duplicate}</code>\n"
                        f"Deleted: <code>{deleted}</code>\n"
                        f"Non-media: <code>{no_media + unsupported}</code>\n"
                        f"Errors: <code>{errors}</code>",
                        parse_mode=enums.ParseMode.HTML
                    )

                    break

                current += 1

                if current % 20 == 0:

                    await msg.edit_text(
                        f"📊 <b>Indexing...</b>\n\n"
                        f"Fetched: <code>{current}</code>\n"
                        f"Saved: <code>{total_files}</code>\n"
                        f"Duplicate: <code>{duplicate}</code>\n"
                        f"Deleted: <code>{deleted}</code>\n"
                        f"Non-media: <code>{no_media + unsupported}</code>\n"
                        f"Errors: <code>{errors}</code>",
                        reply_markup=InlineKeyboardMarkup(
                            [
                                [
                                    InlineKeyboardButton(
                                        "❌ Cancel",
                                        callback_data="index_cancel"
                                    )
                                ]
                            ]
                        ),
                        parse_mode=enums.ParseMode.HTML
                    )

                if message.empty:
                    deleted += 1
                    continue

                if not message.media:
                    no_media += 1
                    continue

                if message.media not in [
                    enums.MessageMediaType.VIDEO,
                    enums.MessageMediaType.AUDIO,
                    enums.MessageMediaType.DOCUMENT,
                ]:
                    unsupported += 1
                    continue

                media = getattr(
                    message,
                    message.media.value,
                    None
                )

                if not media:
                    unsupported += 1
                    continue

                media.file_type = message.media.value
                media.caption = message.caption

                saved, status = await save_file(media)

                if saved:
                    total_files += 1

                elif status == 0:
                    duplicate += 1

                elif status == 2:
                    errors += 1

        except FloodWait as e:

            await asyncio.sleep(
                e.value
            )

            await msg.edit_text(
                "FloodWait occurred. Please try again."
            )

        except Exception as e:

            logger.exception(e)

            await msg.edit_text(
                f"❌ <b>Error:</b>\n<code>{e}</code>",
                parse_mode=enums.ParseMode.HTML
            )

        else:

            await msg.edit_text(
                f"✅ <b>Indexing Completed</b>\n\n"
                f"Saved: <code>{total_files}</code>\n"
                f"Duplicate: <code>{duplicate}</code>\n"
                f"Deleted: <code>{deleted}</code>\n"
                f"Non-media: <code>{no_media + unsupported}</code>\n"
                f"Errors: <code>{errors}</code>",
                parse_mode=enums.ParseMode.HTML
            )
