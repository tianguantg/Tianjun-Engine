package org.cloudsimplus.examples.tianjun;

import com.google.gson.Gson;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParseException;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Comparator;
import java.util.Locale;
import java.util.Map;

/**
 * Minimal HTTP bridge between CloudSim Plus and the Python Tianjun control plane.
 */
public class TianjunHttpBridge {
    private static final int ERROR_BODY_LIMIT = 800;

    private final HttpClient client;
    private final Gson gson;
    private final String server;

    public TianjunHttpBridge(final String server) {
        this.server = stripTrailingSlash(server);
        this.gson = new Gson();
        this.client = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(5))
            .build();
    }

    public boolean isHealthy() {
        try {
            final String body = get("/health");
            final JsonObject root = parseObject(body, "/health");
            return "ok".equalsIgnoreCase(stringField(root, "status", ""));
        } catch (IOException | JsonParseException | IllegalStateException e) {
            return false;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return false;
        }
    }

    public void registerNode(final SimNode node) {
        post("/nodes/register", nodeRegistrationJson(node));
    }

    public void registerTopology(final String topologyJson) {
        post("/topology/register", topologyJson);
    }

    public void registerNode(final SimNode node, final Map<String, NetworkPath> networkPaths) {
        post("/nodes/register", nodeRegistrationJson(node, networkPathsJson(networkPaths)));
    }

    public void heartbeat(final SimNode node, final double tick) {
        heartbeat(node, tick, 0.0, 0.0, 0.0);
    }

    public void heartbeat(
        final SimNode node,
        final double tick,
        final double cpuUtilization,
        final double ramUtilization,
        final double bandwidthUtilization
    ) {
        post("/nodes/heartbeat", heartbeatJson(node, tick, cpuUtilization, ramUtilization, bandwidthUtilization));
    }

    public void heartbeat(
        final SimNode node,
        final double tick,
        final double cpuUtilization,
        final double ramUtilization,
        final double bandwidthUtilization,
        final Map<String, NetworkPath> networkPaths
    ) {
        post("/nodes/heartbeat", heartbeatJson(
            node,
            tick,
            cpuUtilization,
            ramUtilization,
            bandwidthUtilization,
            networkPathsJson(networkPaths)
        ));
    }

    public boolean tryHeartbeat(
        final SimNode node,
        final double tick,
        final double cpuUtilization,
        final double ramUtilization,
        final double bandwidthUtilization
    ) {
        try {
            heartbeat(node, tick, cpuUtilization, ramUtilization, bandwidthUtilization);
            return true;
        } catch (IllegalStateException e) {
            return false;
        }
    }

    public boolean tryHeartbeat(
        final SimNode node,
        final double tick,
        final double cpuUtilization,
        final double ramUtilization,
        final double bandwidthUtilization,
        final Map<String, NetworkPath> networkPaths
    ) {
        try {
            heartbeat(node, tick, cpuUtilization, ramUtilization, bandwidthUtilization, networkPaths);
            return true;
        } catch (IllegalStateException e) {
            return false;
        }
    }

    public SchedulingResult previewSchedule(final SimTask task) {
        final String response = post("/schedule/preview", taskJson(task));
        final JsonObject root = parseObject(response, "/schedule/preview");
        final String status = stringField(root, "status", "unknown");
        final String nodeId = stringField(root, "node_id", "");
        final double score = doubleField(root, "total_score", 0.0);
        return new SchedulingResult(status, nodeId, task.taskId(), score, response);
    }

    public SchedulingResult commitSchedule(final SimTask task) {
        final String response = post("/schedule/commit", taskJson(task));
        final JsonObject root = parseObject(response, "/schedule/commit");
        final String status = stringField(root, "status", "unknown");
        final String nodeId = stringField(root, "node_id", "");
        final double score = doubleField(root, "total_score", 0.0);
        String leaseTaskId = task.taskId();
        if (root.has("lease") && root.get("lease").isJsonObject()) {
            leaseTaskId = stringField(root.getAsJsonObject("lease"), "task_id", task.taskId());
        }
        return new SchedulingResult(status, nodeId, leaseTaskId, score, response);
    }

    public void reportResult(final SimTaskResult result) {
        post("/task-runs/result", resultJson(result));
    }

    private String get(final String path) throws IOException, InterruptedException {
        final HttpRequest request = HttpRequest.newBuilder()
            .uri(URI.create(server + path))
            .timeout(Duration.ofSeconds(15))
            .GET()
            .build();
        final HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        if (response.statusCode() >= 400) {
            throw new IllegalStateException(apiError("GET", path, response.statusCode(), response.body()));
        }
        return response.body();
    }

    private String post(final String path, final String json) {
        final HttpRequest request = HttpRequest.newBuilder()
            .uri(URI.create(server + path))
            .timeout(Duration.ofSeconds(20))
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(json, StandardCharsets.UTF_8))
            .build();
        try {
            final HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
            if (response.statusCode() >= 400) {
                throw new IllegalStateException(apiError("POST", path, response.statusCode(), response.body()));
            }
            return response.body();
        } catch (IOException e) {
            throw new IllegalStateException("Cannot reach Tianjun API POST " + server + path, e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Interrupted while calling Tianjun API POST " + server + path, e);
        }
    }

    private String nodeRegistrationJson(final SimNode node) {
        return nodeRegistrationJson(node, networkPathsJson(node, 0.0, 0.0, 0.0));
    }

    private String nodeRegistrationJson(final SimNode node, final String networkPaths) {
        return """
            {
              "node_id": "%s",
              "region": "%s",
              "location": "%s",
              "service_region": "%s",
              "labels": ["cloudsim", "cpu", "%s", "latency-sensitive"],
              "capacity": {"cpu": %.4f, "memory": %.4f, "gpu": %.4f, "storage": %.4f},
              "cost_per_tick": %.4f,
              "base_reliability": %.4f,
              "performance_factors": {"inference": %.4f, "batch_cpu": %.4f, "analytics": %.4f, "streaming": %.4f},
              "network_paths": %s
            }
            """.formatted(
            node.nodeId(),
            node.region(),
            node.location(),
            node.serviceRegion(),
            node.region(),
            node.cpu(),
            node.memoryGb(),
            node.gpu(),
            node.storageGb(),
            node.costPerTick(),
            node.reliability(),
            node.performanceFactor(),
            node.performanceFactor(),
            node.performanceFactor(),
            node.performanceFactor(),
            networkPaths
        );
    }

    private String heartbeatJson(
        final SimNode node,
        final double tick,
        final double cpuUtilization,
        final double ramUtilization,
        final double bandwidthUtilization
    ) {
        return heartbeatJson(
            node,
            tick,
            cpuUtilization,
            ramUtilization,
            bandwidthUtilization,
            networkPathsJson(node, tick, cpuUtilization, bandwidthUtilization)
        );
    }

    private String heartbeatJson(
        final SimNode node,
        final double tick,
        final double cpuUtilization,
        final double ramUtilization,
        final double bandwidthUtilization,
        final String networkPaths
    ) {
        final double loadWave = 0.5 + (Math.sin((tick + node.index()) * 0.35) * 0.5);
        final double loadPressure = clamp(cpuUtilization * 0.55 + ramUtilization * 0.25 + bandwidthUtilization * 0.20, 0.0, 1.0);
        final double health = clamp(0.96 - loadWave * 0.08 - loadPressure * 0.22 - node.risk() * 0.08, 0.45, 0.99);
        final double reliability = clamp(node.reliability() - node.risk() * 0.035 - loadPressure * 0.025, 0.45, 0.999);
        return """
            {
              "node_id": "%s",
              "health_score": %.4f,
              "online": true,
              "cost_per_tick": %.4f,
              "region": "%s",
              "location": "%s",
              "service_region": "%s",
              "labels": ["cloudsim", "cpu", "%s", "latency-sensitive"],
              "performance_factors": {"inference": %.4f, "batch_cpu": %.4f, "analytics": %.4f, "streaming": %.4f},
              "network_paths": %s,
              "sim_tick": %.4f,
              "simulated": true,
              "telemetry": {"cpu_utilization": %.6f, "ram_utilization": %.6f, "bandwidth_utilization": %.6f},
              "reliability_score": %.4f
            }
            """.formatted(
            node.nodeId(),
            health,
            node.costPerTick(),
            node.region(),
            node.location(),
            node.serviceRegion(),
            node.region(),
            node.performanceFactor(),
            node.performanceFactor(),
            node.performanceFactor(),
            node.performanceFactor(),
            networkPaths,
            tick,
            clamp(cpuUtilization, 0.0, 1.0),
            clamp(ramUtilization, 0.0, 1.0),
            clamp(bandwidthUtilization, 0.0, 1.0),
            reliability
        );
    }

    private String taskJson(final SimTask task) {
        return """
            {
              "task_id": "%s",
              "task_type": "%s",
              "demand": {"cpu": %.4f, "memory": %.4f, "gpu": %.4f, "storage": %.4f},
              "estimated_duration": %d,
              "priority": %d,
              "budget": %.4f,
              "deadline": %d,
              "data_region": "%s",
              "source_region": "%s",
              "input_size_gb": %.4f,
              "max_latency_ms": %.4f,
              "min_bandwidth_mbps": %.4f,
              "network_sensitivity": %.4f,
              "preferred_labels": ["cloudsim"]
            }
            """.formatted(
            task.taskId(),
            task.taskType(),
            task.cpu(),
            task.memoryGb(),
            task.gpu(),
            task.storageGb(),
            task.estimatedDuration(),
            task.priority(),
            task.budget(),
            task.deadline(),
            task.sourceRegion(),
            task.sourceRegion(),
            task.inputSizeGb(),
            task.maxLatencyMs(),
            task.minBandwidthMbps(),
            task.networkSensitivity()
        );
    }

    private String resultJson(final SimTaskResult result) {
        return """
            {
              "node_id": "%s",
              "task_id": "%s",
              "success": %s,
              "duration_seconds": %.4f,
              "stdout": "%s",
              "stderr": "%s",
              "returncode": %d,
              "cost": %.4f
            }
            """.formatted(
            result.nodeId(),
            result.taskId(),
            result.success() ? "true" : "false",
            result.durationSeconds(),
            escapeJson(result.stdout()),
            escapeJson(result.stderr()),
            result.returnCode(),
            result.cost()
        );
    }

    private String networkPathsJson(
        final SimNode node,
        final double tick,
        final double cpuUtilization,
        final double bandwidthUtilization
    ) {
        final StringBuilder builder = new StringBuilder("{");
        final String[] regions = {"shanghai", "beijing", "hangzhou"};
        for (int i = 0; i < regions.length; i++) {
            if (i > 0) {
                builder.append(",");
            }
            final double regionDistance = Math.abs(regionIndex(node.region()) - i);
            final double wave = Math.sin((tick * 0.17) + node.index() * 0.41 + i * 0.77);
            final double burst = Math.max(0.0, Math.sin((tick * 0.071) + node.index() * 0.19 + i));
            final double pressure = clamp(cpuUtilization * 0.45 + bandwidthUtilization * 0.55, 0.0, 1.0);
            final double baseLatency = 7.0 + regionDistance * 18.0 + node.risk() * 18.0;
            final double latency = Math.max(1.0, baseLatency + Math.abs(wave) * (2.8 + node.risk() * 8.0) + pressure * 9.0 + burst * node.risk() * 10.0);
            final double jitter = 1.2 + regionDistance * 3.6 + node.risk() * 9.0 + Math.abs(wave) * 2.4 + pressure * 4.5;
            final double bandwidth = Math.max(80.0, node.bandwidthMbps() - regionDistance * 160.0 - node.risk() * 180.0 - pressure * 140.0 - burst * 60.0);
            final double packetLoss = clamp(0.001 + regionDistance * 0.006 + node.risk() * 0.025 + pressure * 0.012 + burst * node.risk() * 0.018, 0.0, 0.18);
            final double reliability = clamp(node.reliability() - packetLoss * 1.4, 0.45, 0.999);
            builder.append("""
                "%s": {"latency_ms": %.4f, "jitter_ms": %.4f, "bandwidth_mbps": %.4f, "bandwidth_jitter_mbps": %.4f, "packet_loss": %.6f, "path_reliability": %.6f}
                """.formatted(regions[i], latency, jitter, bandwidth, bandwidth * 0.08 + jitter * 3.0, packetLoss, reliability));
        }
        builder.append("}");
        return builder.toString();
    }

    private String networkPathsJson(final Map<String, NetworkPath> networkPaths) {
        final StringBuilder builder = new StringBuilder("{");
        int index = 0;
        for (final var entry : networkPaths.entrySet().stream().sorted(Map.Entry.comparingByKey(Comparator.naturalOrder())).toList()) {
            if (index++ > 0) {
                builder.append(",");
            }
            final var profile = entry.getValue();
            builder.append("""
                "%s": {"latency_ms": %.4f, "jitter_ms": %.4f, "bandwidth_mbps": %.4f, "bandwidth_jitter_mbps": %.4f, "packet_loss": %.6f, "path_reliability": %.6f}
                """.formatted(
                escapeJson(entry.getKey()),
                profile.latencyMs(),
                profile.jitterMs(),
                profile.bandwidthMbps(),
                profile.bandwidthJitterMbps(),
                profile.packetLoss(),
                profile.pathReliability()
            ));
        }
        builder.append("}");
        return builder.toString();
    }

    private static String stripTrailingSlash(final String value) {
        return value.endsWith("/") ? value.substring(0, value.length() - 1) : value;
    }

    private JsonObject parseObject(final String body, final String path) {
        final JsonElement parsed = gson.fromJson(body, JsonElement.class);
        if (parsed == null || !parsed.isJsonObject()) {
            throw new JsonParseException("Tianjun API " + server + path + " did not return a JSON object.");
        }
        return parsed.getAsJsonObject();
    }

    private static String stringField(final JsonObject root, final String key, final String fallback) {
        if (!root.has(key) || root.get(key).isJsonNull()) {
            return fallback;
        }
        try {
            return root.get(key).getAsString();
        } catch (ClassCastException | IllegalStateException | NumberFormatException e) {
            return fallback;
        }
    }

    private static double doubleField(final JsonObject root, final String key, final double fallback) {
        if (!root.has(key) || root.get(key).isJsonNull()) {
            return fallback;
        }
        try {
            return root.get(key).getAsDouble();
        } catch (ClassCastException | IllegalStateException | NumberFormatException e) {
            return fallback;
        }
    }

    private String apiError(final String method, final String path, final int statusCode, final String body) {
        return "Tianjun API " + method + " " + server + path
            + " failed with HTTP " + statusCode
            + "; response body: " + truncate(body);
    }

    private static String truncate(final String value) {
        if (value == null || value.length() <= ERROR_BODY_LIMIT) {
            return value == null ? "" : value;
        }
        return value.substring(0, ERROR_BODY_LIMIT) + "...";
    }

    private static double clamp(final double value, final double min, final double max) {
        return Math.max(min, Math.min(max, value));
    }

    private static String escapeJson(final String value) {
        return value == null ? "" : value
            .replace("\\", "\\\\")
            .replace("\"", "\\\"")
            .replace("\r", "\\r")
            .replace("\n", "\\n");
    }

    private static int regionIndex(final String region) {
        return switch (region.toLowerCase(Locale.ROOT)) {
            case "shanghai", "sh" -> 0;
            case "beijing", "bj" -> 1;
            case "hangzhou", "hz" -> 2;
            default -> 0;
        };
    }

    public record SimNode(
        String nodeId,
        String region,
        String location,
        String serviceRegion,
        int index,
        double cpu,
        double memoryGb,
        double gpu,
        double storageGb,
        double bandwidthMbps,
        double costPerTick,
        double reliability,
        double performanceFactor,
        double risk
    ) {
    }

    public record SimTask(
        String taskId,
        String taskType,
        double cpu,
        double memoryGb,
        double gpu,
        double storageGb,
        int estimatedDuration,
        int priority,
        double budget,
        int deadline,
        String sourceRegion,
        double inputSizeGb,
        double maxLatencyMs,
        double minBandwidthMbps,
        double networkSensitivity
    ) {
    }

    public record NetworkPath(
        double latencyMs,
        double jitterMs,
        double bandwidthMbps,
        double bandwidthJitterMbps,
        double packetLoss,
        double pathReliability
    ) {
    }

    public record SchedulingResult(String status, String nodeId, String taskId, double totalScore, String rawJson) {
        public boolean hasDecision() {
            return nodeId != null && !nodeId.isBlank() && ("leased".equalsIgnoreCase(status) || "scheduled".equalsIgnoreCase(status));
        }
    }

    public record SimTaskResult(
        String nodeId,
        String taskId,
        boolean success,
        double durationSeconds,
        String stdout,
        String stderr,
        int returnCode,
        double cost
    ) {
    }
}
