from fastapi import FastAPI

from ._runtime import rideops_runtime


def include_routes(app: FastAPI) -> None:
    rideops = rideops_runtime()
    app.add_api_route("/wallet", rideops.get_wallet, methods=["GET"])
    app.add_api_route("/wallet/top-up", rideops.top_up_wallet, methods=["POST"])
