from fastapi import FastAPI

from ._runtime import rideops_runtime


def include_routes(app: FastAPI) -> None:
    rideops = rideops_runtime()
    app.add_api_route("/drivers/location", rideops.update_driver_location, methods=["POST"])
    app.add_api_route("/drivers/availability", rideops.update_driver_availability, methods=["POST"])
    app.add_api_route("/drivers/me/ride-request", rideops.get_driver_ride_request, methods=["GET"])
    app.add_api_route("/drivers/me/earnings", rideops.get_driver_earnings, methods=["GET"])
