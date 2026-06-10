from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bonsai.models import PortRepairPlan, PortRepairServiceChange
from bonsai.ports import port_owner_payload

PORT_REPAIR_SCHEMA = "bonsai.port-repair.v1"


def _service_payload(service: PortRepairServiceChange) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": service.name,
        "port_env": service.port_env,
        "old_port": service.old_port,
        "new_port": service.new_port,
    }
    if service.owners:
        payload["owners"] = [port_owner_payload(owner) for owner in service.owners]
    return payload


def port_repair_payload(plan: PortRepairPlan, workspace_root: Path) -> dict[str, Any]:
    return {
        "schema": PORT_REPAIR_SCHEMA,
        "workspace": {"root": str(workspace_root)},
        "repairs": [
            {
                "branch": item.branch,
                "slug": item.slug,
                "current_slot": item.current_slot,
                "proposed_slot": item.proposed_slot,
                "services": [
                    _service_payload(service)
                    for service in item.services
                ],
            }
            for item in plan.items
        ],
    }


def render_port_repair_json(plan: PortRepairPlan, workspace_root: Path) -> str:
    return json.dumps(port_repair_payload(plan, workspace_root), indent=2, sort_keys=True) + "\n"
