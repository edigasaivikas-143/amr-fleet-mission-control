"""
Edge-AI AMR Fleet Mission Control Server (SIH 2026 Edition)
Part of Edge-AI Distributed Fleet Coordination for Autonomous Mobile Robots

Serves the interactive glass-pane dashboard on http://localhost:8000
Provides REST endpoints and WebSocket telemetry for multi-agent coordination,
hardware fault self-healing, human safety interlocks, and dynamic obstacle rerouting.
"""

import os
import sys
import json
import asyncio

# Ensure safe console output on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from typing import Set, Dict, Any, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import uvicorn

from warehouse_map import WarehouseMap
from inventory import InventoryManager

# Request Models
class BulkInjectRequest(BaseModel):
    count: int

class ObstacleRequest(BaseModel):
    x: int
    y: int

class BreakdownRequest(BaseModel):
    robot_id: str
    reason: Optional[str] = "Actuator Drive Stall"

app = FastAPI(
    title="Edge-AI AMR Fleet Mission Control",
    description="Distributed Multi-Agent Coordination & Self-Healing Fault Recovery for SIH 2026",
    version="2.0.0"
)

# Core Simulation Engines
warehouse = WarehouseMap(width=80, height=50)
inventory = InventoryManager(warehouse=warehouse, floors_per_rack=5)

active_connections: Set[WebSocket] = set()
DASHBOARD_FILE = os.path.join(os.path.dirname(__file__), "dashboard.html")

# Simulated Robot Fleet State
FLEET_STATE = {
    f"AMR-0{i+1}": {
        "id": f"AMR-0{i+1}",
        "status": "HEALTHY",
        "battery": 100.0,
        "is_faulted": False,
        "fault_reason": ""
    }
    for i in range(8)
}

# Pre-configured SIH Scenarios Registry
SCENARIOS = [
    {
        "id": 0,
        "name": "Normal Autonomous Operations",
        "description": "Continuous distributed multi-agent task dispatch, 3D rack storage, and opportunistic fast-charging."
    },
    {
        "id": 1,
        "name": "Narrow Aisle Priority Yielding",
        "description": "Head-on conflict between Urgent Cold-Chain Pharma AMR and Standard Cargo AMR in a 1-cell aisle. Low-priority robot steps aside into pocket."
    },
    {
        "id": 2,
        "name": "Human Safety Brake & Dynamic Detour",
        "description": "Human inspector detected in aisle. AMR engages safety brake interlock, yields, and executes dynamic A* detour around the hazard."
    },
    {
        "id": 3,
        "name": "Hardware Fault & Autonomous Task Rescue",
        "description": "Mid-transit mechanical failure. Surrogate AMR automatically dispatches to take over stranded cargo and complete delivery."
    },
    {
        "id": 4,
        "name": "Low-Battery Preemption & Limp Mode",
        "description": "Battery critical trigger (<15%). AMR disengages active task, enters limp mode, and reserves nearest fast-charging bay."
    },
    {
        "id": 5,
        "name": "Rush-Hour Peak Surge Stress Test",
        "description": "High-throughput burst: 40 simultaneous parcel orders across all docks and bays to verify 100% collision-free routing."
    }
]


async def broadcast_ws(message: Dict[str, Any]):
    """Broadcast real-time telemetry to all connected dashboard clients."""
    if not active_connections:
        return
    dead_conns = set()
    for ws in active_connections:
        try:
            await ws.send_json(message)
        except Exception:
            dead_conns.add(ws)
    for dc in dead_conns:
        active_connections.discard(dc)


@app.get("/", response_class=HTMLResponse)
async def get_index():
    """Serve the interactive glassmorphic Mission Control dashboard."""
    if os.path.exists(DASHBOARD_FILE):
        with open(DASHBOARD_FILE, "r", encoding="utf-8") as f:
            return f.read()
    return HTMLResponse("<h1>Error: dashboard.html not found</h1>", status_code=404)


@app.get("/api/map")
async def get_map():
    """Returns the 80x50 industrial warehouse topology, stations, and rack pods."""
    return warehouse.to_dict()


@app.get("/api/inventory")
async def get_inventory():
    """Returns real-time 3D multi-tier rack storage metrics."""
    return inventory.get_summary()


@app.post("/api/inventory/inject")
async def inject_inventory(req: BulkInjectRequest):
    """Directly allocates N parcels into 3D racks categorized by SKU bands."""
    stored = inventory.bulk_store_parcels(req.count)
    summary = {
        "stored": stored,
        "total_stored": inventory.total_stored_parcels,
        "capacity": inventory.total_capacity,
        "occupancy_rate": round(inventory.occupancy_rate, 2)
    }
    await broadcast_ws({"type": "INVENTORY_UPDATE", "data": summary})
    return summary


@app.get("/api/scenarios")
async def get_scenarios():
    """Returns available SIH interactive test scenarios."""
    return SCENARIOS


@app.post("/api/scenario/{scenario_id}")
async def trigger_scenario(scenario_id: int):
    """Triggers an automated demonstration scenario on connected fleets."""
    if scenario_id < 0 or scenario_id >= len(SCENARIOS):
        raise HTTPException(status_code=400, detail="Invalid scenario ID")
    scen = SCENARIOS[scenario_id]
    await broadcast_ws({"type": "SCENARIO_TRIGGER", "scenario_id": scenario_id, "name": scen["name"]})
    return {"status": "SUCCESS", "scenario": scen}


@app.get("/api/fleet/status")
async def get_fleet_status():
    """Returns telemetry of all AMRs (health, battery, status)."""
    return FLEET_STATE


@app.post("/api/fleet/breakdown")
async def report_breakdown(req: BreakdownRequest):
    """Simulates a hardware failure on a specific AMR and triggers autonomous rescue."""
    if req.robot_id not in FLEET_STATE:
        raise HTTPException(status_code=404, detail="Robot not found")
    bot = FLEET_STATE[req.robot_id]
    bot["status"] = "FAULTED"
    bot["is_faulted"] = True
    bot["fault_reason"] = req.reason
    await broadcast_ws({"type": "ROBOT_FAULT", "robot_id": req.robot_id, "reason": req.reason})
    return {"status": "FAULT_TRIGGERED", "robot": bot}


@app.post("/api/fleet/repair/{robot_id}")
async def repair_robot(robot_id: str):
    """Restores a faulted AMR back to active healthy status."""
    if robot_id not in FLEET_STATE:
        raise HTTPException(status_code=404, detail="Robot not found")
    bot = FLEET_STATE[robot_id]
    bot["status"] = "HEALTHY"
    bot["is_faulted"] = False
    bot["fault_reason"] = ""
    await broadcast_ws({"type": "ROBOT_REPAIRED", "robot_id": robot_id})
    return {"status": "REPAIRED", "robot": bot}


@app.get("/api/obstacles")
async def get_obstacles():
    """Returns list of active dynamic hazards."""
    return list(warehouse.dynamic_obstacles)


@app.post("/api/obstacles/add")
async def add_obstacle(req: ObstacleRequest):
    """Injects a dynamic hazard / blocked aisle."""
    warehouse.add_dynamic_obstacle(req.x, req.y)
    await broadcast_ws({"type": "OBSTACLE_ADDED", "x": req.x, "y": req.y})
    return {"status": "ADDED", "x": req.x, "y": req.y}


@app.post("/api/obstacles/clear")
async def clear_obstacles():
    """Clears all active dynamic obstacles."""
    warehouse.clear_dynamic_obstacles()
    await broadcast_ws({"type": "OBSTACLES_CLEARED"})
    return {"status": "CLEARED"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Real-time bi-directional telemetry stream."""
    await websocket.accept()
    active_connections.add(websocket)
    try:
        await websocket.send_json({
            "type": "INIT",
            "map": warehouse.to_dict(),
            "inventory": inventory.get_summary(),
            "fleet": FLEET_STATE,
            "scenarios": SCENARIOS
        })
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                # Handle client actions if needed
                if msg.get("type") == "PING":
                    await websocket.send_json({"type": "PONG"})
            except Exception:
                pass
    except WebSocketDisconnect:
        active_connections.discard(websocket)


if __name__ == "__main__":
    print("================================================================")
    print(" [OK] Edge-AI AMR Fleet Mission Control Server (SIH 2026)")
    print(" [URL] Live Dashboard: http://localhost:8000")
    print(" [WS]  WebSocket Telemetry: ws://localhost:8000/ws")
    print("================================================================")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
