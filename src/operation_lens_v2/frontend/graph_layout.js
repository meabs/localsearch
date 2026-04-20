(function () {
  const STORAGE_KEY = "lens:v1:graph.layout";

  const registry = {
    fcose: { name: "fcose", quality: "proof", animate: false, fit: true, randomize: false, nodeSeparation: 120, idealEdgeLength: 140 },
    "cose-bilkent": { name: "cose-bilkent", animate: "end", fit: true, randomize: false, nodeRepulsion: 4500, idealEdgeLength: 120 },
    dagre: { name: "dagre", rankDir: "TB", nodeSep: 50, edgeSep: 16, rankSep: 64, fit: true, animate: false },
    concentric: { name: "concentric", fit: true, animate: false, minNodeSpacing: 18 },
    circle: { name: "circle", fit: true, animate: false, padding: 32 },
    breadthfirst: { name: "breadthfirst", fit: true, animate: false, directed: true, padding: 32 },
  };

  function load() {
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      return JSON.parse(raw);
    } catch (_) {
      return null;
    }
  }

  function save(state) {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (_) {}
  }

  window.LensGraphLayout = { registry, load, save };
})();
