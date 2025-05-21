from typing import Optional
from decimal import Decimal

from src.SwapCoffeeAPI import get_swap_route, get_prepared_transaction
from tonutils.tonconnect.models import SendTransactionResponse
from tonutils.tonconnect.models import Transaction, Message
import time
async def initiate_swap_transaction(
    connector,
    sender_address: str,
    input_token_address: str,
    output_token_address: str,
    amount: Decimal,
    slippage: float = 0.05,
    is_input_amount: bool = True,
    mev_protection: bool = True
) -> Optional[SendTransactionResponse]:
    """
    Инициирует транзакцию свопа между токенами через кошелек.

    Args:
        connector: Инициализированный коннектор к TON кошельку
        sender_address (str): Адрес отправителя (кошелька)
        input_token_address (str): Адрес входного токена
        output_token_address (str): Адрес выходного токена
        amount (Decimal): Объем транзакции
        slippage (float): Максимальное проскальзывание цены (по умолчанию 5%)
        is_input_amount (bool): True если amount - это количество входного токена, 
                              False если amount - это желаемое количество выходного токена
        mev_protection (bool): Использовать ли защиту от MEV (по умолчанию True)

    Returns:
        Optional[SendTransactionResponse]: Ответ от транзакции если успешно, None если произошла ошибка

    Raises:
        SwapCoffeeException: Если произошла ошибка при получении маршрута или подготовке транзакции
        TonConnectError: Если произошла ошибка при взаимодействии с кошельком
    """
    try:
        # Получаем оптимальный маршрут для свопа
        route = get_swap_route(
            input_token_address,
            output_token_address,
            float(amount),
            is_input_amount
        )

        # Подготавливаем транзакцию
        prepared_transaction = get_prepared_transaction(
            sender_address,
            slippage,
            route["paths"],
            mev_protection
        )

        # Отправляем транзакцию через кошелек
        tx_data = prepared_transaction["transactions"][0]
        message = Message(
            address=tx_data["address"],
            amount=str(tx_data["value"]),
            payload=tx_data["cell"],
            state_init=tx_data.get("stateInit")  # Используем .get(), чтобы избежать KeyError
        )
        rpc_request_id = await connector.send_transaction(
            transaction=Transaction(
                valid_until=int(time.time()) + 600,
                messages=[message]  # Передаём объект. Кстати, batch-send будет всего лишь листом из message.
            )
        )
        return rpc_request_id

    except Exception as e:
        print(f"Ошибка при инициации транзакции: {str(e)}")
        return None 

async def test_initiate_swap(connector):
    """Тестовая функция для проверки инициации транзакции"""
    try:
        # Тестовые данные
        test_sender = "UQCf_BAw_HyPyF1YMNT_jQLHYbPGaKAQcaAyoMjx2qdAy2wb"
        input_token = "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"
        output_token = "EQB420yQsZobGcy0VYDfSKHpG2QQlw-j1f_tPu1J488I__PX"
        amount = Decimal("1.0")
        
        print("Запуск теста инициации транзакции...")
        result = await initiate_swap_transaction(
            connector=connector,
            sender_address=test_sender,
            input_token_address=input_token,
            output_token_address=output_token,
            amount=amount
        )
        
        if result:
            print(f"Тест успешен! Результат: {result}")
            return result
        else:
            print("Тест завершился с None результатом")
            return None
            
    except Exception as e:
        print(f"Ошибка при тестировании: {str(e)}")
        raise