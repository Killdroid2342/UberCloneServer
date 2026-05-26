from fastapi import FastAPI

from ._runtime import rideops_runtime


def include_routes(app: FastAPI) -> None:
    rideops = rideops_runtime()
    app.add_api_route("/notifications", rideops.get_notifications, methods=["GET"])
    app.add_api_route("/notifications/push-config", rideops.get_push_config, methods=["GET"])
    app.add_api_route("/notifications/push-subscriptions", rideops.save_push_subscription, methods=["POST"])
    app.add_api_route(
        "/notifications/push-subscriptions/{subscription_id}",
        rideops.delete_push_subscription,
        methods=["DELETE"],
    )
    app.add_api_route("/notifications/{notification_id}/read", rideops.mark_notification_read, methods=["POST"])
    app.add_api_route("/notifications/read-all", rideops.mark_all_notifications_read, methods=["POST"])
