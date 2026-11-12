export interface GraphNode { id: string; label: string; detail?: string; color?: string }
export interface GraphEdge { id: string; from: string; to: string; label?: string; problem?: boolean }
export function graphLayout(nodes: GraphNode[], edges: GraphEdge[]) {
  const ids = new Set(nodes.map(n => n.id));
  const links = edges.filter(e => ids.has(e.from) && ids.has(e.to));
  const levels = new Map<string, number>();
  // Place shared dependencies after all callers, even when a shorter path exists.
  const incoming = new Map(nodes.map(n => [n.id, links.filter(e => e.to === n.id).length]));
  const ready = nodes.filter(n => incoming.get(n.id) === 0).map(n => n.id);
  ready.forEach(id => levels.set(id, 0));
  for (let i = 0; i < ready.length; i++) {
    for (const edge of links.filter(e => e.from === ready[i])) {
      levels.set(edge.to, Math.max(levels.get(edge.to) ?? 0, levels.get(ready[i])! + 1));
      incoming.set(edge.to, incoming.get(edge.to)! - 1);
      if (incoming.get(edge.to) === 0) ready.push(edge.to);
    }
  }
  // Cyclic and disconnected components retain all nodes and edges.
  const visit = (root: string) => {
    if (levels.has(root)) return;
    levels.set(root, 0);
    const queue = [root];
    for (let i = 0; i < queue.length; i++) for (const e of links.filter(e => e.from === queue[i])) {
      if (!levels.has(e.to)) { levels.set(e.to, levels.get(queue[i])! + 1); queue.push(e.to); }
    }
  };
  nodes.filter(n => !links.some(e => e.to === n.id)).forEach(n => visit(n.id));
  nodes.forEach(n => visit(n.id));
  const groups = [...new Set(levels.values())].sort((a,b) => a-b).map(level => nodes.filter(n => levels.get(n.id) === level));
  const positions = new Map<string, { x: number; y: number; width: number; height: number }>();
  let x = 48;
  const height = Math.max(260, ...groups.map(g => g.length * 112 + 64));
  for (const group of groups) {
    let width = 180;
    group.forEach((node, i) => {
      const w = Math.max(180, node.label.length * 9 + 52, (node.detail?.length ?? 0) * 7.5 + 32);
      positions.set(node.id, { x, y: (height - group.length * 112) / 2 + i * 112, width: w, height: 72 });
      width = Math.max(width, w);
    });
    x += width + 130;
  }
  return { positions, width: Math.max(400, x - 82), height, edges: links };
}
export function zoomAt(camera: { x:number; y:number; scale:number }, point:{x:number;y:number}, scale:number) {
  const next = Math.max(.15, Math.min(3, scale));
  return { scale:next, x:point.x-(point.x-camera.x)*next/camera.scale, y:point.y-(point.y-camera.y)*next/camera.scale };
}
