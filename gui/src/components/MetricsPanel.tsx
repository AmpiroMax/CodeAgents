import { useEffect, useMemo, useRef, useState } from "react";
import {
  fetchMetricsHistory,
  subscribeMetrics,
  type MetricsSnapshot,
} from "../lib/api";

const MAX_POINTS = 600;

/** Tiny canvas-based sparkline. Avoids pulling chart libs for ~40 lines.
 *  Draws a line from oldest (left) to newest (right); a faint baseline
 *  helps differentiate "no data" from "flat zero". */
function Sparkline({
  values,
  color,
  height = 48,
  max,
}: {
  values: number[];
  color: string;
  height?: number;
  max?: number;
}) {
  const ref = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || 280;
    const h = height;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);

    ctx.strokeStyle = "rgba(255,255,255,0.08)";
    ctx.beginPath();
    ctx.moveTo(0, h - 0.5);
    ctx.lineTo(w, h - 0.5);
    ctx.stroke();

    if (values.length < 2) return;
    const peak = Math.max(max ?? 1, ...values, 1);
    const stepX = w / Math.max(1, values.length - 1);
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    values.forEach((v, i) => {
      const x = i * stepX;
      const y = h - (v / peak) * (h - 4) - 2;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }, [values, color, height, max]);

  return <canvas ref={ref} className="metrics-spark" style={{ height }} />;
}

export function MetricsPanel({
  base,
  onClose,
}: {
  base: string;
  onClose: () => void;
}) {
  const [points, setPoints] = useState<MetricsSnapshot[]>([]);

  // Close on ESC. The overlay is the topmost interactive surface while open
  // so we listen on window directly — anything else (composer, palette) is
  // hidden behind the dim background.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  useEffect(() => {
    let mounted = true;
    void fetchMetricsHistory(base).then((h) => {
      if (mounted) setPoints(h.slice(-MAX_POINTS));
    });
    const unsub = subscribeMetrics(base, (snap) => {
      if (!mounted) return;
      setPoints((prev) => {
        const next = prev.length >= MAX_POINTS ? prev.slice(1) : prev.slice();
        next.push(snap);
        return next;
      });
    });
    return () => {
      mounted = false;
      unsub();
    };
  }, [base]);

  const cpu = useMemo(() => points.map((p) => p.cpu_percent || 0), [points]);
  const rss = useMemo(() => points.map((p) => p.rss_mb || 0), [points]);
  const ram = useMemo(
    () => points.map((p) => p.ram_used_percent || 0),
    [points],
  );
  const gpuUtil = useMemo(
    () => points.map((p) => p.gpu?.gpus?.[0]?.utilization_percent || 0),
    [points],
  );

  const latest = points[points.length - 1];
  const psModels =
    latest?.ollama_ps?.models?.map(
      (m) => m.name || m.model || "(unknown)",
    ) ?? [];

  return (
    <div
      className="metrics-overlay"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div className="metrics-panel" onClick={(e) => e.stopPropagation()}>
        <header className="metrics-header">
          <strong>Resources</strong>
          <button type="button" className="metrics-close" onClick={onClose}>
            ×
          </button>
        </header>
        <div className="metrics-body">
          <div className="metrics-row">
            <div className="metrics-label">CPU {cpu.at(-1)?.toFixed(0) ?? 0}%</div>
            <Sparkline values={cpu} color="#4ea1ff" max={100} />
          </div>
          <div className="metrics-row">
            <div className="metrics-label">
              RAM {ram.at(-1)?.toFixed(0) ?? 0}% · proc{" "}
              {Math.round(rss.at(-1) ?? 0)} MB
            </div>
            <Sparkline values={ram} color="#66d685" max={100} />
          </div>
          <div className="metrics-row">
            <div className="metrics-label">
              GPU{" "}
              {latest?.gpu?.gpus?.[0]?.name ??
                (latest?.gpu?.ok === false ? "n/a" : "—")}{" "}
              {gpuUtil.at(-1)?.toFixed(0) ?? 0}%
            </div>
            <Sparkline values={gpuUtil} color="#ff9b3d" max={100} />
          </div>
          <div className="metrics-row">
            <div className="metrics-label">
              Ollama models loaded ({psModels.length})
            </div>
            <ul className="metrics-list">
              {psModels.length === 0 ? (
                <li className="muted">none</li>
              ) : (
                psModels.map((m) => <li key={m}>{m}</li>)
              )}
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}
