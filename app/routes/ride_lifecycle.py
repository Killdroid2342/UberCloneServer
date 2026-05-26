from fastapi import FastAPI

from ._runtime import rideops_runtime


def include_routes(app: FastAPI) -> None:
    rideops = rideops_runtime()
    app.add_api_route("/rides", rideops.request_ride, methods=["POST"])
    app.add_api_route("/rides/{ride_id}/rider-location", rideops.update_rider_location, methods=["POST"])
    app.add_api_route("/rides/history", rideops.get_ride_history, methods=["GET"])
    app.add_api_route("/rides/{ride_id}", rideops.get_ride, methods=["GET"])
    app.add_api_route("/rides/{ride_id}/receipt", rideops.get_ride_receipt, methods=["GET"])
    app.add_api_route("/rides/{ride_id}/receipt/email", rideops.email_ride_receipt, methods=["POST"])
    app.add_api_route("/rides/{ride_id}/share", rideops.create_ride_share, methods=["POST"])
    app.add_api_route("/shares/{share_token}", rideops.get_shared_ride, methods=["GET"])
    app.add_api_route("/rides/{ride_id}/ratings", rideops.rate_ride, methods=["POST"])
    app.add_api_route("/rides/{ride_id}/issues", rideops.report_ride_issue, methods=["POST"])
    app.add_api_route("/rides/{ride_id}/accept", rideops.accept_ride, methods=["POST"])
    app.add_api_route("/rides/{ride_id}/reject", rideops.reject_ride, methods=["POST"])
    app.add_api_route("/rides/{ride_id}/status", rideops.update_ride_status, methods=["POST"])
    app.add_api_route("/rides/{ride_id}/refund", rideops.simulate_ride_refund, methods=["POST"])
    app.add_api_route("/rides/{ride_id}/cancel", rideops.cancel_ride, methods=["POST"])
