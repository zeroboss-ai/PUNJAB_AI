export interface NetworkStatus {
  ok: boolean;
  app_mode: "sample" | "paper" | "live";
  mode: "auto" | "own";
  detected: { at: number | null; v4: string; v6: string; errors: { v4: string; v6: string } };
  own: { v4: string; v6: string };
  registered: { v4: string; v6: string; at: number } | null;
  expected: string[];
  source: "" | "account" | "terminal" | "config";
  account_label: string;
  kotak_sees: string | null;
  verdict: "ok" | "partial" | "changed" | "mismatch" | "unset" | "unchecked";
  message: string;
  bad: boolean;
}
