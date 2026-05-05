from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from uuid import uuid4

app = FastAPI(title="MyUber API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

rides = {}
connections: list[WebSocket] = []


class Location(BaseModel):
    lat: float
    lng: float


class RideRequest(BaseModel):
    rider_id: str
    pickup: Location
    destination: Location


@app.get("/")
def root():
    return {"status": "MyUber API running"}


@app.post("/rides")
def request_ride(payload: RideRequest):
    ride_id = str(uuid4())

    ride = {
        "id": ride_id,
        "rider_id": payload.rider_id,
        "pickup": payload.pickup.model_dump(),
        "destination": payload.destination.model_dump(),
        "status": "requested",
        "driver_location": None,
    }

    rides[ride_id] = ride
    return ride


@app.get("/rides/{ride_id}")
def get_ride(ride_id: str):
    return rides.get(ride_id, {"error": "Ride not found"})


@app.websocket("/ws/rides/{ride_id}")
async def ride_socket(websocket: WebSocket, ride_id: str):
    await websocket.accept()
    connections.append(websocket)

    try:
        while True:
            data = await websocket.receive_json()

            if data["type"] == "driver_location":
                rides[ride_id]["driver_location"] = data["location"]

                message = {
                    "type": "ride_update",
                    "ride": rides[ride_id],
                }

                for conn in connections:
                    await conn.send_json(message)

    except WebSocketDisconnect:
        connections.remove(websocket)