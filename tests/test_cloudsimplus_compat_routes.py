from __future__ import annotations

import json
import threading
import urllib.request
from contextlib import contextmanager
from typing import Iterator

from tianjun.application.bootstrap import build_control_plane
from tianjun.chat import ChatRuntime
from tianjun.interfaces.http.server import build_http_server
from tianjun.llm import LLMSettings


@contextmanager
def running_server() -> Iterator[str]:
    control_plane = build_control_plane()
    chat = ChatRuntime.with_llm_settings(control_plane, LLMSettings(offline=True))
    server = build_http_server(control_plane, "127.0.0.1", 0, chat_runtime=chat)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def post_json(base_url: str, path: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def cloudsim_node_payload() -> dict:
    return {
        "node_id": "dci-dc1-beijing-vm-0",
        "region": "dc1",
        "location": "beijing",
        "service_region": "east",
        "labels": ["cloudsim", "cpu", "dc1", "latency-sensitive"],
        "capacity": {"cpu": 8, "memory": 32, "gpu": 0, "storage": 200},
        "cost_per_tick": 1.1,
        "base_reliability": 0.993,
        "performance_factors": {"inference": 1.1, "batch_cpu": 1.1, "analytics": 1.1},
        "network_paths": {
            "dc1": {
                "latency_ms": 1.0,
                "jitter_ms": 0.2,
                "bandwidth_mbps": 25000,
                "bandwidth_jitter_mbps": 250,
                "packet_loss": 0.001,
                "path_reliability": 0.999,
            }
        },
    }


def cloudsim_task_payload(task_id: str = "cloudsim-task-1") -> dict:
    return {
        "task_id": task_id,
        "task_type": "inference",
        "demand": {"cpu": 1, "memory": 2, "gpu": 0, "storage": 1},
        "estimated_duration": 2,
        "priority": 8,
        "budget": 20,
        "deadline": 20,
        "data_region": "dc1",
        "source_region": "dc1",
        "input_size_gb": 0.5,
        "max_latency_ms": 25,
        "min_bandwidth_mbps": 100,
        "network_sensitivity": 0.8,
        "preferred_labels": ["cloudsim"],
    }


def test_cloudsimplus_schedule_rejection_shape_without_feasible_node() -> None:
    with running_server() as base_url:
        result = post_json(base_url, "/schedule/commit", cloudsim_task_payload("rejected-task"))

    assert result["status"] == "rejected"
    assert result["task_id"] == "rejected-task"
    assert result["node_id"] == ""
    assert result["total_score"] == 0.0
    assert result["preview_decision"] is None
    assert result["lease"] is None
    assert result["reason"]


def test_cloudsimplus_compat_routes_accept_direct_schedule_and_result_flow() -> None:
    with running_server() as base_url:
        topology = post_json(
            base_url,
            "/topology/register",
            {
                "topology_id": "cloudsim-test-topology",
                "topology_nodes": ["DC1"],
                "topology_edges": [],
                "compute_attachments": {"dci-dc1-beijing-vm-0": "DC1"},
            },
        )
        registered = post_json(base_url, "/nodes/register", cloudsim_node_payload())
        heartbeat = post_json(
            base_url,
            "/nodes/heartbeat",
            {
                "node_id": "dci-dc1-beijing-vm-0",
                "health_score": 0.98,
                "online": True,
                "region": "dc1",
                "location": "beijing",
                "service_region": "east",
                "labels": ["cloudsim", "cpu", "dc1", "latency-sensitive"],
                "network_paths": cloudsim_node_payload()["network_paths"],
            },
        )
        preview = post_json(base_url, "/schedule/preview", cloudsim_task_payload())
        committed = post_json(base_url, "/schedule/commit", cloudsim_task_payload())
        result = post_json(
            base_url,
            "/task-runs/result",
            {
                "node_id": committed["node_id"],
                "task_id": committed["task_id"],
                "success": True,
                "duration_seconds": 2.5,
                "stdout": "CloudSimPlus task completed.",
                "stderr": "",
                "returncode": 0,
                "cost": 2.75,
            },
        )

    assert topology["topology_id"] == "cloudsim-test-topology"
    assert registered["node_id"] == "dci-dc1-beijing-vm-0"
    assert heartbeat["node_id"] == "dci-dc1-beijing-vm-0"
    assert preview["status"] == "preview"
    assert preview["node_id"] == "dci-dc1-beijing-vm-0"
    assert committed["status"] == "scheduled"
    assert committed["node_id"] == "dci-dc1-beijing-vm-0"
    assert committed["lease"]["task_id"] == "cloudsim-task-1"
    assert result["task_id"] == "cloudsim-task-1"
    assert result["success"] is True
