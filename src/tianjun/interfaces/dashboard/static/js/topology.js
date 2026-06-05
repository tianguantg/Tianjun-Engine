import { escapeHtml } from "./utils.js";

let activeTopologyKey = "global";
let selectedDetail = null;
let resizeHandler = null;
let latestTopologyReport = null;

const schedulerStatus = {
  task: "inference-task-027",
  source: "User-Access",
  target: "DC2 / 深圳资源区",
  strategy: "延迟优先 + 负载均衡",
  link: "正常",
  gnn: "0.91",
};

const globalTopology = {
  key: "global",
  id: "global",
  kind: "global",
  title: "DCI 跨数据中心网络拓扑",
  subtitle: "天钧引擎资源调度链路：接入、边界、PE、骨干与数据中心资源池",
  currentRoute: ["user-access", "pe3", "p-core-b", "p-core-a", "pe1", "border1", "dc1"],
  currentPathText: "当前任务调度路径：User-Access → DC1 → 北京计算集群 / VM-02",
  layers: [
    { id: "access", label: "接入层", y: 10 },
    { id: "border", label: "边界层", y: 31 },
    { id: "pe", label: "PE层", y: 52 },
    { id: "dc", label: "数据中心层", y: 80 },
  ],
  nodes: [
    node("user-access", "User-Access", "用户业务接入点", "access", "access", 50, 10, "业务请求入口与调度流量接入", "3ms · 10Gbps", "正常"),
    node("border1", "Border1", "DC1 侧出口路由", "border", "border", 23, 27, "东部数据中心边界出口", "40Gbps · 0.5~0.7ms", "正常"),
    node("border2", "Border2", "DC2 侧入口路由", "border", "border", 72, 27, "华南数据中心边界入口", "40Gbps · 0.5~0.7ms", "正常"),
    node("pe1", "PE1", "DC1 侧 MPLS VPN 边缘", "pe", "pe", 23, 44, "Provider Edge 边缘路由器", "40Gbps", "正常"),
    node("pe3", "PE3（枢纽）", "DC2/DC3 侧 VPN + 分叉", "pe", "pe", 72, 44, "跨域 VPN 枢纽与分支调度入口", "20Gbps", "正常"),
    node("p-core-a", "P-Core-A", "MPLS 标签交换 · 6ms", "core", "core", 40, 61, "Provider Core 骨干路由器", "10Gbps · 6ms", "正常"),
    node("p-core-b", "P-Core-B", "MPLS 标签交换 · 6ms", "core", "core", 58, 61, "Provider Core 骨干路由器", "10Gbps · 6ms", "拥塞"),
    node("dc1", "DC1（东部）", "北京 · 杭州 · 8 节点", "dc", "dc", 23, 82, "东部资源池，可下钻查看内部调度资源", "40Gbps · 0.5~0.7ms", "正常", "dc1", { region: "东部", zones: 2, vmTotal: 8, avgCpu: 42, tasks: 14, scheduleState: "可调度" }),
    node("dc2", "DC2（华南）", "广州 · 深圳 · 8 节点", "dc", "dc", 72, 82, "华南资源池，可下钻查看内部调度资源", "40Gbps", "正常", "dc2", { region: "华南", zones: 2, vmTotal: 8, avgCpu: 58, tasks: 18, scheduleState: "调度中" }),
    node("border3", "Border3", "DC3 分支边界 · 1.0ms", "border", "border", 87, 61, "西部数据中心分支入口路由", "1.0ms", "正常"),
    node("dc3", "DC3（西部）", "成都 · 重庆 · 8 节点", "dc", "dc", 87, 82, "西部资源池，可下钻查看内部调度资源", "分支链路 · Border3 1.0ms", "正常", "dc3", { region: "西部", zones: 2, vmTotal: 8, avgCpu: 47, tasks: 13, scheduleState: "可调度" }),
  ],
  links: [
    link("user-access", "pe3", "access", "3ms · 10Gbps", "接入链路", { showLabel: true, labelAnchor: "mid" }),
    link("dc1", "border1", "main", "40Gbps · 0.5~0.7ms", "DC 出入口链路", { showLabel: true, labelAnchor: "left" }),
    link("border1", "pe1", "main", "40Gbps · 0.5~0.7ms", "边界到 PE 主链路", { showLabel: false }),
    link("pe1", "p-core-a", "main", "40Gbps", "MPLS 主链路", { showLabel: false }),
    link("p-core-a", "p-core-b", "main bottleneck", "10Gbps DCI瓶颈", "骨干瓶颈链路", { showLabel: true, labelAnchor: "below" }),
    link("p-core-b", "pe3", "main", "40Gbps", "MPLS 主链路", { showLabel: false }),
    link("pe3", "border2", "main", "20Gbps · 1.2ms", "PE 到边界主链路", { showLabel: false }),
    link("border2", "dc2", "main", "40Gbps · 0.5~0.7ms", "DC 出入口链路", { showLabel: true, labelAnchor: "right" }),
    link("pe3", "border3", "branch", "Border3 · 1.0ms", "DC3 分支链路", { showLabel: false }),
    link("border3", "dc3", "branch", "分支链路", "西部数据中心分支链路", { showLabel: false }),
  ],
  footer: [
    "点击任意数据中心可查看内部节点",
    "总骨干时延：PE1 → PE3 ≈ 13.2ms",
    "DCI 瓶颈：10Gbps（P-Core 段）",
  ],
};

normalizeGlobalTopology();

const dcTopologies = {
  dc1: makeDcTopology({
    key: "dc1",
    dcName: "DC1",
    title: "DC1 数据中心内部拓扑",
    subtitle: "东部数据中心 Spine-Leaf Fabric 与服务支撑拓扑",
    region: "东部",
    border: "Border1",
    pe: "PE1",
    gateway: "DC1-GW",
    zones: [
      zone("beijing", "北京资源区", ["Leaf-BJ-1", "Leaf-BJ-2"], "北京计算集群", "正在调度", "scheduling", 46, 54, 7),
      zone("hangzhou", "杭州资源区", ["Leaf-HZ-1", "Leaf-HZ-2"], "杭州计算集群", "可调度", "ok", 38, 49, 5),
    ],
    path: "User-Access → DC1 → 北京计算集群 / VM-02",
    internalPath: "DC1-GW → Spine-A → Fabric Bus → Leaf-BJ-1 → 北京计算集群 → VM-02",
    routeLeaf: "leaf-a",
    routeCluster: "cluster-a",
    routeVm: "VM-02",
  }),
  dc2: makeDcTopology({
    key: "dc2",
    dcName: "DC2",
    title: "DC2 数据中心内部拓扑",
    subtitle: "华南数据中心 Spine-Leaf Fabric 与服务支撑拓扑",
    region: "华南",
    border: "Border2",
    pe: "PE3",
    gateway: "DC2-GW",
    zones: [
      zone("guangzhou", "广州资源区", ["Leaf-GZ-1", "Leaf-GZ-2"], "广州计算集群", "高负载", "congested", 76, 68, 13),
      zone("shenzhen", "深圳资源区", ["Leaf-SZ-1", "Leaf-SZ-2"], "深圳计算集群", "正在调度", "scheduling", 39, 48, 5),
    ],
    path: "User-Access → DC2 → 深圳计算集群 / VM-02",
    internalPath: "DC2-GW → Spine-A → Fabric Bus → Leaf-SZ-1 → 深圳计算集群 → VM-02",
    routeLeaf: "leaf-c",
    routeCluster: "cluster-b",
    routeVm: "VM-02",
  }),
  dc3: makeDcTopology({
    key: "dc3",
    dcName: "DC3",
    title: "DC3 数据中心内部拓扑",
    subtitle: "西部数据中心 Spine-Leaf Fabric 与服务支撑拓扑",
    region: "西部",
    border: "Border3",
    pe: "PE3",
    gateway: "DC3-GW",
    zones: [
      zone("chengdu", "成都资源区", ["Leaf-CD-1", "Leaf-CD-2"], "成都计算集群", "正在调度", "scheduling", 58, 51, 9),
      zone("chongqing", "重庆资源区", ["Leaf-CQ-1", "Leaf-CQ-2"], "重庆计算集群", "可调度", "ok", 34, 45, 4),
    ],
    path: "User-Access → DC3 → 成都计算集群 / VM-02",
    internalPath: "DC3-GW → Spine-A → Fabric Bus → Leaf-CD-1 → 成都计算集群 → VM-02",
    routeLeaf: "leaf-a",
    routeCluster: "cluster-a",
    routeVm: "VM-02",
  }),
};

function node(id, name, subtitle, layer, type, x, y, role, qos, status, drilldown = null, extra = {}) {
  return { id, name, subtitle, layer, type, x, y, role, qos, status, drilldown, ...extra };
}

function normalizeGlobalTopology() {
  globalTopology.nodes = globalTopology.nodes.filter((item) => !["p-core-a", "p-core-b"].includes(item.id));
  if (!globalTopology.nodes.some((item) => item.id === "pe2")) {
    globalTopology.nodes.push(node("pe2", "PE2", "DC2 侧 MPLS VPN 边缘", "pe", "pe", 70, 44, "DC2 独立 Provider Edge 边缘路由器", "40Gbps", "正常"));
  }
  globalTopology.layers = globalTopology.layers.filter((item) => item.id !== "core");
  const positions = {
    "user-access": { x: 50, y: 11 },
    border1: { x: 28, y: 31, egress: "出口 A" },
    pe1: { x: 28, y: 52 },
    dc1: { x: 28, y: 82 },
    border2: { x: 50, y: 31, egress: "出口 B" },
    pe2: { x: 50, y: 52 },
    dc2: { x: 50, y: 82 },
    border3: { x: 72, y: 31, egress: "分支出口" },
    pe3: { x: 72, y: 52 },
    dc3: { x: 72, y: 82 },
  };
  for (const item of globalTopology.nodes) {
    if (positions[item.id]) Object.assign(item, positions[item.id]);
  }

  globalTopology.currentRoute = ["user-access", "border1", "pe1", "dc1"];
  globalTopology.links = [
    link("user-access", "border1", "access", "接入链路", "用户业务接入出口 A", { showLabel: false }),
    link("user-access", "border2", "access", "3ms · 10Gbps", "用户业务接入出口 B", { showLabel: true, labelAnchor: "mid" }),
    link("user-access", "border3", "access", "接入链路", "用户业务接入分支出口", { showLabel: false }),
    link("border1", "pe1", "main", "40Gbps · 0.5~0.7ms", "出口 A 到 PE1", { showLabel: true, labelAnchor: "left" }),
    link("pe1", "dc1", "main", "40Gbps", "PE1 到 DC1 资源池", { showLabel: false }),
    link("border2", "pe2", "main", "40Gbps · 0.5~0.7ms", "出口 B 到 PE2", { showLabel: false }),
    link("pe2", "dc2", "main", "40Gbps · 0.5~0.7ms", "PE2 到 DC2 资源池", { showLabel: true, labelAnchor: "right" }),
    link("border3", "pe3", "main", "40Gbps · 0.5~0.7ms", "分支出口到 PE3", { showLabel: false }),
    link("pe3", "dc3", "main", "40Gbps", "PE3 到 DC3 西部资源池", { showLabel: false }),
    link("pe1", "pe2", "main", "VPN 互联", "PE1 与 PE2 跨域 VPN 互联", { showLabel: true, labelAnchor: "below" }),
    link("pe2", "pe3", "main", "VPN 分支互联", "PE2 与 PE3 分支 VPN 互联", { showLabel: true, labelAnchor: "below" }),
  ];
  globalTopology.footer = [
    "点击任意数据中心可查看内部节点",
    "跨域互联：PE1 ↔ PE2 ↔ PE3 VPN",
    "出口结构：Border1/2/3 分别接入 PE1/2/3",
  ];
}

function link(source, target, type, label, role, extra = {}) {
  return { id: `${source}-${target}`, source, target, type, label, role, bandwidth: bandwidthOf(label), latency: latencyOf(label), jitter: "0.3ms", packetLoss: "0.02%", stability: "0.91", ...extra };
}

function zone(id, name, leaves, cluster, scheduleState, status, cpu, memory, tasks) {
  return { id, name, leaves, cluster, scheduleState, status, cpu, memory, tasks, vmCount: 4 };
}

function makeDcTopology(config) {
  const [leftZone, rightZone] = config.zones;
  const leaves = [...leftZone.leaves, ...rightZone.leaves];
  const nodes = [
    node("border", config.border, `${config.dcName} 侧出口路由`, "entry", "border", null, null, "数据中心边界入口", "40Gbps · 0.5~0.7ms", "正常"),
    node("pe", config.pe, `${config.dcName} 侧 MPLS VPN 边缘`, "entry", "pe", null, null, "Provider Edge 边缘路由器", "40Gbps · 0.5ms", "正常"),
    node("gw", config.gateway, "数据中心网关 / VRF 接入", "gateway", "gateway", null, null, "VRF 与 Fabric 接入网关", "40Gbps · 0.5ms", "正常"),
    node("spine-a", "Spine-A", "Fabric 骨干交换", "spine", "core", null, null, "Spine 冗余交换", "25Gbps · 0.8ms", "正常"),
    node("spine-b", "Spine-B", "Fabric 骨干交换", "spine", "core", null, null, "Spine 冗余交换", "25Gbps · 0.8ms", "正常"),
    node("leaf-a", leaves[0], leftZone.name, "leaf", "leaf", null, null, "计算接入 Leaf", "25Gbps · 0.8ms", "正常", null, { resourceZone: leftZone.name, health: leftZone.status }),
    node("leaf-b", leaves[1], leftZone.name, "leaf", "leaf", null, null, "计算接入 Leaf", "25Gbps · 0.8ms", "正常", null, { resourceZone: leftZone.name, health: "ok" }),
    node("leaf-c", leaves[2], rightZone.name, "leaf", "leaf", null, null, "计算接入 Leaf", "25Gbps · 0.8ms", "正常", null, { resourceZone: rightZone.name, health: rightZone.status }),
    node("leaf-d", leaves[3], rightZone.name, "leaf", "leaf", null, null, "计算接入 Leaf", "25Gbps · 0.8ms", "正常", null, { resourceZone: rightZone.name, health: "ok" }),
    node("cluster-a", leftZone.cluster, `${leftZone.vmCount} 个 VM 节点`, "compute", "dc", null, null, `${leftZone.name}算力资源池`, "25Gbps · 0.8ms", leftZone.scheduleState, null, { resourceZone: leftZone.name, metrics: leftZone, health: leftZone.status }),
    node("cluster-b", rightZone.cluster, `${rightZone.vmCount} 个 VM 节点`, "compute", "dc", null, null, `${rightZone.name}算力资源池`, "25Gbps · 0.8ms", rightZone.scheduleState, null, { resourceZone: rightZone.name, metrics: rightZone, health: rightZone.status }),
    node("storage", "存储", "共享数据卷", "service", "support", null, null, "任务输入输出与共享卷", "服务支撑链路", "正常"),
    node("scheduler", "调度控制", "Tianjun / Hermes", "service", "support", null, null, "策略生成、候选节点评估与调度闭环", "控制面链路", "正常", null, {
      scheduler: {
        strategy: "延迟优先 + 负载均衡",
        candidates: 8,
        assignedTasks: leftZone.tasks + rightZone.tasks,
        latestPath: config.path,
        gnnScore: "0.91",
        avoidance: "已避开拥塞链路与高负载 VM",
      },
    }),
    node("monitor", "监控管理", "监控 / 日志 / OAM", "service", "support", null, null, "资源观测、日志采集与故障隔离", "服务支撑链路", "正常"),
  ];

  return {
    key: config.key,
    id: config.key,
    kind: "dc",
    dcName: config.dcName,
    title: config.title,
    subtitle: config.subtitle,
    region: config.region,
    zones: config.zones,
    routeLeaf: config.routeLeaf,
    routeCluster: config.routeCluster,
    routeVm: config.routeVm,
    currentPath: config.path,
    internalPath: config.internalPath,
    currentRoute: ["gw", "spine-a", "fabric-bus", config.routeLeaf, config.routeCluster, "vm-02"],
    layers: [
      { id: "gateway", label: "数据中心网关层" },
      { id: "spine", label: "Spine 骨干层" },
      { id: "leaf", label: "Leaf 接入层" },
      { id: "service", label: "服务与支撑层" },
    ],
    nodes,
    links: [
      link("border", "pe", "main", "40Gbps · 0.5~0.7ms", "入口主链路"),
      link("pe", "gw", "main", "40Gbps · 0.5ms", "网关接入链路"),
      link("gw", "spine-a", "main route", "25Gbps · 0.8ms", "当前调度路径"),
      link("gw", "spine-b", "fabric", "25Gbps · 0.8ms", "Fabric 冗余链路"),
      link("spine-a", "fabric-bus", "fabric route", "本地 Fabric：25Gbps · 0.8ms", "Fabric 总线"),
      link("spine-b", "fabric-bus", "fabric", "本地 Fabric：25Gbps · 0.8ms", "Fabric 总线"),
      link("fabric-bus", "leaf-a", "fabric route", "25Gbps", "Fabric 到 Leaf"),
      link("fabric-bus", "leaf-b", "fabric", "25Gbps", "Fabric 到 Leaf"),
      link("fabric-bus", "leaf-c", "fabric route", "25Gbps", "Fabric 到 Leaf"),
      link("fabric-bus", "leaf-d", "fabric", "25Gbps", "Fabric 到 Leaf"),
      link("leaf-a", "cluster-a", "main route", "25Gbps", "计算接入链路"),
      link("leaf-b", "cluster-a", "main", "25Gbps", "计算接入链路"),
      link("leaf-c", "cluster-b", "main route", "25Gbps", "计算接入链路"),
      link("leaf-d", "cluster-b", "main", "25Gbps", "计算接入链路"),
      link("cluster-a", "service-bus", "support", "服务支撑总线", "支撑 / 管理链路"),
      link("cluster-b", "service-bus", "support", "服务支撑总线", "支撑 / 管理链路"),
      link("service-bus", "storage", "support", "服务链路", "支撑 / 管理链路"),
      link("service-bus", "scheduler", "support route", "控制链路", "当前调度控制链路"),
      link("service-bus", "monitor", "support", "服务链路", "支撑 / 管理链路"),
    ],
    footer: [
      `当前任务调度路径：${config.path}`,
      `${config.dcName} 摘要：${leftZone.name.replace("资源区", "")} / ${rightZone.name.replace("资源区", "")}双资源区，8 个 VM 节点，Fabric 25Gbps，局部时延约 0.8ms`,
      "图例：青色高亮为当前路径；绿色正常；黄色拥塞；蓝色正在调度；红色故障隔离",
    ],
  };
}

function currentTopology() {
  return activeTopologyKey === "global" ? globalTopology : dcTopologies[activeTopologyKey] ?? globalTopology;
}

function nodeById(topology, id) {
  return topology.nodes.find((item) => item.id === id);
}

function linkById(topology, id) {
  return topology.links.find((item) => item.id === id);
}

function layerLabel(topology, id) {
  return topology.layers.find((layer) => layer.id === id)?.label ?? "--";
}

function iconFor(type) {
  return {
    access: "⇢",
    border: "✣",
    pe: "↔",
    core: "⌁",
    gateway: "▣",
    dc: "▦",
    leaf: "▤",
    support: "□",
  }[type] ?? "●";
}

function renderStatusBar() {
  return `<div class="topology-statusbar" aria-label="当前调度状态">
    ${statusBadge("当前任务", schedulerStatus.task, "scheduling")}
    ${statusBadge("源区域", schedulerStatus.source, "neutral")}
    ${statusBadge("目标区域", schedulerStatus.target, "scheduling")}
    ${statusBadge("调度策略", schedulerStatus.strategy, "neutral")}
    ${statusBadge("链路状态", schedulerStatus.link, "ok")}
    ${statusBadge("GNN 稳定性评分", schedulerStatus.gnn, "ok")}
  </div>`;
}

function statusBadge(label, value, tone) {
  return `<span class="topology-status-badge ${escapeHtml(tone)}"><small>${escapeHtml(label)}</small><b>${escapeHtml(value)}</b></span>`;
}

function isRouteNode(topology, id) {
  return topology.currentRoute?.includes(id);
}

function isRouteLink(topology, item) {
  const route = topology.currentRoute ?? [];
  return route.some((id, index) => {
    const next = route[index + 1];
    return next && ((item.source === id && item.target === next) || (item.source === next && item.target === id));
  }) || item.type.includes("route");
}

function renderGlobalLinks(topology) {
  return topology.links.map((item) => {
    const selected = selectedDetail?.kind === "link" && selectedDetail.id === item.id;
    const route = isRouteLink(topology, item);
    return `<g class="topology-link-group ${selected ? "selected" : ""}" data-link="${escapeHtml(item.id)}">
      <path class="network-link ${escapeHtml(item.type)} ${route ? "route" : ""}" data-link-path="${escapeHtml(item.id)}"></path>
    </g>`;
  }).join("");
}

function renderGlobalLinkLabels(topology) {
  return topology.links.filter((item) => item.showLabel).map((item) => {
    const selected = selectedDetail?.kind === "link" && selectedDetail.id === item.id;
    return `<button class="global-link-label ${escapeHtml(item.type)} ${selected ? "selected" : ""}" type="button" data-link="${escapeHtml(item.id)}" data-link-label="${escapeHtml(item.id)}">
      ${escapeHtml(item.label)}
    </button>`;
  }).join("");
}

function renderGlobalNode(topology, item) {
  const status = statusClass(item.status);
  const drill = item.drilldown ? `<span class="node-drill">查看内部拓扑</span>` : "";
  const egress = item.egress ? `<span class="egress-badge">${escapeHtml(item.egress)}</span>` : "";
  return `<button class="network-node ${escapeHtml(item.type)} ${status} ${isRouteNode(topology, item.id) ? "route-node" : ""}" data-node="${escapeHtml(item.id)}" style="left:${item.x}%; top:${item.y}%;" type="button">
    ${drill}
    ${egress}
    <span class="node-icon">${iconFor(item.type)}</span>
    <span class="node-copy"><b>${escapeHtml(item.name)}</b><small>${escapeHtml(item.subtitle)}</small></span>
  </button>`;
}

function renderGlobalLayers(topology) {
  return topology.layers.map((layer) => `<div class="topology-layer-label" style="top:${layer.y}%;">${escapeHtml(layer.label)}</div>
    <div class="topology-layer-line" style="top:${layer.y + 8}%"></div>`).join("");
}

function renderGlobalScene(topology) {
  return `<div class="topology-toolbar">
      <div>
        <h3>${escapeHtml(topology.title)}</h3>
        <p>${escapeHtml(topology.subtitle)}</p>
      </div>
    </div>
    ${renderStatusBar()}
    <div class="network-stage" data-global-stage>
      ${renderGlobalLayers(topology)}
      <svg class="network-links" data-link-svg aria-hidden="true">${renderGlobalLinks(topology)}</svg>
      <div class="global-link-labels">${renderGlobalLinkLabels(topology)}</div>
      <div class="network-nodes">${topology.nodes.map((item) => renderGlobalNode(topology, item)).join("")}</div>
    </div>
    <div class="global-route-banner">${escapeHtml(topology.currentPathText)}</div>
    ${renderFooter(topology)}`;
}

function drawMeasuredGlobalLinks(container, topology) {
  const stage = container.querySelector("[data-global-stage]");
  const svg = container.querySelector("[data-link-svg]");
  if (!stage || !svg) return;
  const rect = stage.getBoundingClientRect();
  svg.setAttribute("viewBox", `0 0 ${rect.width} ${rect.height}`);
  svg.setAttribute("width", rect.width);
  svg.setAttribute("height", rect.height);

  topology.links.forEach((item) => {
    const pathEl = svg.querySelector(`[data-link-path="${CSS.escape(item.id)}"]`);
    const labelEl = stage.querySelector(`[data-link-label="${CSS.escape(item.id)}"]`);
    const sourceEl = stage.querySelector(`[data-node="${CSS.escape(item.source)}"]`);
    const targetEl = stage.querySelector(`[data-node="${CSS.escape(item.target)}"]`);
    if (!pathEl || !sourceEl || !targetEl) return;
    const d = measuredPath(item, sourceEl, targetEl, rect);
    pathEl.setAttribute("d", d.path);
    if (labelEl) {
      labelEl.style.left = `${d.label.x}px`;
      labelEl.style.top = `${d.label.y}px`;
    }
  });
}

function measuredPath(item, sourceEl, targetEl, stageRect) {
  const s = box(sourceEl, stageRect);
  const t = box(targetEl, stageRect);
  const pair = `${item.source}-${item.target}`;
  const reversePair = `${item.target}-${item.source}`;
  const verticalPairs = new Set(["border1-pe1", "pe1-dc1", "border2-pe2", "pe2-dc2", "border3-pe3", "pe3-dc3"]);
  const horizontalPairs = new Set(["pe1-pe2", "pe2-pe3"]);
  const accessPairs = new Set(["user-access-border1", "user-access-border2", "user-access-border3"]);

  if (accessPairs.has(pair) || accessPairs.has(reversePair)) {
    const sourceIsAccess = item.source === "user-access";
    const accessBox = sourceIsAccess ? s : t;
    const borderBox = sourceIsAccess ? t : s;
    const start = bottom(accessBox);
    const end = top(borderBox);
    const midY = start.y + Math.max(20, (end.y - start.y) * 0.48);
    const path = `M ${start.x} ${start.y} C ${start.x} ${midY}, ${end.x} ${midY}, ${end.x} ${end.y}`;
    return { path, label: { x: (start.x + end.x) / 2, y: midY - 10 } };
  }

  if (verticalPairs.has(pair) || verticalPairs.has(reversePair)) {
    const sourceAboveTarget = s.y <= t.y;
    const start = sourceAboveTarget ? bottom(s) : top(s);
    const end = sourceAboveTarget ? top(t) : bottom(t);
    const path = `M ${start.x} ${start.y} L ${end.x} ${end.y}`;
    return { path, label: labelPoint(item, start, end, start.x) };
  }

  if (horizontalPairs.has(pair) || horizontalPairs.has(reversePair)) {
    const sourceLeftOfTarget = s.x < t.x;
    const start = sourceLeftOfTarget ? right(s) : left(s);
    const end = sourceLeftOfTarget ? left(t) : right(t);
    return { path: `M ${start.x} ${start.y} L ${end.x} ${end.y}`, label: { x: (start.x + end.x) / 2, y: start.y + 42 } };
  }

  const start = smartAnchor(s, t);
  const end = smartAnchor(t, s);
  const midX = (start.x + end.x) / 2;
  const bendY = item.type.includes("access") ? Math.min(start.y, end.y) + 70 : (start.y + end.y) / 2;
  const path = item.type.includes("branch")
    ? `M ${start.x} ${start.y} C ${midX + 40} ${start.y}, ${midX + 40} ${end.y}, ${end.x} ${end.y}`
    : `M ${start.x} ${start.y} C ${midX} ${bendY}, ${midX} ${bendY}, ${end.x} ${end.y}`;
  return { path, label: { x: (start.x + end.x) / 2 + (item.labelAnchor === "right" ? 48 : 0), y: (start.y + end.y) / 2 - 16 } };
}

function box(element, stageRect) {
  const rect = element.getBoundingClientRect();
  return {
    left: rect.left - stageRect.left,
    right: rect.right - stageRect.left,
    top: rect.top - stageRect.top,
    bottom: rect.bottom - stageRect.top,
    x: rect.left - stageRect.left + rect.width / 2,
    y: rect.top - stageRect.top + rect.height / 2,
  };
}

function top(b) { return { x: b.x, y: b.top }; }
function bottom(b) { return { x: b.x, y: b.bottom }; }
function left(b) { return { x: b.left, y: b.y }; }
function right(b) { return { x: b.right, y: b.y }; }

function smartAnchor(from, to) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  if (Math.abs(dx) > Math.abs(dy)) return dx > 0 ? right(from) : left(from);
  return dy > 0 ? bottom(from) : top(from);
}

function labelPoint(item, start, end, lane) {
  if (item.labelAnchor === "left") return { x: start.x - 62, y: (start.y + end.y) / 2 };
  if (item.labelAnchor === "right") return { x: lane + 28, y: (start.y + end.y) / 2 };
  if (item.labelAnchor === "below") return { x: (start.x + end.x) / 2, y: start.y + 42 };
  return { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 };
}

function renderInternalScene(topology) {
  return `<div class="dc-topology-head">
    <div>
      <div class="dc-breadcrumb">全局拓扑 / <b>${escapeHtml(topology.title)}</b></div>
      <h3>${escapeHtml(topology.title)}</h3>
      <p>${escapeHtml(topology.subtitle)}</p>
    </div>
    <button class="topology-back" type="button" data-back-global>返回全局拓扑</button>
  </div>
  ${renderStatusBar()}
  <div class="dc-internal-stage" aria-label="${escapeHtml(topology.title)}">
    ${renderDcLayer("gateway", "数据中心网关层", renderGatewayBridge(topology))}
    ${renderDcLayer("spine", "Spine 骨干层", renderSpineRow(topology))}
    ${renderDcLayer("leaf", "Leaf 接入层", renderFabricAndZones(topology))}
    ${renderDcLayer("service", "服务与支撑层", renderSupportBus(topology))}
  </div>
  ${renderFooter(topology)}`;
}

function renderDcLayer(id, label, body) {
  return `<section class="dc-layer-row dc-layer-${escapeHtml(id)}">
    <div class="dc-layer-label">${escapeHtml(label)}</div>
    <div class="dc-layer-content">${body}</div>
  </section>`;
}

function renderEntryStack(topology) {
  return `<div class="dc-entry-stack">
    ${renderDcNode(topology, "border")}
    ${renderVerticalLink(topology, "border-pe", "right")}
    ${renderDcNode(topology, "pe")}
  </div>`;
}

function renderGatewayBridge(topology) {
  return `<div class="dc-gateway-stack">
    ${renderDcNode(topology, "gw")}
  </div>`;
}

function renderSpineRow(topology) {
  return `<div class="dc-spine-wrap">
    <button class="dc-link-stem route" type="button" data-link="gw-spine-a" title="DC 网关到 Spine-A" aria-label="DC 网关到 Spine-A"></button>
    <button class="dc-link-stem" type="button" data-link="gw-spine-b" title="DC 网关到 Spine-B" aria-label="DC 网关到 Spine-B"></button>
    <div class="dc-spine-grid">
      ${renderDcNode(topology, "spine-a")}
      ${renderDcNode(topology, "spine-b")}
    </div>
  </div>`;
}

function renderFabricAndZones(topology) {
  const [leftZone, rightZone] = topology.zones;
  return `<div class="dc-fabric-wrap">
    <button class="fabric-bus ${selectedClass("spine-a-fabric-bus")}" type="button" data-link="spine-a-fabric-bus" title="Spine-A / Spine-B 双上行冗余连接全部 Leaf 节点">
      <span>本地交换网络：25Gbps · 0.8ms</span>
    </button>
    <div class="dc-zone-grid">
      ${renderZone(topology, leftZone, "left")}
      ${renderZone(topology, rightZone, "right")}
    </div>
  </div>`;
}

function renderZone(topology, zoneInfo, side) {
  const leafIds = side === "left" ? ["leaf-a", "leaf-b"] : ["leaf-c", "leaf-d"];
  const clusterId = side === "left" ? "cluster-a" : "cluster-b";
  const isRoute = topology.routeCluster === clusterId;
  return `<section class="resource-zone ${isRoute ? "route-zone" : ""}">
    <h4>${escapeHtml(zoneInfo.name)}</h4>
    <div class="zone-leaf-grid">
      ${leafIds.map((id) => `<div class="leaf-slot">${renderDownlink(topology, `fabric-bus-${id}`, id === topology.routeLeaf)}${renderDcNode(topology, id, "compact")}</div>`).join("")}
    </div>
    ${renderClusterCard(topology, clusterId, zoneInfo)}
  </section>`;
}

function renderClusterCard(topology, id, zoneInfo) {
  const item = nodeById(topology, id);
  const route = id === topology.routeCluster;
  const tooltip = `${item.name} / CPU ${zoneInfo.cpu}% / 内存 ${zoneInfo.memory}% / 任务 ${zoneInfo.tasks}`;
  return `<article class="compute-card ${statusClass(zoneInfo.scheduleState)} ${route ? "route-node" : ""}" role="button" tabindex="0" data-node="${escapeHtml(id)}" title="${escapeHtml(tooltip)}">
    <span class="status-dot ${escapeHtml(zoneInfo.status)}"></span>
    <span class="compute-title">${escapeHtml(item.name)}</span>
    <span class="compute-metrics">
      <b>CPU ${escapeHtml(zoneInfo.cpu)}%</b>
      <b>内存 ${escapeHtml(zoneInfo.memory)}%</b>
      <b>任务 ${escapeHtml(zoneInfo.tasks)}</b>
    </span>
    <span class="vm-grid">
      ${Array.from({ length: zoneInfo.vmCount }, (_, index) => renderVmNode(topology, id, zoneInfo, index, route)).join("")}
    </span>
  </article>`;
}

function renderVmNode(topology, clusterId, zoneInfo, index, routeCluster) {
  const vmName = `VM-${String(index + 1).padStart(2, "0")}`;
  const vmId = `${clusterId}-vm-${String(index + 1).padStart(2, "0")}`;
  const active = routeCluster && vmName === topology.routeVm;
  const selected = selectedDetail?.kind === "vm" && selectedDetail.id === vmId;
  const cpu = Math.min(92, Math.max(12, zoneInfo.cpu + (index - 1) * 6));
  const memory = Math.min(90, Math.max(18, zoneInfo.memory + (index % 2 === 0 ? -4 : 5)));
  const state = active ? "正在调度" : cpu > 78 ? "高负载" : "可调度";
  return `<button class="vm-node ${active ? "active" : ""} ${selected ? "selected" : ""}" type="button"
      data-vm="${escapeHtml(vmId)}"
      data-cluster="${escapeHtml(clusterId)}"
      data-zone="${escapeHtml(zoneInfo.name)}"
      data-name="${escapeHtml(vmName)}"
      data-cpu="${cpu}"
      data-memory="${memory}"
      data-state="${escapeHtml(state)}"
      data-task-count="${active ? 1 : Math.max(0, Math.floor(zoneInfo.tasks / zoneInfo.vmCount) - (index % 2))}"
      title="${escapeHtml(`${vmName} / ${zoneInfo.name} / CPU ${cpu}% / 内存 ${memory}% / ${state}`)}">
      ${escapeHtml(vmName)}
    </button>`;
}

function renderSupportBus(topology) {
  return `<div class="support-wrap">
    <div class="support-feed">
      <button class="support-feed-line route" type="button" data-link="cluster-a-service-bus" title="计算集群到服务支撑总线"></button>
      <button class="support-feed-line" type="button" data-link="cluster-b-service-bus" title="计算集群到服务支撑总线"></button>
    </div>
    <button class="service-bus" type="button" data-link="service-bus-scheduler" title="存储 / 调度控制 / 监控管理通过服务支撑总线连通">服务支撑总线</button>
    <div class="support-grid">
      ${["storage", "scheduler", "monitor"].map((id) => `<div class="support-slot">${renderDownlink(topology, `service-bus-${id}`, id === "scheduler")}${renderDcNode(topology, id)}</div>`).join("")}
    </div>
  </div>`;
}

function renderDcNode(topology, id, modifier = "") {
  const item = nodeById(topology, id);
  if (!item) return "";
  const selected = selectedDetail?.kind === "node" && selectedDetail.id === id;
  const route = ["gw", "spine-a", topology.routeLeaf].includes(id);
  const health = item.health ?? statusClass(item.status);
  const tooltip = `${item.name} / ${item.subtitle} / ${item.status}`;
  return `<button class="dc-node ${escapeHtml(item.type)} ${escapeHtml(modifier)} ${statusClass(item.status)} ${route ? "route-node" : ""} ${selected ? "selected" : ""}" type="button" data-node="${escapeHtml(id)}" title="${escapeHtml(tooltip)}">
    <span class="status-dot ${escapeHtml(health)}"></span>
    <span class="node-icon">${iconFor(item.type)}</span>
    <span class="node-copy"><b>${escapeHtml(item.name)}</b><small>${escapeHtml(item.subtitle)}</small></span>
  </button>`;
}

function renderVerticalLink(topology, id, align = "center") {
  const item = linkById(topology, id);
  return `<button class="dc-vertical-link ${escapeHtml(align)} ${selectedClass(id)}" type="button" data-link="${escapeHtml(id)}" title="${escapeHtml(item?.role ?? id)}">
    <span>${escapeHtml(item?.label ?? "")}</span>
  </button>`;
}

function renderDownlink(topology, id, route = false) {
  const item = linkById(topology, id);
  return `<button class="dc-downlink ${route ? "route" : ""} ${selectedClass(id)}" type="button" data-link="${escapeHtml(id)}" title="${escapeHtml(item?.role ?? id)}">
    <span>${escapeHtml(item?.label ?? "")}</span>
  </button>`;
}

function selectedClass(id) {
  return selectedDetail?.kind === "link" && selectedDetail.id === id ? "selected" : "";
}

function statusClass(status) {
  return {
    正常: "ok",
    可调度: "ok",
    拥塞: "congested",
    高负载: "congested",
    故障模拟: "fault",
    故障隔离: "fault",
    正在调度: "scheduling",
    调度中: "scheduling",
  }[status] ?? status ?? "ok";
}

function renderFooter(topology) {
  const legend = topology.kind === "global"
    ? ["实线：主链路", "虚线：分支链路", "点虚线：接入链路", "青色：当前任务调度路径", "PE：Provider Edge", "DC：Data Center", "Border：出口路由"]
    : topology.footer;
  return `<div class="topology-footer ${topology.kind === "dc" ? "dc-footer" : ""}">
    ${topology.kind === "dc"
      ? topology.footer.map((item) => `<div class="topology-facts"><span>${escapeHtml(item)}</span></div>`).join("")
      : `<div class="topology-facts">${topology.footer.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>
         <div class="topology-legend">${legend.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>`}
  </div>`;
}

function renderDetails(topology) {
  if (!selectedDetail) return renderOverviewDetails(topology);

  if (selectedDetail.kind === "vm") return renderVmDetails(selectedDetail);

  if (selectedDetail.kind === "link") {
    const item = linkById(topology, selectedDetail.id);
    const source = nodeById(topology, item?.source);
    const target = nodeById(topology, item?.target);
    return `<div class="topology-detail-card">
      <h3>链路详情</h3>
      ${detailRow("起点", source?.name ?? virtualEndpoint(item?.source))}
      ${detailRow("终点", target?.name ?? virtualEndpoint(item?.target))}
      ${detailRow("链路类型", item?.role)}
      ${detailRow("带宽", item?.bandwidth)}
      ${detailRow("时延", item?.latency)}
      ${detailRow("抖动", item?.jitter)}
      ${detailRow("丢包率", item?.packetLoss)}
      ${detailRow("稳定性评分", item?.stability)}
    </div>`;
  }

  const item = nodeById(topology, selectedDetail.id);
  if (item?.drilldown || item?.type === "dc") return renderDcNodeDetails(item);
  return `<div class="topology-detail-card">
    <h3>节点详情</h3>
    ${detailRow("节点名称", item?.name)}
    ${detailRow("所属层级", layerLabel(topology, item?.layer))}
    ${detailRow("所属区域", item?.resourceZone ?? (topology.kind === "global" ? "全局 DCI" : `${topology.region} / ${topology.dcName}`))}
    ${detailRow("节点职责", item?.role)}
    ${detailRow("带宽 / 时延", item?.qos)}
    ${detailRow("状态", item?.status)}
    ${renderMetricRows(item)}
    ${renderSchedulerRows(item)}
  </div>`;
}

function renderVmDetails(vm) {
  const cluster = nodeById(currentTopology(), vm.clusterId);
  return `<div class="topology-detail-card">
    <h3>VM 节点详情</h3>
    ${detailRow("节点名称", vm.name)}
    ${detailRow("所属集群", cluster?.name ?? vm.clusterId)}
    ${detailRow("所属资源区", vm.zone)}
    ${detailRow("节点职责", "任务执行 / 算力资源实例")}
    ${detailRow("CPU 使用率", `${vm.cpu}%`)}
    ${detailRow("内存使用率", `${vm.memory}%`)}
    ${detailRow("当前任务数", `${vm.taskCount} 个`)}
    ${detailRow("调度状态", vm.state)}
    ${detailRow("推荐状态", vm.state === "高负载" ? "暂不推荐" : "可作为候选执行节点")}
  </div>`;
}

function renderOverviewDetails(topology) {
  return `<div class="topology-detail-card">
    <h3>当前拓扑概览</h3>
    ${detailRow("拓扑视图", topology.title)}
    ${detailRow("当前任务", schedulerStatus.task)}
    ${detailRow("调度策略", schedulerStatus.strategy)}
    ${detailRow("当前路径", topology.kind === "global" ? globalTopology.currentPathText.replace("当前任务调度路径：", "") : topology.currentPath)}
    ${detailRow("链路状态", schedulerStatus.link)}
    ${detailRow("GNN 稳定性评分", schedulerStatus.gnn)}
    <p>点击 DC1 / DC2 / DC3 可进入内部拓扑；点击节点或链路查看更细指标。</p>
  </div>`;
}

function renderDcNodeDetails(item) {
  return `<div class="topology-detail-card">
    <h3>数据中心详情</h3>
    ${detailRow("数据中心名称", item?.name)}
    ${detailRow("所属区域", item?.region ?? item?.subtitle)}
    ${detailRow("资源区数量", item?.zones ? `${item.zones} 个` : "2 个")}
    ${detailRow("VM 总数", item?.vmTotal ? `${item.vmTotal} 个` : "8 个")}
    ${detailRow("平均 CPU 使用率", item?.avgCpu ? `${item.avgCpu}%` : "--")}
    ${detailRow("当前任务数", item?.tasks ? `${item.tasks} 个` : "--")}
    ${detailRow("调度状态", item?.scheduleState ?? item?.status)}
  </div>`;
}

function renderMetricRows(item) {
  if (!item?.metrics) return "";
  const metrics = item.metrics;
  const available = Math.max(0, metrics.vmCount - Math.ceil(metrics.tasks / 5));
  const highLoad = metrics.cpu > 70 ? 2 : metrics.cpu > 55 ? 1 : 0;
  return `${detailRow("VM 数量", `${metrics.vmCount} 个`)}
    ${detailRow("CPU 使用率", `${metrics.cpu}%`)}
    ${detailRow("内存使用率", `${metrics.memory}%`)}
    ${detailRow("当前任务数", `${metrics.tasks} 个`)}
    ${detailRow("可调度 VM", `${available} 个`)}
    ${detailRow("高负载 VM", `${highLoad} 个`)}
    ${detailRow("推荐调度目标", metrics.status === "congested" ? "暂不推荐" : "VM-02")}`;
}

function renderSchedulerRows(item) {
  if (!item?.scheduler) return "";
  const scheduler = item.scheduler;
  return `${detailRow("当前调度策略", scheduler.strategy)}
    ${detailRow("候选节点数量", `${scheduler.candidates} 个`)}
    ${detailRow("已分配任务数量", `${scheduler.assignedTasks} 个`)}
    ${detailRow("最近一次调度路径", scheduler.latestPath)}
    ${detailRow("GNN 拓扑稳定性评分", scheduler.gnnScore)}
    ${detailRow("故障规避状态", scheduler.avoidance)}`;
}

function virtualEndpoint(id) {
  return {
    "fabric-bus": "Fabric Bus",
    "service-bus": "服务支撑总线",
  }[id] ?? id ?? "--";
}

function detailRow(label, value) {
  return `<div class="detail-row"><span>${escapeHtml(label)}</span><b>${escapeHtml(value ?? "--")}</b></div>`;
}

function bandwidthOf(label = "") {
  return label.match(/\d+(?:\.\d+)?Gbps/)?.[0] ?? "--";
}

function latencyOf(label = "") {
  return label.match(/\d+(?:\.\d+)?(?:~\d+(?:\.\d+)?)?ms/)?.[0] ?? "--";
}

function renderScene(topology, container) {
  container.innerHTML = `<section class="network-topology-shell ${topology.kind === "dc" ? "dc-mode" : "global-mode"}">
    ${topology.kind === "dc" ? renderInternalScene(topology) : renderGlobalScene(topology)}
  </section>`;
  if (topology.kind === "global") {
    requestAnimationFrame(() => drawMeasuredGlobalLinks(container, topology));
    if (resizeHandler) window.removeEventListener("resize", resizeHandler);
    resizeHandler = () => drawMeasuredGlobalLinks(container, currentTopology());
    window.addEventListener("resize", resizeHandler);
  }
}

function hasImportedTopology(report) {
  const nodes = Array.isArray(report?.nodes) ? report.nodes : [];
  return nodes.length > 0 || Boolean(report?.physical_topology);
}

function renderTopologyEmpty(container, report) {
  const tickText = report?.tick == null ? "--" : String(report.tick);
  container.innerHTML = `<section class="network-topology-shell topology-empty-shell">
    <div class="topology-empty-state">
      <span class="topology-empty-icon">◎</span>
      <h3>等待仿真节点导入</h3>
      <p>当前 report 中没有在线节点，也没有注册物理拓扑。导入 CloudSimPlus / 仿真节点后，系统会根据真实节点与链路数据生成 DCI 拓扑。</p>
      <div class="topology-empty-meta">
        <span>当前 tick：${escapeHtml(tickText)}</span>
        <span>在线节点：0</span>
        <span>物理拓扑：未注册</span>
      </div>
    </div>
  </section>`;
  if (resizeHandler) {
    window.removeEventListener("resize", resizeHandler);
    resizeHandler = null;
  }
  const detailPanel = document.getElementById("topologyDetailPanel");
  if (detailPanel) {
    detailPanel.innerHTML = `<article class="topology-detail-card">
      <h3>拓扑尚未生成</h3>
      <p>这里不会再使用静态 DC1/DC2/DC3 假数据兜底。请先导入仿真 inventory 或启动仿真节点后再查看全局与数据中心内部拓扑。</p>
      ${detailRow("数据来源", "report.nodes / report.physical_topology")}
      ${detailRow("当前状态", "等待仿真节点导入")}
    </article>`;
  }
}

function bindInteractions(topology, container, detailPanel) {
  container.querySelectorAll("[data-node]").forEach((element) => {
    element.addEventListener("click", (event) => {
      event.stopPropagation();
      const item = nodeById(topology, element.dataset.node);
      if (item?.drilldown) {
        activeTopologyKey = item.drilldown;
        selectedDetail = null;
      } else {
        selectedDetail = { kind: "node", id: element.dataset.node };
      }
      renderTopology(null, container);
    });
  });

  container.querySelectorAll("[data-link]").forEach((element) => {
    element.addEventListener("click", (event) => {
      event.stopPropagation();
      selectedDetail = { kind: "link", id: element.dataset.link };
      renderTopology(null, container);
    });
  });

  container.querySelectorAll("[data-vm]").forEach((element) => {
    element.addEventListener("click", (event) => {
      event.stopPropagation();
      selectedDetail = {
        kind: "vm",
        id: element.dataset.vm,
        clusterId: element.dataset.cluster,
        zone: element.dataset.zone,
        name: element.dataset.name,
        cpu: element.dataset.cpu,
        memory: element.dataset.memory,
        state: element.dataset.state,
        taskCount: element.dataset.taskCount,
      };
      renderTopology(null, container);
    });
  });

  container.querySelector("[data-back-global]")?.addEventListener("click", (event) => {
    event.stopPropagation();
    activeTopologyKey = "global";
    selectedDetail = null;
    renderTopology(null, container);
  });

  container.querySelector(".network-topology-shell")?.addEventListener("click", () => {
    if (!selectedDetail) return;
    selectedDetail = null;
    renderTopology(null, container);
  });

  if (detailPanel) detailPanel.innerHTML = renderDetails(topology);
}

export function renderTopology(_report, container) {
  if (!container) return;
  if (_report !== null && _report !== undefined) latestTopologyReport = _report;
  const report = latestTopologyReport;
  if (!hasImportedTopology(report)) {
    renderTopologyEmpty(container, report);
    const metricsPanel = document.getElementById("pathMetrics");
    if (metricsPanel) metricsPanel.innerHTML = "";
    return;
  }
  const topology = currentTopology();
  const detailPanel = document.getElementById("topologyDetailPanel");
  const metricsPanel = document.getElementById("pathMetrics");
  renderScene(topology, container);
  bindInteractions(topology, container, detailPanel);
  if (metricsPanel) metricsPanel.innerHTML = "";
}
