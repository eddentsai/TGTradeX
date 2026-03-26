ERROR_CODES: dict[int, str] = {
    0: "Success",
    10001: "Network Error",
    10002: "Parameter Error",
    10003: "api-key can't be empty",
    10004: "The current ip is not in the apikey ip whitelist",
    10005: "Too many requests",
    10006: "Request too frequently",
    10007: "Sign signature error",
    10008: "{value} does not comply with the rule",
    20001: "Market not exists",
    20002: "Positions amount exceeded max limit",
    20003: "Insufficient balance",
    20004: "Insufficient Trader",
    20005: "Invalid leverage",
    20006: "Can't change leverage/margin mode with open orders",
    20007: "Order not found",
    20008: "Insufficient amount",
    20009: "Position exists, can't update position mode",
    30001: "Failed to order - price may liquidate immediately",
    30002: "Price below liquidated price",
    30003: "Price above liquidated price",
    30004: "Position not exist",
    30005: "Trigger price may be triggered immediately",
}


def get_error_message(code: int) -> str:
    return ERROR_CODES.get(code, f"Unknown error code: {code}")
