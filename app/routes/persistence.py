from fastapi import FastAPI

from ._runtime import rideops_runtime


def include_routes(app: FastAPI) -> None:
    rideops = rideops_runtime()
    app.add_api_route("/health/storage", rideops.health_storage, methods=["GET"])
