# Ride Lifecycle Diagram

Ride state transitions are enforced by `ALLOWED_RIDE_TRANSITIONS` in
`RideOpsServer/app/main.py`. Terminal states are `completed` and `cancelled`.

## State Diagram

```mermaid
stateDiagram-v2
    [*] --> matching: rider creates immediate ride
    [*] --> scheduled: rider schedules future ride

    scheduled --> matching: scheduled pickup time is due
    scheduled --> cancelled: rider cancels

    matching --> pending_driver: system assigns nearest driver
    matching --> no_drivers_available: no available drivers
    matching --> cancelled: rider cancels

    no_drivers_available --> matching: driver comes online or rider retries
    no_drivers_available --> pending_driver: system assigns driver
    no_drivers_available --> cancelled: rider cancels

    pending_driver --> accepted: assigned driver accepts
    pending_driver --> matching: assigned driver rejects, times out, or goes offline
    pending_driver --> no_drivers_available: rematch finds no drivers
    pending_driver --> cancelled: rider cancels

    accepted --> arrived: driver arrives
    accepted --> cancelled: rider or driver cancels

    arrived --> in_progress: driver starts trip
    arrived --> cancelled: rider or driver cancels

    in_progress --> completed: driver completes trip

    completed --> [*]
    cancelled --> [*]
```

## Transition Table

| From | To | Actor | Trigger |
| --- | --- | --- | --- |
| `scheduled` | `matching` | system | scheduled pickup time is due |
| `scheduled` | `cancelled` | rider | `POST /rides/{ride_id}/cancel` |
| `matching` | `pending_driver` | system | nearest available driver assigned |
| `matching` | `no_drivers_available` | system | no available driver candidates |
| `matching` | `cancelled` | rider | `POST /rides/{ride_id}/cancel` |
| `no_drivers_available` | `matching` | system or driver flow | waiting ride is retried |
| `no_drivers_available` | `pending_driver` | system | driver becomes available and is assigned |
| `no_drivers_available` | `cancelled` | rider | `POST /rides/{ride_id}/cancel` |
| `pending_driver` | `accepted` | driver | `POST /rides/{ride_id}/accept` |
| `pending_driver` | `matching` | driver or system | `POST /rides/{ride_id}/reject`, dispatch timeout, or driver goes offline |
| `pending_driver` | `no_drivers_available` | system | rematching finds no candidates |
| `pending_driver` | `cancelled` | rider | `POST /rides/{ride_id}/cancel` |
| `accepted` | `arrived` | driver | `POST /rides/{ride_id}/status` with `arrived` |
| `accepted` | `cancelled` | rider or driver | `POST /rides/{ride_id}/cancel` |
| `arrived` | `in_progress` | driver | `POST /rides/{ride_id}/status` with `in_progress` |
| `arrived` | `cancelled` | rider or driver | `POST /rides/{ride_id}/cancel` |
| `in_progress` | `completed` | driver | `POST /rides/{ride_id}/status` with `completed` |

## Main Flow

```mermaid
sequenceDiagram
    participant Rider
    participant API
    participant Driver
    participant Payment as Wallet Payment
    participant Realtime

    Rider->>API: POST /rides
    API->>Payment: authorize wallet fare
    API->>API: status matching
    API->>Driver: ride_request
    API->>Realtime: ride_update pending_driver

    Driver->>API: POST /rides/{id}/accept
    API->>API: status accepted
    API->>Realtime: ride_update accepted

    Driver->>API: POST /rides/{id}/status arrived
    API->>Realtime: ride_update arrived

    Driver->>API: POST /rides/{id}/status in_progress
    API->>Realtime: ride_update in_progress

    Driver->>API: POST /rides/{id}/status completed
    API->>Payment: capture payment
    API->>API: release driver
    API->>Realtime: ride_update completed
```

