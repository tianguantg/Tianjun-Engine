/*
 * Case-referenced DCI topology experiment for Tianjun Engine.
 *
 * The logical chain is abstracted from a public compute-network DCI case:
 * DC1 -> Border1 -> PE1 -> backbone P routers -> PE3 -> Border2 -> DC2,
 * plus a user access point connected at the destination-side PE. A simulated
 * DC3 access branch is added at PE3 so Tianjun can evaluate one access point
 * per Hermes deployment region.
 * Resource capacities and dynamic impairments are reproducible simulation
 * assumptions and are not vendor production telemetry.
 */
package org.cloudsimplus.examples;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import org.cloudsimplus.brokers.DatacenterBroker;
import org.cloudsimplus.brokers.DatacenterBrokerSimple;
import org.cloudsimplus.builders.tables.CloudletsTableBuilder;
import org.cloudsimplus.cloudlets.Cloudlet;
import org.cloudsimplus.cloudlets.CloudletSimple;
import org.cloudsimplus.core.CloudSimPlus;
import org.cloudsimplus.datacenters.Datacenter;
import org.cloudsimplus.datacenters.DatacenterSimple;
import org.cloudsimplus.examples.tianjun.TianjunHttpBridge;
import org.cloudsimplus.examples.tianjun.TianjunHttpBridge.NetworkPath;
import org.cloudsimplus.examples.tianjun.TianjunHttpBridge.SchedulingResult;
import org.cloudsimplus.examples.tianjun.TianjunHttpBridge.SimNode;
import org.cloudsimplus.examples.tianjun.TianjunHttpBridge.SimTask;
import org.cloudsimplus.examples.tianjun.TianjunHttpBridge.SimTaskResult;
import org.cloudsimplus.hosts.Host;
import org.cloudsimplus.hosts.HostSimple;
import org.cloudsimplus.listeners.EventInfo;
import org.cloudsimplus.network.topologies.BriteNetworkTopology;
import org.cloudsimplus.resources.Pe;
import org.cloudsimplus.resources.PeSimple;
import org.cloudsimplus.utilizationmodels.UtilizationModelDynamic;
import org.cloudsimplus.vms.Vm;
import org.cloudsimplus.vms.VmSimple;

import java.io.BufferedWriter;
import java.io.IOException;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Random;
import java.util.Set;

/**
 * Runs a three-access-point DCI topology against the Tianjun HTTP control plane.
 *
 * <p>BRITE supplies the static DCI propagation graph used by CloudSim Plus.
 * Heartbeat path observations start from the graph-derived site-to-site delay,
 * then apply documented congestion and optional failure disturbances to produce
 * raw graph snapshots suitable for subsequent GNN dataset labeling.</p>
 */
public final class HuaweiDciTianjunExperiment {
    private static final String TOPOLOGY_FILE = "huawei-dci-reference.brite";
    private static final String DEFAULT_SERVER = "http://127.0.0.1:8024";
    private static final String[] REGIONS = {"dc1", "dc2", "dc3"};
    private static final String[] LOCATIONS = {"beijing", "hangzhou", "chengdu", "chongqing", "guangzhou", "shenzhen"};
    private static final String[] SERVICE_REGIONS = {"east", "east", "west", "west", "south", "south"};
    private static final Set<String> SUPPORTED_SCENARIOS = Set.of("normal", "fault", "fault-active");
    private static final int[] LOCATION_SITES = {0, 0, 1, 1, 2, 2};
    private static final int VMS_PER_LOCATION = 4;
    private static final int DEFAULT_CLOUDLETS = 36;
    private static final long DEFAULT_SEED = 20260527L;
    private static final DateTimeFormatter RUN_ID_TIMESTAMP_FORMAT = DateTimeFormatter.ofPattern("yyyyMMddHHmmss");
    private static final double HEARTBEAT_INTERVAL_SECONDS = 5.0;
    private static final double LOCAL_FABRIC_LATENCY_MS = 0.8;
    private static final double LOCAL_FABRIC_BANDWIDTH_MBPS = 25_000.0;
    private static final double DCI_BOTTLENECK_BANDWIDTH_MBPS = 10_000.0;
    private static final double VM_DESTRUCTION_DELAY_SECONDS = 600.0;
    private static final double CLOUDLET_RAM_UTILIZATION_MIN = 0.04;
    private static final double CLOUDLET_RAM_UTILIZATION_RANGE = 0.08;
    private static final double CLOUDLET_BW_UTILIZATION_MIN = 0.03;
    private static final double CLOUDLET_BW_UTILIZATION_RANGE = 0.08;

    private final CloudSimPlus simulation;
    private final DatacenterBroker broker;
    private final TianjunHttpBridge bridge;
    private final BriteNetworkTopology topology;
    private final List<Datacenter> datacenters;
    private final List<Vm> vmList;
    private final List<Cloudlet> cloudletList;
    private final Map<String, Vm> vmByNodeId;
    private final Map<String, SimNode> nodeById;
    private final Map<Long, SimTask> taskByCloudletId;
    private final Map<Long, String> selectedNodeByCloudletId;
    private final Map<Long, Vm> selectedVmByCloudletId;
    private final Random random;
    private final Gson gson;
    private final String controlPlaneServer;
    private final String disturbanceScenario;
    private final String experimentRunId;
    private final BufferedWriter snapshotWriter;
    private double lastHeartbeatTick = -1.0;

    public static void main(final String[] args) throws IOException {
        final String server = args.length > 0 ? args[0] : DEFAULT_SERVER;
        final String scenario = args.length > 1 ? args[1] : "normal";
        final int cloudlets = args.length > 2 ? Integer.parseInt(args[2]) : DEFAULT_CLOUDLETS;
        final long seed = args.length > 3 ? Long.parseLong(args[3]) : DEFAULT_SEED;
        final Path output = Path.of(args.length > 4 ? args[4] : "output/huawei-dci-topology-snapshots.jsonl");
        final String runId = args.length > 5 ? args[5] : "";
        new HuaweiDciTianjunExperiment(server, scenario, cloudlets, seed, output, runId).run();
    }

    private HuaweiDciTianjunExperiment(
        final String server,
        final String disturbanceScenario,
        final int cloudletCount,
        final long seed,
        final Path outputPath,
        final String runId
    ) throws IOException {
        final String normalizedScenario = validateScenario(disturbanceScenario);
        validateServer(server);
        validateCloudletCount(cloudletCount);
        this.simulation = new CloudSimPlus();
        this.bridge = new TianjunHttpBridge(server);
        this.controlPlaneServer = server;
        this.disturbanceScenario = normalizedScenario;
        this.experimentRunId = normalizeRunId(runId, this.disturbanceScenario, seed);
        this.random = new Random(seed);
        this.gson = new GsonBuilder().disableHtmlEscaping().create();
        this.vmByNodeId = new LinkedHashMap<>();
        this.nodeById = new LinkedHashMap<>();
        this.taskByCloudletId = new LinkedHashMap<>();
        this.selectedNodeByCloudletId = new LinkedHashMap<>();
        this.selectedVmByCloudletId = new LinkedHashMap<>();
        this.datacenters = createDatacenters();
        this.broker = new DatacenterBrokerSimple(simulation)
            .setVmDestructionDelay(VM_DESTRUCTION_DELAY_SECONDS);
        this.topology = configureDciTopology();
        this.vmList = createVms();
        this.cloudletList = createCloudlets(cloudletCount);
        final Path parent = outputPath.toAbsolutePath().getParent();
        if (parent != null) {
            Files.createDirectories(parent);
        }
        this.snapshotWriter = Files.newBufferedWriter(outputPath.toAbsolutePath());
    }

    private void run() throws IOException {
        if (!bridge.isHealthy()) {
            snapshotWriter.close();
            throw new IllegalStateException("Tianjun control plane /health is not reachable at " + controlPlaneServer + ".");
        }

        broker.setDatacenterMapper((lastDatacenter, vm) -> datacenters.get(siteIndexForVm(vm)));
        broker.submitVmList(vmList);
        registerTianjunNodes();
        simulation.addOnClockTickListener(this::onClockTick);
        bindTasksFromTianjunDecisions();
        broker.submitCloudletList(
            cloudletList.stream().filter(cloudlet -> selectedVmByCloudletId.containsKey(cloudlet.getId())).toList()
        );

        writeSnapshot(0.0, "registered");
        simulation.start();
        sendHeartbeats(simulation.clock());
        reportResults();
        writeSnapshot(simulation.clock(), "finished");
        snapshotWriter.close();

        final var finished = broker.getCloudletFinishedList();
        System.out.printf("=== Huawei-reference DCI Tianjun experiment (%s) ===%n", disturbanceScenario);
        System.out.printf("Run id: %s%n", experimentRunId);
        System.out.printf("Topology: DC1 -> Border1 -> PE1 -> IPCORE -> PE3 -> Border2 -> DC2, plus PE3 -> Border3 -> DC3%n");
        System.out.printf("Simulation nodes: %d, submitted tasks: %d, finished: %d%n", vmList.size(), cloudletList.size(), finished.size());
        System.out.printf("Max cross-site propagation baseline: %.3f ms, DCI bottleneck: %.0f Mbps%n", maxCrossSiteBaseLatencyMs(), DCI_BOTTLENECK_BANDWIDTH_MBPS);
        new CloudletsTableBuilder(finished).build();
        if (finished.size() != cloudletList.size()) {
            throw new IllegalStateException(
                "CloudSimPlus finished " + finished.size() + "/" + cloudletList.size()
                    + " submitted tasks; inspect bandwidth constraints, VM destruction delay, and CloudletScheduler warnings."
            );
        }
    }

    private List<Datacenter> createDatacenters() {
        final var list = new ArrayList<Datacenter>();
        for (int site = 0; site < REGIONS.length; site++) {
            final var dc = new DatacenterSimple(simulation, createHosts(site));
            dc.setName("DC" + (site + 1));
            dc.setSchedulingInterval(1.0);
            list.add(dc);
        }
        return list;
    }

    private List<Host> createHosts(final int site) {
        final var hosts = new ArrayList<Host>();
        for (int index = 0; index < 3; index++) {
            final int pes = 32;
            final long mips = site == 0 ? 2400L : site == 1 ? 2250L : 2200L;
            final long ramMb = 131_072L;
            final long bandwidthMbps = 25_000L;
            final long storageMb = 4_000_000L;
            final var peList = new ArrayList<Pe>();
            for (int pe = 0; pe < pes; pe++) {
                peList.add(new PeSimple(mips));
            }
            hosts.add(new HostSimple(ramMb, bandwidthMbps, storageMb, peList));
        }
        return hosts;
    }

    private BriteNetworkTopology configureDciTopology() {
        final var dciTopology = BriteNetworkTopology.getInstance(TOPOLOGY_FILE);
        simulation.setNetworkTopology(dciTopology);
        // BRITE node map: 0 DC1, 1 Border1, 2 PE1, 3/4 P routers,
        // 5 PE3, 6 Border2, 7 DC2, 8 user access, 9 Border3, 10 DC3.
        dciTopology.mapNode(datacenters.get(0), 0);
        dciTopology.mapNode(datacenters.get(1), 7);
        dciTopology.mapNode(datacenters.get(2), 10);
        dciTopology.mapNode(broker, 8);
        return dciTopology;
    }

    private List<Vm> createVms() {
        final var result = new ArrayList<Vm>();
        for (int locationIndex = 0; locationIndex < LOCATIONS.length; locationIndex++) {
            final int site = LOCATION_SITES[locationIndex];
            for (int index = 0; index < VMS_PER_LOCATION; index++) {
                final int pes = 4 + (index % 2) * 4;
                final double mips = site == 0 ? 2200.0 : site == 1 ? 2120.0 : 2050.0;
                final var vm = new VmSimple(mips, pes)
                    .setRam(16_384L + (index % 2) * 16_384L)
                    .setBw(5_000L)
                    .setSize(200_000L)
                    .setDescription("dci-" + REGIONS[site] + "-" + LOCATIONS[locationIndex] + "-vm-" + index);
                result.add(vm);
                vmByNodeId.put(vm.getDescription(), vm);
            }
        }
        return result;
    }

    private List<Cloudlet> createCloudlets(final int count) {
        final var result = new ArrayList<Cloudlet>();
        for (int index = 0; index < count; index++) {
            final var cloudlet = new CloudletSimple(index, 80_000L + index % 8 * 20_000L, 1 + index % 4);
            cloudlet.setFileSize(2_048L + index % 6 * 512L)
                .setOutputSize(1_024L)
                .setUtilizationModelCpu(new UtilizationModelDynamic(0.40 + random.nextDouble() * 0.42))
                .setUtilizationModelRam(new UtilizationModelDynamic(
                    CLOUDLET_RAM_UTILIZATION_MIN + random.nextDouble() * CLOUDLET_RAM_UTILIZATION_RANGE
                ))
                .setUtilizationModelBw(new UtilizationModelDynamic(
                    CLOUDLET_BW_UTILIZATION_MIN + random.nextDouble() * CLOUDLET_BW_UTILIZATION_RANGE
                ));
            result.add(cloudlet);
        }
        return result;
    }

    private void registerTianjunNodes() {
        for (int index = 0; index < vmList.size(); index++) {
            final var node = simNodeForVm(vmList.get(index), index);
            nodeById.put(node.nodeId(), node);
        }
        bridge.registerTopology(gson.toJson(topologyRegistration()));
        for (final var node : nodeById.values()) {
            bridge.registerNode(node, observedPaths(node, 0.0, 0.0));
            bridge.heartbeat(node, 0.0, 0.0, 0.0, 0.0, observedPaths(node, 0.0, 0.0));
        }
        System.out.printf("Registered physical DCI topology and %d attached compute nodes with topology-derived paths.%n", nodeById.size());
    }

    private void bindTasksFromTianjunDecisions() {
        int mapped = 0;
        for (int index = 0; index < cloudletList.size(); index++) {
            final Cloudlet cloudlet = cloudletList.get(index);
            final SimTask task = taskForCloudlet(cloudlet, index);
            final SchedulingResult decision = bridge.commitSchedule(task);
            if (!decision.hasDecision()) {
                System.out.printf(
                    "Tianjun rejected task %s: %s%n",
                    task.taskId(),
                    decision.rawJson()
                );
                continue;
            }
            final Vm selectedVm = vmByNodeId.get(decision.nodeId());
            if (selectedVm == null || selectedVm == Vm.NULL) {
                System.out.printf(
                    "Tianjun selected unknown node %s for task %s: %s%n",
                    decision.nodeId(),
                    task.taskId(),
                    decision.rawJson()
                );
                continue;
            }
            selectedVmByCloudletId.put(cloudlet.getId(), selectedVm);
            selectedNodeByCloudletId.put(cloudlet.getId(), decision.nodeId());
            taskByCloudletId.put(cloudlet.getId(), task);
            mapped++;
        }
        broker.setVmMapper(cloudlet -> selectedVmByCloudletId.getOrDefault(cloudlet.getId(), Vm.NULL));
        System.out.printf("Tianjun mapped %d/%d DCI tasks.%n", mapped, cloudletList.size());
        if (mapped == 0) {
            throw new IllegalStateException(
                "No DCI tasks were mapped by Tianjun; check node registration, task requirements, and /schedule/commit responses."
            );
        }
    }

    private void onClockTick(final EventInfo event) {
        final double tick = event.getTime();
        if (lastHeartbeatTick >= 0.0 && tick - lastHeartbeatTick < HEARTBEAT_INTERVAL_SECONDS) {
            return;
        }
        sendHeartbeats(tick);
        try {
            writeSnapshot(tick, "running");
        } catch (IOException exception) {
            throw new IllegalStateException("Cannot write topology snapshot.", exception);
        }
    }

    private void sendHeartbeats(final double tick) {
        lastHeartbeatTick = tick;
        for (final var vm : vmList) {
            final var node = nodeById.get(vm.getDescription());
            final double cpu = vm.getCpuPercentUtilization();
            final double ram = vm.getRam().getPercentUtilization();
            final double bw = vm.getBw().getPercentUtilization();
            bridge.tryHeartbeat(node, tick, cpu, ram, bw, observedPaths(node, tick, bw));
        }
    }

    private void reportResults() {
        for (final var cloudlet : broker.getCloudletFinishedList()) {
            final var task = taskByCloudletId.get(cloudlet.getId());
            final String nodeId = selectedNodeByCloudletId.get(cloudlet.getId());
            if (task == null || nodeId == null) {
                continue;
            }
            final double duration = Math.max(1.0, cloudlet.getFinishTime() - cloudlet.getStartTime());
            bridge.reportResult(new SimTaskResult(
                nodeId,
                task.taskId(),
                cloudlet.isFinished(),
                duration,
                "Huawei-reference DCI CloudSim cloudlet completed.",
                "",
                cloudlet.isFinished() ? 0 : 1,
                duration * nodeById.get(nodeId).costPerTick()
            ));
        }
    }

    private SimNode simNodeForVm(final Vm vm, final int index) {
        final int locationIndex = index / VMS_PER_LOCATION;
        final int site = LOCATION_SITES[locationIndex];
        return new SimNode(
            vm.getDescription(),
            REGIONS[site],
            LOCATIONS[locationIndex],
            SERVICE_REGIONS[locationIndex],
            index,
            vm.getPesNumber(),
            vm.getRam().getCapacity() / 1024.0,
            0.0,
            vm.getStorage().getCapacity() / 1024.0,
            vm.getBw().getCapacity(),
            1.10 + site * 0.08 + (index % VMS_PER_LOCATION) * 0.04,
            0.993 - site * 0.002,
            site == 0 ? 1.10 : 1.04,
            site == 0 ? 0.06 : 0.09
        );
    }

    private SimTask taskForCloudlet(final Cloudlet cloudlet, final int index) {
        final String sourceRegion = REGIONS[index % REGIONS.length];
        return new SimTask(
            experimentRunId + "-task-" + cloudlet.getId(),
            index % 3 == 0 ? "inference" : index % 3 == 1 ? "analytics" : "batch_cpu",
            Math.max(1.0, cloudlet.getPesNumber()),
            2.0 + index % 4 * 2.0,
            0.0,
            Math.max(1.0, cloudlet.getFileSize() / 1024.0),
            Math.max(1, (int) Math.ceil(cloudlet.getLength() / 8_000.0)),
            5 + index % 5,
            80.0,
            100,
            sourceRegion,
            0.5 + index % 4 * 0.4,
            sourceRegion.equals("dc1") ? 25.0 : sourceRegion.equals("dc2") ? 28.0 : 30.0,
            500.0,
            0.55 + index % 4 * 0.10
        );
    }

    private Map<String, NetworkPath> observedPaths(final SimNode node, final double tick, final double bandwidthUtilization) {
        final var paths = new LinkedHashMap<String, NetworkPath>();
        for (final String sourceRegion : REGIONS) {
            final boolean crossSite = !sourceRegion.equals(node.region());
            final double baselineLatency = crossSite ? siteToSiteBaseLatencyMs(sourceRegion, node.region()) : LOCAL_FABRIC_LATENCY_MS;
            final double baselineBandwidth = crossSite ? DCI_BOTTLENECK_BANDWIDTH_MBPS : LOCAL_FABRIC_BANDWIDTH_MBPS;
            final double pressure = clamp(bandwidthUtilization, 0.0, 1.0);
            final boolean degraded = crossSite && isDciDegraded(tick);
            final double periodicVariation = Math.abs(Math.sin(tick * 0.13 + node.index() * 0.27));
            final double latency = baselineLatency * (1.0 + pressure * 0.32) + periodicVariation * 1.2 + (degraded ? 16.0 : 0.0);
            final double jitter = 0.35 + periodicVariation * 0.8 + pressure * 2.5 + (degraded ? 7.0 : 0.0);
            final double bandwidth = Math.max(200.0, baselineBandwidth * (1.0 - pressure * 0.38) * (degraded ? 0.32 : 1.0));
            final double bandwidthVariation = Math.max(
                25.0,
                bandwidth * (0.03 + pressure * 0.12 + (degraded ? 0.18 : 0.0))
            );
            final double loss = clamp(0.0005 + pressure * 0.0045 + (degraded ? 0.035 : 0.0), 0.0, 0.20);
            paths.put(sourceRegion, new NetworkPath(
                latency,
                jitter,
                bandwidth,
                bandwidthVariation,
                loss,
                clamp(0.999 - loss * 1.7, 0.45, 0.999)
            ));
        }
        return paths;
    }

    private double siteToSiteBaseLatencyMs(final String sourceRegion, final String targetRegion) {
        final int sourceIndex = siteIndexForRegion(sourceRegion);
        final int targetIndex = siteIndexForRegion(targetRegion);
        return Math.max(1.0, topology.getDelay(datacenters.get(sourceIndex), datacenters.get(targetIndex)) * 1000.0);
    }

    private double maxCrossSiteBaseLatencyMs() {
        double maxLatency = 0.0;
        for (final String sourceRegion : REGIONS) {
            for (final String targetRegion : REGIONS) {
                if (!sourceRegion.equals(targetRegion)) {
                    maxLatency = Math.max(maxLatency, siteToSiteBaseLatencyMs(sourceRegion, targetRegion));
                }
            }
        }
        return maxLatency;
    }

    private boolean isDciDegraded(final double tick) {
        if (disturbanceScenario.contains("fault-active")) {
            return true;
        }
        return disturbanceScenario.contains("fault") && tick >= 18.0 && tick < 48.0;
    }

    private int siteIndexForVm(final Vm vm) {
        if (vm.getDescription().contains("-dc3-")) {
            return 2;
        }
        return vm.getDescription().contains("-dc2-") ? 1 : 0;
    }

    private int siteIndexForRegion(final String region) {
        for (int index = 0; index < REGIONS.length; index++) {
            if (REGIONS[index].equals(region)) {
                return index;
            }
        }
        throw new IllegalArgumentException("Unknown DCI region: " + region);
    }

    private void writeSnapshot(final double tick, final String phase) throws IOException {
        final var sample = new LinkedHashMap<String, Object>();
        sample.put("scenario", "huawei_reference_dci");
        sample.put("experiment_run_id", experimentRunId);
        sample.put("disturbance", disturbanceScenario);
        sample.put("phase", phase);
        sample.put("tick_seconds", tick);
        sample.put("provenance", provenance());
        sample.put("topology_id", "huawei_public_case_reference_dci_v1");
        sample.put("topology_nodes", topologyNodeNames());
        sample.put("topology_edges", topologyEdges());
        sample.put("compute_attachments", topologyRegistration().get("compute_attachments"));
        final var observations = new ArrayList<Map<String, Object>>();
        for (final var vm : vmList) {
            final var node = nodeById.get(vm.getDescription());
            final var item = new LinkedHashMap<String, Object>();
            item.put("node_id", node.nodeId());
            item.put("region", node.region());
            item.put("location", node.location());
            item.put("service_region", node.serviceRegion());
            item.put("cpu_utilization", vm.getCpuPercentUtilization());
            item.put("memory_utilization", vm.getRam().getPercentUtilization());
            item.put("bandwidth_utilization", vm.getBw().getPercentUtilization());
            item.put("network_paths", observedPaths(node, tick, vm.getBw().getPercentUtilization()));
            observations.add(item);
        }
        sample.put("compute_nodes", observations);
        sample.put("dci_degraded", isDciDegraded(tick));
        snapshotWriter.write(gson.toJson(sample));
        snapshotWriter.newLine();
        snapshotWriter.flush();
    }

    private List<Map<String, Object>> topologyEdges() {
        return List.of(
            edge("DC1", "Border1", 0.5, 40_000.0),
            edge("Border1", "PE1", 0.7, 40_000.0),
            edge("PE1", "P-Core-A", 1.2, 20_000.0),
            edge("P-Core-A", "P-Core-B", 6.0, 10_000.0),
            edge("P-Core-B", "PE3", 6.0, 10_000.0),
            edge("PE3", "Border2", 1.2, 20_000.0),
            edge("Border2", "DC2", 0.7, 40_000.0),
            edge("User-Access", "PE3", 3.0, 10_000.0),
            edge("PE3", "Border3", 1.0, 20_000.0),
            edge("Border3", "DC3", 0.7, 40_000.0)
        );
    }

    private List<String> topologyNodeNames() {
        return List.of("DC1", "Border1", "PE1", "P-Core-A", "P-Core-B", "PE3", "Border2", "DC2", "User-Access", "Border3", "DC3");
    }

    private Map<String, Object> topologyRegistration() {
        final var payload = new LinkedHashMap<String, Object>();
        payload.put("topology_id", "huawei_public_case_reference_dci_v1");
        payload.put("topology_nodes", topologyNodeNames());
        payload.put("topology_edges", topologyEdges());
        final var attachments = new LinkedHashMap<String, String>();
        for (final var node : nodeById.values()) {
            attachments.put(node.nodeId(), "DC" + (siteIndexForRegion(node.region()) + 1));
        }
        payload.put("compute_attachments", attachments);
        payload.put("provenance", provenance());
        return payload;
    }

    private Map<String, Object> provenance() {
        final var value = new LinkedHashMap<String, Object>();
        value.put("structure_status", "public_case_abstraction");
        value.put("numeric_parameter_status", "calibrated_simulation_assumptions_not_vendor_telemetry");
        value.put("compute_node_status", "cloudsimplus_vm_resources_not_physical_inventory");
        value.put("location_status", "named_simulation_placement_labels_not_vendor_disclosed_sites");
        value.put("service_region_status", "three_simulated_deployment_zones_each_bound_to_one_simulated_access_point");
        value.put("description", "DCI structure follows the public case diagram; DC3 is a reproducible simulation branch for one-region-one-access-point evaluation. Named city placement, capacities and impairment dynamics are experimental values.");
        return value;
    }

    private Map<String, Object> edge(final String source, final String target, final double delayMs, final double bandwidthMbps) {
        final var edge = new LinkedHashMap<String, Object>();
        edge.put("source", source);
        edge.put("target", target);
        edge.put("propagation_delay_ms", delayMs);
        edge.put("bandwidth_mbps", bandwidthMbps);
        return edge;
    }

    private static double clamp(final double value, final double min, final double max) {
        return Math.max(min, Math.min(max, value));
    }

    private static void validateServer(final String server) {
        final URI uri = URI.create(server);
        final String scheme = uri.getScheme();
        if (!"http".equalsIgnoreCase(scheme) && !"https".equalsIgnoreCase(scheme)) {
            throw new IllegalArgumentException("Server must be an http/https URL: " + server);
        }
        if (uri.getHost() == null || uri.getHost().isBlank()) {
            throw new IllegalArgumentException("Server URL must include a host: " + server);
        }
    }

    private static String validateScenario(final String scenario) {
        final String normalized = scenario.toLowerCase(Locale.ROOT);
        if (!SUPPORTED_SCENARIOS.contains(normalized)) {
            throw new IllegalArgumentException(
                "Unsupported scenario '" + scenario + "'. Supported scenarios: normal, fault, fault-active."
            );
        }
        return normalized;
    }

    private static void validateCloudletCount(final int cloudletCount) {
        if (cloudletCount <= 0) {
            throw new IllegalArgumentException("cloudletCount must be greater than 0.");
        }
    }

    private static String normalizeRunId(final String runId, final String scenario, final long seed) {
        final String value = runId == null || runId.isBlank()
            ? "dci-" + scenario + "-" + seed + "-" + RUN_ID_TIMESTAMP_FORMAT.format(LocalDateTime.now())
            : runId.strip();
        final String sanitized = value.replaceAll("[^A-Za-z0-9._-]", "-");
        if (sanitized.isBlank()) {
            throw new IllegalArgumentException("runId must contain at least one ASCII letter, digit, dot, underscore, or hyphen.");
        }
        return sanitized;
    }
}
