from fastapi import FastAPI

from ._runtime import rideops_runtime


def include_routes(app: FastAPI) -> None:
    rideops = rideops_runtime()
    app.add_api_route("/realtime/{target}/{target_id}/events", rideops.get_realtime_events, methods=["GET"])
    app.add_api_websocket_route("/ws/rides/{ride_id}", rideops.ride_socket)
    app.add_api_websocket_route("/ws/shares/{share_token}", rideops.share_socket)
    app.add_api_websocket_route("/ws/drivers/{driver_id}", rideops.driver_socket)
