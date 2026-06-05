import { renderTopology as renderTopologyCanvas } from "../topology.js";

export function initTopology() {
  document.getElementById("page-topology").innerHTML = `
    <div class="page-head">
      <div>
        <h1 class="page-title">网络拓扑</h1>
        <p class="page-subtitle">DCI 跨数据中心网络拓扑与数据中心内部 Spine-Leaf 结构。</p>
      </div>
    </div>
    <section class="grid topology-layout">
      <article class="card topology-map-card">
        <h2 class="card-title title-topology">交互式网络拓扑</h2>
        <div id="topologyCanvas" class="topology-canvas"></div>
        <section id="pathMetrics" class="grid path-metrics"></section>
      </article>
      <aside class="card">
        <h2 class="card-title title-topology">节点 / 链路详情</h2>
        <div id="topologyDetailPanel"></div>
      </aside>
    </section>`;
}

export function renderTopology(report) {
  renderTopologyCanvas(report, document.getElementById("topologyCanvas"));
}
