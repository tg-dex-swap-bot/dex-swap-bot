import base64
from contextlib import suppress
from typing import List
import os

from aiogram import Dispatcher, Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    CallbackQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.utils.markdown import hide_link, hcode
from redis.asyncio import Redis

from src.Storage import TCRedisStorage
from tonutils.tonconnect import TonConnect
from tonutils.tonconnect.models import (
    WalletApp,
    Event,
    EventError,
)
from tonutils.tonconnect.utils.exceptions import (
    TonConnectError,
    UserRejectsError,
    RequestTimeoutError,
)

BOT_TOKEN = os.getenv("BOT_API_KEY")
REDIS_DSN = os.getenv("REDIS_DSN")
TC_MANIFEST_URL = "https://raw.githubusercontent.com/tg-dex-swap-bot/tonconnect-manifest/refs/heads/main/tonconnect-manifest.json" # noqa: E501


class SwapStates(StatesGroup):
    waiting_for_token_input = State()
    choosing_option = State()
    setting_slippage = State()
    setting_max_splits = State()
    setting_max_length = State()


redis = Redis.from_url(url=REDIS_DSN)
dp = Dispatcher(storage=RedisStorage(redis))
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
tc = TonConnect(
    storage=TCRedisStorage(redis),
    manifest_url=TC_MANIFEST_URL,
    wallets_fallback_file_path="../wallets.json"
)


async def _is_valid_token(token: str) -> bool:
    VALID_TOKENS = {"TON", "USDT", "USDC", "BTC", "ETH"}
    return token.upper() in VALID_TOKENS


async def _delete_prompt_message(user_id: int, state: FSMContext) -> None:
    state_data = await state.get_data()
    prompt_message_id = state_data.get("prompt_message_id")

    if prompt_message_id:
        with suppress(Exception):
            await bot.delete_message(
                chat_id=user_id, message_id=prompt_message_id
            )
        await state.update_data(prompt_message_id=None)


async def delete_last_message(user_id: int, message_id: int) -> None:
    state = dp.fsm.resolve_context(bot, user_id, user_id)
    last_message_id = (await state.get_data()).get("last_message_id")

    if last_message_id is not None:
        with suppress(Exception):
            await bot.delete_message(
                chat_id=user_id, message_id=last_message_id
            )

    await state.update_data(last_message_id=message_id)


def _connect_wallet_markup(
    wallets: List[WalletApp],
    selected_wallet: WalletApp,
    connect_url: str,
) -> InlineKeyboardMarkup:
    wallets_button = [
        *[
            InlineKeyboardButton(
                text=(
                    f"• {wallet.name} •"
                    if wallet.app_name == selected_wallet.app_name
                    else wallet.name
                ),
                callback_data=f"app_wallet:{wallet.app_name}",
            )
            for wallet in wallets
        ]
    ]
    connect_wallet_button = InlineKeyboardButton(
        text=f"Connect {selected_wallet.name}",
        url=connect_url,
    )
    builder = InlineKeyboardBuilder()
    builder.row(connect_wallet_button)
    builder.row(*wallets_button, width=2)

    return builder.as_markup()


def _swap_options_markup() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Slippage", callback_data="set_slippage")
    )
    builder.row(
        InlineKeyboardButton(text="Max Splits", callback_data="set_max_splits")
    )
    builder.row(
        InlineKeyboardButton(text="Max Length", callback_data="set_max_length")
    )
    builder.row(InlineKeyboardButton(text="Back", callback_data="back"))
    return builder.as_markup()


def _choose_action_markup() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Swap", callback_data="swap"))
    builder.row(
        InlineKeyboardButton(text="Options", callback_data="swap_options")
    )
    builder.row(
        InlineKeyboardButton(
            text="Disconnect wallet", callback_data="disconnect_wallet"
        )
    )
    return builder.as_markup()


def _swap_menu_markup() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Build root", callback_data="build_root")
    )
    builder.row(
        InlineKeyboardButton(text="Options", callback_data="swap_options")
    )
    builder.row(InlineKeyboardButton(text="Cancel", callback_data="main_menu"))
    return builder.as_markup()


async def show_swap_options(user_id: int, state: FSMContext):
    await _delete_prompt_message(user_id, state)

    data = await state.get_data()

    defaults = {"slippage": 0.5, "max_splits": 3, "max_length": 5}

    for key, default in defaults.items():
        if key not in data:
            await state.update_data(**{key: default})
            data[key] = default

    text = (
        "<b>Current values:</b>\n"
        f"Slippage = <code>{data['slippage']}</code>\n"
        f"Max Splits = <code>{data['max_splits']}</code>\n"
        f"Max Length = <code>{data['max_length']}</code>"
    )

    msg = await bot.send_message(
        chat_id=user_id, text=text, reply_markup=_swap_options_markup()
    )
    await state.update_data(prompt_message_id=msg.message_id)
    await state.set_state(SwapStates.choosing_option)


async def swap_input_window(user_id: int, state: FSMContext) -> None:
    await _delete_prompt_message(user_id, state)
    await state.set_state(SwapStates.waiting_for_token_input)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Cancel", callback_data="main_menu"))

    msg = await bot.send_message(
        chat_id=user_id,
        text=(
            "Enter tokens and amount in the format: <code>TOKEN1 TOKEN2 AMOUNT</code>\n"
            "Example: <code>USDT TON 10.5</code>"
        ),
        reply_markup=builder.as_markup(),
    )
    await state.update_data(prompt_message_id=msg.message_id)


async def connect_wallet_window(state: FSMContext, user_id: int) -> None:
    connector = await tc.init_connector(user_id)
    state_data = await state.get_data()
    wallets = await tc.get_wallets()

    selected_wallet = state_data.get("selected_wallet", wallets[0].app_name)
    selected_wallet = next(w for w in wallets if w.app_name == selected_wallet)
    connect_url = await connector.connect_wallet(wallet_app=selected_wallet)

    qrcode_url = (
        f"https://qrcode.ness.su/create?"
        f"box_size=20&border=7&image_padding=20"
        f"&data={base64.b64encode(connect_url.encode()).decode()}"
        f"&image_url={base64.b64encode(selected_wallet.image.encode()).decode()}"
    )

    text = f"{hide_link(qrcode_url)}Connect your wallet!"
    reply_markup = _connect_wallet_markup(
        wallets, selected_wallet, connect_url
    )

    message = await bot.send_message(
        chat_id=user_id, text=text, reply_markup=reply_markup
    )
    await delete_last_message(user_id, message.message_id)


async def wallet_connected_window(state: FSMContext, user_id: int) -> None:
    connector = await tc.init_connector(user_id)
    wallet_address = connector.wallet.account.address.to_str(
        is_bounceable=False
    )

    reply_markup = _choose_action_markup()
    text = f"Connected wallet:\n{hcode(wallet_address)}\n\nChoose an action:"

    await state.update_data(previous_screen="main_menu")

    message = await bot.send_message(
        chat_id=user_id, text=text, reply_markup=reply_markup
    )
    await delete_last_message(user_id, message.message_id)


async def error_window(
    user_id: int,
    message_text: str,
    button_text: str,
    callback_data: str,
    state: FSMContext,
) -> None:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=button_text, callback_data=callback_data)
    )
    reply_markup = builder.as_markup()

    message = await bot.send_message(
        chat_id=user_id, text=message_text, reply_markup=reply_markup
    )
    await state.update_data(prompt_message_id=message.message_id)


@tc.on_event(Event.CONNECT)
async def connect_event(user_id: int) -> None:
    state = dp.fsm.resolve_context(bot, user_id, user_id)
    await wallet_connected_window(state, user_id)


@tc.on_event(EventError.CONNECT)
async def connect_error_event(error: TonConnectError, user_id: int) -> None:
    button_text, callback_data = "Try again", "connect_wallet"
    if isinstance(error, UserRejectsError):
        message_text = "You rejected the wallet connection."
    elif isinstance(error, RequestTimeoutError):
        message_text = "Connection request timed out."
    else:
        message_text = f"Connection error. Error: {error.message}"
    await error_window(user_id, message_text, button_text, callback_data)


@tc.on_event(Event.DISCONNECT)
async def disconnect_event(user_id: int) -> None:
    state = dp.fsm.resolve_context(bot, user_id, user_id)
    await connect_wallet_window(state, user_id)


@tc.on_event(EventError.DISCONNECT)
async def disconnect_error_event(error: TonConnectError, user_id: int) -> None:
    button_text, callback_data = "Try again", "connect_wallet"
    if isinstance(error, UserRejectsError):
        message_text = "You rejected the wallet disconnection."
    elif isinstance(error, RequestTimeoutError):
        message_text = "Disconnect request timed out."
    else:
        message_text = f"Disconnect error. Error: {error.message}"

    await error_window(user_id, message_text, button_text, callback_data)


@dp.message(CommandStart())
async def start_command(message: Message, state: FSMContext) -> None:
    connector = await tc.init_connector(message.from_user.id)
    rpc_request_id = (await state.get_data()).get("rpc_request_id")
    if connector.is_transaction_pending(rpc_request_id):
        connector.cancel_pending_transaction(rpc_request_id)

    if not connector.connected:
        await connect_wallet_window(state, message.from_user.id)
    else:
        await wallet_connected_window(state, message.from_user.id)


@dp.callback_query()
async def callback_query_handler(
    callback_query: CallbackQuery, state: FSMContext
) -> None:
    connector = await tc.init_connector(callback_query.from_user.id)
    rpc_request_id = (await state.get_data()).get("rpc_request_id")

    if callback_query.data.startswith("app_wallet:"):
        selected_wallet = callback_query.data.split(":")[1]
        await state.update_data(selected_wallet=selected_wallet)
        await connect_wallet_window(state, callback_query.from_user.id)

    elif callback_query.data == "main_menu":
        await _delete_prompt_message(callback_query.from_user.id, state)
        await wallet_connected_window(state, callback_query.from_user.id)

    elif callback_query.data == "connect_wallet":
        await _delete_prompt_message(callback_query.from_user.id, state)
        await connect_wallet_window(state, callback_query.from_user.id)

    elif callback_query.data == "disconnect_wallet":
        await _delete_prompt_message(callback_query.from_user.id, state)
        connector.add_event_kwargs(Event.DISCONNECT, state=state)
        await connector.disconnect_wallet()

    elif callback_query.data == "cancel_transaction":
        if connector.is_transaction_pending(rpc_request_id):
            connector.cancel_pending_transaction(rpc_request_id)
        await _delete_prompt_message(callback_query.from_user.id, state)
        await wallet_connected_window(state, callback_query.from_user.id)

    elif callback_query.data == "swap":
        await swap_input_window(callback_query.from_user.id, state)

    elif callback_query.data == "build_root":
        await bot.send_message(
            callback_query.from_user.id,
            "Build root selected (logic will be here)",
        )

    elif callback_query.data == "swap_options":
        await show_swap_options(callback_query.from_user.id, state)

    elif callback_query.data == "set_slippage":
        await _delete_prompt_message(callback_query.from_user.id, state)
        msg = await bot.send_message(
            callback_query.from_user.id, "Enter new slippage value (e.g. 0.5):"
        )
        await state.update_data(prompt_message_id=msg.message_id)
        await state.set_state(SwapStates.setting_slippage)

    elif callback_query.data == "set_max_splits":
        await _delete_prompt_message(callback_query.from_user.id, state)
        msg = await bot.send_message(
            callback_query.from_user.id, "Enter new max splits value (e.g. 3):"
        )
        await state.update_data(prompt_message_id=msg.message_id)
        await state.set_state(SwapStates.setting_max_splits)

    elif callback_query.data == "set_max_length":
        await _delete_prompt_message(callback_query.from_user.id, state)
        msg = await bot.send_message(
            callback_query.from_user.id, "Enter new max length value (e.g. 5):"
        )
        await state.update_data(prompt_message_id=msg.message_id)
        await state.set_state(SwapStates.setting_max_length)

    elif callback_query.data == "back":
        data = await state.get_data()
        previous = data.get("previous_screen", "main_menu")

        await _delete_prompt_message(callback_query.from_user.id, state)

        if previous == "swap":
            await swap_input_window(callback_query.from_user.id, state)

        elif previous == "main_menu":
            await wallet_connected_window(state, callback_query.from_user.id)

    await callback_query.answer()


@dp.message(SwapStates.waiting_for_token_input)
async def token_input_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    error_params = {
        "user_id": user_id,
        "button_text": "Try again",
        "callback_data": "swap",
    }

    parts = message.text.strip().split()
    if len(parts) != 3:
        await error_window(
            **error_params,
            message_text="Invalid format. Use: <code>TOKEN1 TOKEN2 AMOUNT</code>\nExample: <code>USDT TON 10.5</code>",
            state=state,
        )
        return

    token1, token2, amount_str = map(str.upper, parts)

    for token in (token1, token2):
        if not await _is_valid_token(token):
            await error_window(
                **error_params,
                message_text=f"Token <code>{token}</code> is not supported.",
                state=state,
            )
            return

    try:
        amount = float(amount_str)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await error_window(
            **error_params,
            message_text="Amount must be a positive number.\nExample: <code>USDT TON 10.5</code>",
            state=state,
        )
        return

    await state.update_data(token1=token1, token2=token2, amount=amount)

    text = f"<b>Swap:</b> <code>{token1}</code> → <code>{token2}</code>\n<b>Amount:</b> {amount}"
    msg = await message.answer(text, reply_markup=_swap_menu_markup())
    await state.update_data(previous_screen="swap")
    await state.update_data(prompt_message_id=msg.message_id)


@dp.message(SwapStates.setting_slippage)
async def set_slippage(message: Message, state: FSMContext):
    try:
        value = float(message.text.strip())
        if value <= 0:
            raise ValueError
        await state.update_data(slippage=value)
        await message.answer(f"✅ Slippage set to {value}")
    except ValueError:
        await message.answer("❌ Invalid value. Enter a positive number.")
        return
    await show_swap_options(message.from_user.id, state)


@dp.message(SwapStates.setting_max_splits)
async def set_max_splits(message: Message, state: FSMContext):
    try:
        value = int(message.text.strip())
        if value <= 0:
            raise ValueError
        await state.update_data(max_splits=value)
        await message.answer(f"✅ Max Splits set to {value}")
    except ValueError:
        await message.answer("❌ Invalid value. Enter a positive integer.")
        return
    await show_swap_options(message.from_user.id, state)


@dp.message(SwapStates.setting_max_length)
async def set_max_length(message: Message, state: FSMContext):
    try:
        value = int(message.text.strip())
        if value <= 0:
            raise ValueError
        await state.update_data(max_length=value)
        await message.answer(f"✅ Max Length set to {value}")
    except ValueError:
        await message.answer("❌ Invalid value. Enter a positive integer.")
        return
    await show_swap_options(message.from_user.id, state)


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
