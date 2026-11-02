import { navigate } from "./app";
import { createStore, useStore } from "./store";

export interface ChartSel {
  key: string;
  label: string;
  tf: "1" | "5" | "15" | "60" | "D";
}
export const TF_LABEL: Record<ChartSel["tf"], string> = { "1": "1m", "5": "5m", "15": "15m", "60": "1h", D: "1D" };

function load(): ChartSel {
  try {
    const r = JSON.parse(localStorage.getItem("nt-chart") || "{}");
    const tf = ["1", "5", "15", "60", "D"].includes(r.tf) ? r.tf : "5";
    return { key: typeof r.key === "string" ? r.key : "", label: typeof r.label === "string" ? r.label : "", tf };
  } catch {
    return { key: "", label: "", tf: "5" };
  }
}
export const chartSel = createStore<ChartSel>(load());
export function setChart(p: Partial<ChartSel>) {
  chartSel.set((s) => {
    const n = { ...s, ...p };
    try {
      localStorage.setItem("nt-chart", JSON.stringify(n));
    } catch {
      /* storage may be unavailable */
    }
    return n;
  });
}
export const useChartSel = () => useStore(chartSel);

/** Open the Chart page on an instrument. */
export function openChart(key: string, label: string) {
  setChart({ key, label });
  navigate("/chart");
}

export interface Drawing {
  id: string;
  type: "h" | "t";
  price?: number;
  t1?: number;
  p1?: number;
  t2?: number;
  p2?: number;
}
