from fastapi import FastAPI

from ._runtime import rideops_runtime


def include_routes(app: FastAPI) -> None:
    rideops = rideops_runtime()
    app.add_api_route("/search/locations", rideops.search_locations, methods=["GET"])
    app.add_api_route("/geofences", rideops.get_geofences, methods=["GET"])
    app.add_api_route("/geofences/evaluate", rideops.evaluate_geofences, methods=["POST"])
    app.add_api_route("/routes/snap-to-road", rideops.snap_to_road, methods=["POST"])
    app.add_api_route("/routes/estimate", rideops.estimate_route, methods=["POST"])
    app.add_api_route("/routes/alternatives", rideops.route_alternatives, methods=["POST"])
    app.add_api_route("/routes/distance-matrix", rideops.distance_matrix, methods=["POST"])
    app.add_api_route("/traffic/simulate", rideops.simulate_traffic, methods=["POST"])
