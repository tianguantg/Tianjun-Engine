from __future__ import annotations

from math import ceil
from statistics import mean
from typing import Any, Iterable

from ..ml.runtime import TrainedModelRuntime, get_default_model_runtime
from ..domain import METRIC_KEYS, Node, PhysicalTopology, PolicyState, SchedulingDecision, Task, clamp, normalize_weights


class ClosedLoopAdaptiveScheduler:
    def __init__(
        self,
        policy_state: PolicyState,
        model_runtime: TrainedModelRuntime | None = None,
    ) -> None:
        self.policy_state = policy_state
        self.model_runtime = model_runtime or get_default_model_runtime()
        self._deterministic_latency_state: dict[str, float] = {}
        self.physical_topology: PhysicalTopology | None = None

    def set_physical_topology(self, topology: PhysicalTopology | None) -> None:
        self.physical_topology = topology

    def select_node(
        self,
        task: Task,
        nodes: Iterable[Node],
        current_tick: int,
        *,
        topology_nodes: Iterable[Node] | None = None,
    ) -> SchedulingDecision | None:
        candidate_pool = list(nodes)
        neighbor_pool = list(topology_nodes) if topology_nodes is not None else candidate_pool
        raw_metrics: dict[str, dict[str, float]] = {}
        candidate_details: dict[str, dict[str, Any]] = {}
        candidates: list[Node] = []
        for node in candidate_pool:
            if not node.can_host_now(task):
                continue

            network_snapshot = self._network_snapshot(task, node, neighbor_pool)
            if not self._network_feasible(task, network_snapshot):
                continue

            transfer_ticks = float(network_snapshot["transfer_ticks"])
            predicted_duration = node.predict_duration(task) + int(transfer_ticks)
            queue_snapshot = self._queue_snapshot(node, task, current_tick, predicted_duration)
            predicted_start_tick = int(queue_snapshot["predicted_start_tick"])
            predicted_finish_tick = predicted_start_tick + predicted_duration
            predicted_cost = max(1.0, predicted_duration - transfer_ticks) * node.cost_per_tick
            candidates.append(node)

            raw_metrics[node.node_id] = {
                "performance": self._performance_raw(task, predicted_duration, predicted_finish_tick),
                "completion": self._completion_raw(task, current_tick, predicted_finish_tick),
                "cost": self._cost_raw(task, predicted_cost),
                "reliability": max(
                    0.0,
                    (node.reliability_score or 0.0)
                    * node.health_score
                    * float(network_snapshot["delivery_probability"])
                    * float(network_snapshot["deterministic_confidence"]),
                ),
                "balance": self._balance_raw(node, task, queue_snapshot),
                "fragmentation": node.fragmentation_after(task.demand),
                "locality": node.locality_score(task),
                "network": self._network_raw(task, network_snapshot),
                "security": self._security_raw(task, node, network_snapshot),
            }
            candidate_details[node.node_id] = {
                "predicted_duration": float(predicted_duration),
                "predicted_start_tick": float(predicted_start_tick),
                "predicted_finish_tick": float(predicted_finish_tick),
                "predicted_cost": float(predicted_cost),
                "network_snapshot": network_snapshot,
                "queue_snapshot": queue_snapshot,
            }

        if task.budget is not None:
            within_budget = [
                node
                for node in candidates
                if candidate_details[node.node_id]["predicted_cost"] <= float(task.budget)
            ]
            if within_budget:
                allowed_ids = {node.node_id for node in within_budget}
                candidates = within_budget
                raw_metrics = {
                    node_id: metrics
                    for node_id, metrics in raw_metrics.items()
                    if node_id in allowed_ids
                }
                candidate_details = {
                    node_id: details
                    for node_id, details in candidate_details.items()
                    if node_id in allowed_ids
                }

        if not candidates:
            return None

        metric_scores = self._normalize_metric_matrix(raw_metrics)
        weights = self._derive_task_weights(task, current_tick)

        def adjusted_total(node: Node) -> float:
            score = sum(metric_scores[node.node_id][key] * weights[key] for key in METRIC_KEYS)
            # Avoid burning scarce GPU nodes for CPU-only jobs when a CPU-capable node exists.
            # This is a soft preference, not a hard constraint: if a region only has a GPU node,
            # the task can still run there.
            if task.demand.gpu <= 0 and node.capacity.gpu > 0:
                score -= 0.75
            return score

        best_node = max(
            candidates,
            key=lambda node: (
                adjusted_total(node),
                metric_scores[node.node_id]["network"],
                metric_scores[node.node_id]["performance"],
                metric_scores[node.node_id]["reliability"],
            ),
        )
        total_score = adjusted_total(best_node)
        detail = candidate_details[best_node.node_id]
        decision_snapshot = dict(detail["network_snapshot"])
        decision_snapshot["queue"] = detail["queue_snapshot"]
        decision_snapshot["adaptive_scoring_formula"] = (
            "score=sum(w_k*s_k); completion penalizes predicted finish time; "
            "balance penalizes dominant utilization, queue depth, and queued work."
        )
        explanation = self._build_explanation(
            task,
            best_node,
            metric_scores[best_node.node_id],
            weights,
            decision_snapshot,
        )
        return SchedulingDecision(
            task_id=task.task_id,
            node_id=best_node.node_id,
            total_score=total_score,
            metric_scores=metric_scores[best_node.node_id],
            raw_metrics=raw_metrics[best_node.node_id],
            weights=weights,
            predicted_start_tick=int(detail["predicted_start_tick"]),
            predicted_finish_tick=int(detail["predicted_finish_tick"]),
            predicted_cost=detail["predicted_cost"],
            explanation=explanation,
            network_snapshot=decision_snapshot,
        )

    def _performance_raw(self, task: Task, predicted_duration: int, predicted_finish_tick: int) -> float:
        delay_penalty = 1.0
        deadline_tick = task.effective_deadline_tick()
        if deadline_tick is not None and predicted_finish_tick > deadline_tick:
            lateness = predicted_finish_tick - deadline_tick
            delay_penalty += lateness * 2.5
        return 1.0 / max(1.0, predicted_duration * delay_penalty)

    def _completion_raw(self, task: Task, current_tick: int, predicted_finish_tick: int) -> float:
        completion_time = max(1.0, float(predicted_finish_tick - current_tick))
        deadline_penalty = 1.0
        deadline_tick = task.effective_deadline_tick()
        if deadline_tick is not None and predicted_finish_tick > deadline_tick:
            lateness_ratio = (predicted_finish_tick - deadline_tick) / max(1.0, float(task.estimated_duration))
            deadline_penalty += lateness_ratio * 3.0
        return 1.0 / max(1.0, completion_time * deadline_penalty)

    def _cost_raw(self, task: Task, predicted_cost: float) -> float:
        budget_penalty = 1.0
        if task.budget is not None and predicted_cost > task.budget:
            budget_penalty += ((predicted_cost - task.budget) / max(task.budget, 1.0)) * 3.0
        return 1.0 / max(0.1, predicted_cost * budget_penalty)

    def _balance_raw(self, node: Node, task: Task, queue_snapshot: dict[str, float]) -> float:
        resource_pressure = node.dominant_utilization_after(task.demand)
        queue_depth_pressure = float(queue_snapshot["queue_depth_pressure"])
        queued_work_pressure = float(queue_snapshot["queued_work_pressure"])
        pressure = (
            (resource_pressure * 0.45)
            + (queue_depth_pressure * 0.35)
            + (queued_work_pressure * 0.20)
        )
        return clamp(1.0 - pressure)

    def _queue_snapshot(
        self,
        node: Node,
        task: Task,
        current_tick: int,
        predicted_duration: int,
    ) -> dict[str, float]:
        running = list(node.running_tasks.values())
        remaining_ticks = [
            max(0.0, float(running_task.finish_tick - current_tick))
            for running_task in running
        ]
        queued_work_ticks = sum(remaining_ticks)
        running_count = len(running)
        effective_slots = max(
            1.0,
            min(
                8.0,
                node.capacity.cpu / max(1.0, task.demand.cpu * 2.0),
            ),
        )
        queue_wait_ticks = queued_work_ticks / effective_slots
        queue_depth_pressure = clamp(running_count / (effective_slots * 2.0))
        queued_work_pressure = clamp(queued_work_ticks / max(1.0, effective_slots * max(1, predicted_duration) * 2.0))
        predicted_start_tick = current_tick + int(ceil(queue_wait_ticks))
        return {
            "running_count": float(running_count),
            "effective_slots": float(effective_slots),
            "queued_work_ticks": float(queued_work_ticks),
            "queue_wait_ticks": float(queue_wait_ticks),
            "queue_depth_pressure": queue_depth_pressure,
            "queued_work_pressure": queued_work_pressure,
            "predicted_start_tick": float(predicted_start_tick),
            "predicted_finish_tick": float(predicted_start_tick + predicted_duration),
        }

    def _derive_task_weights(self, task: Task, current_tick: int) -> dict[str, float]:
        weights = self.policy_state.current_weights()
        urgency = task.urgency_score(current_tick)

        weights["performance"] += 0.18 * urgency
        weights["completion"] += 0.14 + (0.18 * urgency)
        weights["reliability"] += 0.10 * urgency
        weights["balance"] += 0.08
        for metric, boost in task.intent_weights.items():
            if metric in weights:
                weights[metric] += max(0.0, float(boost))
        if task.deadline is not None:
            weights["completion"] += 0.10
        if task.budget is not None:
            weights["cost"] += 0.24
        if task.data_region is not None or task.preferred_labels:
            weights["locality"] += 0.06
        if task.demand.gpu > 0:
            weights["fragmentation"] += 0.05
        if task.task_type in {"batch_cpu", "analytics"}:
            weights["completion"] += 0.08
            weights["balance"] += 0.06
        if (
            task.network_source() is not None
            or task.max_latency_ms is not None
            or task.min_bandwidth_mbps is not None
        ):
            weights["network"] += 0.16
        if task.network_sensitivity >= 0.75 or task.task_type in {"streaming", "inference"}:
            weights["network"] += 0.20
            weights["performance"] += 0.05
        elif task.network_sensitivity >= 0.5:
            weights["network"] += 0.10
        if task.priority <= 4:
            weights["cost"] += 0.14
            weights["performance"] -= 0.12
            weights["completion"] -= 0.06
            weights["reliability"] -= 0.04
        if task.security_level == "high":
            weights["security"] += 0.22
            weights["reliability"] += 0.08
            weights["locality"] += 0.06
        elif task.security_level == "medium":
            weights["security"] += 0.10
        if task.allowed_regions or task.forbidden_nodes or task.require_encrypted_transport:
            weights["security"] += 0.08
        return normalize_weights(weights)

    def _normalize_metric_matrix(
        self,
        metric_matrix: dict[str, dict[str, float]],
    ) -> dict[str, dict[str, float]]:
        normalized: dict[str, dict[str, float]] = {
            node_id: {} for node_id in metric_matrix
        }
        for metric in METRIC_KEYS:
            values = [metrics[metric] for metrics in metric_matrix.values()]
            minimum = min(values)
            maximum = max(values)
            span = maximum - minimum
            for node_id, metrics in metric_matrix.items():
                if span <= 1e-9:
                    normalized[node_id][metric] = 1.0
                else:
                    normalized[node_id][metric] = (metrics[metric] - minimum) / span
        return normalized

    def _build_explanation(
        self,
        task: Task,
        node: Node,
        metric_scores: dict[str, float],
        weights: dict[str, float],
        network_snapshot: dict[str, Any],
    ) -> str:
        labels = {
            "performance": "性能",
            "cost": "成本",
            "reliability": "可靠性",
            "balance": "负载均衡",
            "fragmentation": "资源碎片",
            "locality": "局部性",
            "network": "网络稳定性",
            "security": "安全",
        }
        contributions = sorted(
            (
                (metric, metric_scores[metric] * weights[metric])
                for metric in METRIC_KEYS
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        top_metrics = "、".join(labels.get(metric, metric) for metric, _ in contributions[:3])
        stable_latency = float(network_snapshot.get("stable_latency_ms", 0.0))
        fusion_score = float(network_snapshot.get("feature_fusion_score", 0.0))
        confidence = float(network_snapshot.get("deterministic_confidence", 0.0))
        return (
            f"在四特征融合策略下，{node.node_id} 为任务 {task.task_id} 提供了"
            f"{top_metrics} 等维度的更优组合；预测稳定时延约 {stable_latency:.1f} ms，"
            f"融合评分 {fusion_score:.3f}，确定化置信度 {confidence:.3f}，因此被选为目标节点。"
        )

    def _network_snapshot(self, task: Task, node: Node, topology_nodes: list[Node]) -> dict[str, Any]:
        profile = node.path_profile_for(task.network_source())
        latency_history = profile.synthesized_latency_history_ms()
        latency_ewma = self._ewma(latency_history)
        latency_trend = latency_history[-1] - latency_history[0]

        node_load = node.dominant_utilization_after(task.demand)
        bandwidth_utilization = profile.bandwidth_utilization_estimate()
        node_by_id = {candidate.node_id: candidate for candidate in topology_nodes}
        neighbor_distances = (
            self.physical_topology.compute_neighbor_distances(node.node_id, set(node_by_id))
            if self.physical_topology is not None
            else {}
        )
        neighbor_ids = sorted(neighbor_distances, key=lambda neighbor_id: (neighbor_distances[neighbor_id], neighbor_id))
        selected_anchor = (
            self.physical_topology.compute_attachments.get(node.node_id)
            if self.physical_topology is not None
            else None
        )
        direct_neighbor_ids = [
            neighbor_id
            for neighbor_id in neighbor_ids
            if self.physical_topology is not None
            and self.physical_topology.compute_attachments.get(neighbor_id) == selected_anchor
        ]
        neighbor_observations = [
            (
                node_by_id[neighbor_id],
                node_by_id[neighbor_id].path_profile_for(task.network_source()),
                neighbor_distances[neighbor_id],
            )
            for neighbor_id in neighbor_ids
        ]
        model_prediction = self.model_runtime.predict(
            task=task,
            node=node,
            profile=profile,
            latency_history_ms=latency_history,
            node_load=node_load,
            bandwidth_utilization=bandwidth_utilization,
            neighbor_observations=neighbor_observations,
            topology_id=None if self.physical_topology is None else self.physical_topology.topology_id,
        )
        ewma_predicted_latency_ms = max(1.0, latency_ewma + (latency_trend * 0.25))
        if model_prediction.lstm_latency_ms is not None:
            predicted_latency_ms = max(
                1.0,
                (model_prediction.lstm_latency_ms * 0.62) + (ewma_predicted_latency_ms * 0.38),
            )
            latency_predictor = "lstm_ewma_hybrid"
        else:
            predicted_latency_ms = ewma_predicted_latency_ms
            latency_predictor = "ewma_fallback"
        latency_volatility = clamp(
            (max(latency_history) - min(latency_history)) / max(8.0, mean(latency_history))
        )
        jitter_pressure = clamp(profile.jitter_ms / max(5.0, profile.latency_ms))
        loss_pressure = clamp(profile.packet_loss / 0.05)
        virtual_queue_pressure = clamp((node_load - 0.70) / 0.30)

        risk_margin_ms = (
            profile.jitter_ms * (0.85 + task.network_sensitivity)
            + predicted_latency_ms * (0.15 * node_load)
            + predicted_latency_ms * (0.12 * bandwidth_utilization)
            + predicted_latency_ms * (0.10 * loss_pressure)
            + predicted_latency_ms * (0.08 * virtual_queue_pressure)
        )
        robust_stable_latency_ms = max(1.0, predicted_latency_ms + risk_margin_ms)
        state_key = f"{node.node_id}:{task.network_source() or node.region}:{task.task_type}"
        previous_stable_latency = self._deterministic_latency_state.get(state_key)
        if previous_stable_latency is None:
            stable_latency_ms = robust_stable_latency_ms
        else:
            stable_latency_ms = (previous_stable_latency * 0.84) + (robust_stable_latency_ms * 0.16)
        self._deterministic_latency_state[state_key] = stable_latency_ms

        risk_factor = 1.0 + (task.network_sensitivity * 0.9)
        guaranteed_bandwidth_mbps = profile.guaranteed_bandwidth_mbps(risk_factor=risk_factor)
        delivery_probability = profile.delivery_probability()

        latency_target_ms = task.max_latency_ms or max(35.0, stable_latency_ms * 1.25)
        latency_history_score = 1.0 / (1.0 + (predicted_latency_ms / max(10.0, latency_target_ms)))
        jitter_score = 1.0 - jitter_pressure
        node_load_score = 1.0 - node_load
        bandwidth_score = 1.0 - bandwidth_utilization
        gnn_stability_score = (
            float(model_prediction.gnn_stability_score)
            if model_prediction.gnn_stability_score is not None
            else 0.5
        )
        feature_fusion_score = clamp(
            (latency_history_score * 0.32)
            + (jitter_score * 0.20)
            + (node_load_score * 0.18)
            + (bandwidth_score * 0.12)
            + (gnn_stability_score * 0.18)
        )
        deterministic_confidence = clamp(
            1.0
            - (
                (latency_volatility * 0.26)
                + (jitter_pressure * 0.22)
                + (node_load * 0.18)
                + (bandwidth_utilization * 0.18)
                + (loss_pressure * 0.10)
                + (virtual_queue_pressure * 0.06)
            )
        )
        if model_prediction.gnn_stability_score is not None:
            deterministic_confidence = clamp(
                (deterministic_confidence * 0.82) + (float(model_prediction.gnn_stability_score) * 0.18)
            )
        uncertainty = clamp(1.0 - deterministic_confidence)
        active_model_features = []
        if model_prediction.lstm_latency_ms is not None:
            active_model_features.append("lstm_latency_prediction")
        if model_prediction.gnn_stability_score is not None:
            active_model_features.append("graphsage_topology_score")
        transfer_ticks = max(
            0,
            ceil(
                (stable_latency_ms / 40.0)
                + ((task.estimated_input_size_gb() * 120.0) / guaranteed_bandwidth_mbps)
            ),
        )
        return {
            "stable_latency_ms": stable_latency_ms,
            "raw_latency_ms": profile.latency_ms,
            "robust_stable_latency_ms": robust_stable_latency_ms,
            "deterministic_latency_ms": stable_latency_ms,
            "latency_stabilization_delta_ms": robust_stable_latency_ms - stable_latency_ms,
            "predicted_latency_ms": predicted_latency_ms,
            "latency_ewma_ms": latency_ewma,
            "latency_predictor": latency_predictor,
            "robust_latency_ms": profile.robust_latency_ms(risk_factor=risk_factor),
            "guaranteed_bandwidth_mbps": guaranteed_bandwidth_mbps,
            "delivery_probability": delivery_probability,
            "uncertainty": uncertainty,
            "deterministic_confidence": deterministic_confidence,
            "transfer_ticks": float(transfer_ticks),
            "feature_fusion_score": feature_fusion_score,
            "latency_volatility": latency_volatility,
            "node_load": node_load,
            "bandwidth_utilization": bandwidth_utilization,
            "virtual_queue_pressure": virtual_queue_pressure,
            "fusion_features": {
                "latency_history": latency_history_score,
                "jitter": jitter_score,
                "node_load": node_load_score,
                "bandwidth_utilization": bandwidth_score,
                "gnn_topology": gnn_stability_score,
            },
            "feature_weights": {
                "latency_history": 0.32,
                "jitter": 0.20,
                "node_load": 0.18,
                "bandwidth_utilization": 0.12,
                "gnn_topology": 0.18,
            },
            "latency_history_ms": latency_history,
            "model_prediction": model_prediction.to_dict(),
            "data_status": {
                "latency_history": "暂用链路基线时延与抖动合成，待接入真实探测序列后可训练 LSTM。",
                "bandwidth_utilization": "暂用带宽波动与丢包估计，待接入交换机/云监控链路利用率。",
                "gnn_topology": (
                    "GraphSAGE 输出因运行时特征超出训练分布而未参与评分；gnn_topology 使用中性兜底分，需接入同分布拓扑/调用链特征后再启用。"
                    if model_prediction.gnn_applicable is False
                    else (
                        "GraphSAGE 模型已参与 gnn_topology 评分；当前使用物理拓扑传播时延加权的仿真算力邻居特征。"
                        if model_prediction.gnn_neighbor_mode == "physical_topology_distance_weighted_neighbors"
                        else "GraphSAGE 模型已参与 gnn_topology 评分；未注册物理拓扑时使用候选路径自嵌入兜底。"
                    )
                    if model_prediction.gnn_stability_score is not None
                    else "GraphSAGE 未加载，gnn_topology 使用中性兜底分；待接入模型文件和真实拓扑边特征。"
                ),
            },
            "algorithm": "deterministic_fusion_with_optional_models",
            "active_model_features": active_model_features,
            "physical_topology": {
                "topology_id": None if self.physical_topology is None else self.physical_topology.topology_id,
                "neighbor_mode": model_prediction.gnn_neighbor_mode,
                "selected_node_id": node.node_id,
                "selected_node_service_region": node.service_region or node.location or node.region,
                "selected_node_location": node.location or node.region,
                "selected_node_physical_region": node.region,
                "selected_node_access_point": selected_anchor,
                "source_location": task.network_source(),
                "compute_neighbor_ids": neighbor_ids,
                "topology_reachable_neighbor_count": len(neighbor_ids),
                "direct_compute_neighbor_ids": direct_neighbor_ids,
                "direct_compute_neighbor_count": len(direct_neighbor_ids),
                "compute_neighbor_distance_ms": neighbor_distances,
                "compute_neighbor_locations": {
                    neighbor_id: node_by_id[neighbor_id].location or node_by_id[neighbor_id].region
                    for neighbor_id in neighbor_ids
                },
            },
        }

    def _network_feasible(self, task: Task, network_snapshot: dict[str, Any]) -> bool:
        if (
            task.max_latency_ms is not None
            and float(network_snapshot["stable_latency_ms"]) > task.max_latency_ms
        ):
            return False
        if (
            task.min_bandwidth_mbps is not None
            and float(network_snapshot["guaranteed_bandwidth_mbps"]) < task.min_bandwidth_mbps
        ):
            return False
        return True

    def _network_raw(self, task: Task, network_snapshot: dict[str, Any]) -> float:
        stable_latency_ms = float(network_snapshot["stable_latency_ms"])
        latency_target_ms = task.max_latency_ms or max(35.0, stable_latency_ms * 1.25)
        latency_score = 1.0 / (1.0 + (stable_latency_ms / max(10.0, latency_target_ms)))
        required_bandwidth = task.min_bandwidth_mbps or (80.0 + (task.estimated_input_size_gb() * 20.0))
        guaranteed_bandwidth = float(network_snapshot["guaranteed_bandwidth_mbps"])
        bandwidth_score = guaranteed_bandwidth / (guaranteed_bandwidth + required_bandwidth)
        return clamp(
            (float(network_snapshot["feature_fusion_score"]) * 0.40)
            + (latency_score * 0.24)
            + (bandwidth_score * 0.14)
            + (float(network_snapshot["delivery_probability"]) * 0.12)
            + (float(network_snapshot["deterministic_confidence"]) * 0.10)
        )

    def _security_raw(self, task: Task, node: Node, network_snapshot: dict[str, Any]) -> float:
        region_allowed = not task.allowed_regions or any(
            node.matches_deployment_region(region) for region in task.allowed_regions
        )
        data_residency_score = 1.0 if region_allowed else 0.0
        if task.data_region and task.security_level == "high":
            data_residency_score = 1.0 if node.matches_deployment_region(task.data_region) else 0.35

        isolation_scores = {
            "none": 0.35,
            "process": 0.65,
            "container": 0.82,
            "namespace": 0.95,
        }
        isolation_score = isolation_scores.get(task.isolation_level, 0.65)
        if task.security_level == "high" and task.isolation_level in {"none", "process"}:
            isolation_score *= 0.72

        transport_score = 1.0
        if task.require_encrypted_transport:
            transport_score = 0.70 + (0.30 * float(network_snapshot["delivery_probability"]))

        violation_penalty = 0.0
        if node.node_id in task.forbidden_nodes:
            violation_penalty += 1.0
        if not region_allowed:
            violation_penalty += 0.7

        return clamp(
            (data_residency_score * 0.38)
            + (isolation_score * 0.30)
            + (transport_score * 0.22)
            + ((node.reliability_score or 0.0) * 0.10)
            - violation_penalty
        )

    def _ewma(self, values: list[float], alpha: float = 0.58) -> float:
        if not values:
            return 0.0
        current = float(values[0])
        for value in values[1:]:
            current = (alpha * float(value)) + ((1.0 - alpha) * current)
        return current
