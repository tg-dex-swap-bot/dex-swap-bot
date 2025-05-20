import requests
import json
import os

class SwapCoffeeAPI:
    def __init__(self):
        self.base_url = "https://backend.swap.coffee"
        self.cache_file = "tokens_cache.json"

    def get_tokens(self):
        """
        Получает список токенов и сохраняет результат в кэш

        Returns:
            dict: Ответ API с информацией о токенах
        """
        url = f"{self.base_url}/v1/tokens"

        try:
            response = requests.get(url)
            response.raise_for_status()  # Проверка на ошибки HTTP

            # Сохраняем ответ в кэш
            tokens_data = response.json()
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(tokens_data, f, ensure_ascii=False, indent=4)

            return tokens_data
        except requests.exceptions.RequestException as e:
            print(f"Ошибка при получении токенов: {e}")

            # Если есть кэш, возвращаем данные из него
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return None

    def get_route(self, input_blockchain, input_address, output_blockchain, output_address, input_amount):
        """
        Получает маршрут обмена между токенами

        Args:
            input_blockchain (str): Блокчейн входного токена
            input_address (str): Адрес входного токена
            output_blockchain (str): Блокчейн выходного токена
            output_address (str): Адрес выходного токена
            input_amount (float): Количество входного токена

        Returns:
            dict: Ответ API с информацией о маршруте обмена
        """
        url = f"{self.base_url}/v1/route"

        payload = {
            "input_token": {
                "blockchain": input_blockchain,
                "address": input_address
            },
            "output_token": {
                "blockchain": output_blockchain,
                "address": output_address
            },
            "input_amount": input_amount
        }

        try:
            response = requests.post(url, json=payload)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Ошибка при получении маршрута: {e}")
            return None

    def get_route_transactions(self, **params):
        """
        Получает информацию о транзакциях для маршрута

        Args:
            **params: Параметры для запроса

        Returns:
            dict: Ответ API с информацией о транзакциях
        """
        url = f"{self.base_url}/v2/route/transactions"

        try:
            response = requests.post(url, json=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Ошибка при получении информации о транзакциях: {e}")
            return None

# Пример использования
if __name__ == "__main__":
    api = SwapCoffeeAPI()

    # Пример 1: Получение списка токенов
    tokens = api.get_tokens()
    if tokens:
        print("Токены успешно получены и сохранены в кэш")

    # Пример 2: Получение маршрута обмена
    route = api.get_route(
        input_blockchain="ton",
        input_address="EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs",
        output_blockchain="ton",
        output_address="EQC98_qAmNEptUtPc7W6xdHh_ZHrBUFpw5Ft_IzNU20QAJav",
        input_amount=1
    )
    if route:
        print("Маршрут обмена успешно получен")
        print(json.dumps(route, indent=4))

    # Пример 3: Получение информации о транзакциях
    # Здесь нужно передать необходимые параметры в соответствии с API
    transactions = api.get_route_transactions(
        # Пример параметров - замените на реальные
        route_id="example_route_id",
        sender_address="example_sender_address"
    )
    if transactions:
        print("Информация о транзакциях успешно получена")
        print(json.dumps(transactions, indent=4))