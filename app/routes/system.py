from fastapi import FastAPI

from ._runtime import rideops_runtime


def include_routes(app: FastAPI) -> None:
    rideops = rideops_runtime()
    app.add_api_route("/", rideops.root, methods=["GET"])
    app.add_api_route("/features", rideops.get_features, methods=["GET"])
    app.add_api_route("/health/live", rideops.health_live, methods=["GET"])
    app.add_api_route("/health/ready", rideops.health_ready, methods=["GET"])
    app.add_api_route("/test/reset-demo-data", rideops.reset_test_demo_data, methods=["POST"])
    app.add_api_route("/vehicle-types", rideops.get_vehicle_types, methods=["GET"])
