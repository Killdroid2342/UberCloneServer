from fastapi import FastAPI

from ._runtime import rideops_runtime


def include_routes(app: FastAPI) -> None:
    rideops = rideops_runtime()
    app.add_api_route("/admin/dashboard", rideops.get_admin_dashboard, methods=["GET"])
    app.add_api_route("/admin/fraud-events", rideops.get_admin_fraud_events, methods=["GET"])
    app.add_api_route("/admin/abuse-events", rideops.get_admin_abuse_events, methods=["GET"])
    app.add_api_route("/admin/audit-logs", rideops.get_admin_audit_logs, methods=["GET"])
    app.add_api_route("/admin/observability", rideops.get_admin_observability, methods=["GET"])
    app.add_api_route("/admin/events", rideops.get_admin_events, methods=["GET"])
    app.add_api_route("/admin/platform", rideops.get_admin_platform, methods=["GET"])
    app.add_api_route("/admin/platform/drain", rideops.update_admin_platform_drain, methods=["POST"])
    app.add_api_route("/admin/access-control", rideops.get_admin_access_control, methods=["GET"])
    app.add_api_route("/admin/users/{role}/{user_id}/status", rideops.update_admin_user_status, methods=["PATCH"])
    app.add_api_route("/admin/drivers/{driver_id}/force-offline", rideops.force_admin_driver_offline, methods=["POST"])
    app.add_api_route("/admin/drivers/{driver_id}/documents", rideops.review_admin_driver_documents, methods=["PATCH"])
