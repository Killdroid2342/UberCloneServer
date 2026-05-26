from fastapi import FastAPI

from ._runtime import rideops_runtime


def include_routes(app: FastAPI) -> None:
    rideops = rideops_runtime()
    app.add_api_route("/riders/signup", rideops.rider_signup, methods=["POST"])
    app.add_api_route("/riders/login", rideops.rider_login, methods=["POST"])
    app.add_api_route("/drivers/signup", rideops.driver_signup, methods=["POST"])
    app.add_api_route("/drivers/login", rideops.driver_login, methods=["POST"])
    app.add_api_route("/admin/login", rideops.admin_login, methods=["POST"])
    app.add_api_route("/auth/refresh", rideops.refresh_auth_session, methods=["POST"])
    app.add_api_route("/auth/logout", rideops.logout_auth_session, methods=["POST"])
    app.add_api_route("/auth/me", rideops.get_me, methods=["GET"])
