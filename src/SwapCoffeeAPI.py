import requests

swap_coffee_url = "https://backend.swap.coffee"

class SwapCoffeeException(Exception):
    ...
    
def get_tokens() -> list:
    """
    Get a list of supported tokens

    Returns:
        list: Supported tokens
    """
    url = f"{swap_coffee_url}/v1/tokens"

    try:
        response = requests.get(url)
        response.raise_for_status()  # Проверка на ошибки HTTP

        tokens_data = response.json()

        return tokens_data

    except requests.exceptions.RequestException as e:
        raise SwapCoffeeException(e)

def get_swap_route(
        input_address: str,
        output_address: str,
        amount: float,
        is_input: bool = True
) -> dict:
    """
    Get a swap route for two tokens of requested amount

    Args:
        input_address (str): Input token address
        output_address (str): Output token address
        amount (float): Swap amount
        is_input (bool): If True, amount is set as input, else for output

    Returns:
        dict: https://backend.swap.coffee/swagger-ui#/Routing/buildRoute
    """
    url = f"{swap_coffee_url}/v1/route"

    payload: dict = {
        "input_token": {
            "blockchain": "ton",
            "address": input_address
        },
        "output_token": {
            "blockchain": "ton",
            "address": output_address
        }
    }

    if is_input:
        payload["input_amount"] = amount
    else:
        payload["output_amount"] = amount

    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()

        return response.json()

    except requests.exceptions.RequestException as e:
        raise SwapCoffeeException(e)

def get_prepared_transaction(
        sender_address: str,
        slippage: float,
        paths: list,
        mev_protection: bool = True
) -> dict:
    """
    Get prepared transaction for given swap route

    Args:
        sender_address (str): User TON address
        slippage (float): max price slippage
        paths (list): paths field from get_swap_route

    Returns:
        dict: https://backend.swap.coffee/swagger-ui#/Routing/buildTransactionsV2
    """
    url = f"{swap_coffee_url}/v2/route/transactions"

    payload = {
        "sender_address": sender_address,
        "slippage": slippage,
        "mev_protection": mev_protection,
        "paths": paths
    }

    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()

        return response.json()

    except requests.exceptions.RequestException as e:
        raise SwapCoffeeException(e)
