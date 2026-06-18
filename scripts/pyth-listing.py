import json
import requests

if __name__ == "__main__":
    urls = [
        "https://benchmarks.pyth.network/v1/shims/tradingview/symbol_info?group=pyth_stock",
        "https://benchmarks.pyth.network/v1/shims/tradingview/symbol_info?group=pyth_crypto",
        "https://benchmarks.pyth.network/v1/shims/tradingview/symbol_info?group=pyth_crypto_rr",
    ]
    assets = []
    for url in urls:
        response = requests.get(url)
        data = response.json()

        search = [
            "SOLUSD",
            "GLD",
            "SIVR",
            "AAPL",
            "IVV",
            "TSLA",
            "SOLANA",
            "SOL",
            "BTC",
            "ETH",
            "TAO",
            "MODE",
            "SOLANA",
            "SOL",
        ]

        for item in zip(
            data["symbol"],
            data["description"],
            data["session-regular"],
            data["base-currency"],
            data["ticker"],
            data["supported-resolutions"],
        ):
            search_result = False
            for s in search:
                if (item[0] is not None and s in item[0]) or (
                    item[3] is not None and s in item[3]
                ):
                    search_result = True
                    break
                if item[1] is not None and s in item[1]:
                    search_result = True
                    break
            if search_result:
                assets.append(
                    {
                        "symbol": item[0],
                        "description": item[1],
                        "session-regular": item[2],
                        "base-currency": item[3],
                        "ticker": item[4],
                        "supported-resolutions": item[5],
                    }
                )

    print(json.dumps(assets, indent=2, ensure_ascii=False))
