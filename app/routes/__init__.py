from fastapi import FastAPI

from . import admin, auth, drivers, notifications, persistence, realtime, ride_lifecycle, routing, system, wallet


def include_feature_routes(app: FastAPI) -> None:
    if getattr(app.state, "feature_routes_included", False):
        return

    system.include_routes(app)
    persistence.include_routes(app)
    auth.include_routes(app)
    wallet.include_routes(app)
    notifications.include_routes(app)
    admin.include_routes(app)
    drivers.include_routes(app)
    routing.include_routes(app)
    ride_lifecycle.include_routes(app)
    realtime.include_routes(app)
    app.state.feature_routes_included = True
