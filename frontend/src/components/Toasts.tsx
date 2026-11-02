import { useToasts } from "../lib/app";

export function Toasts() {
  const list = useToasts();
  return (
    <div className="toasts" role="status" aria-live="polite">
      {list.map((t) => (
        <div key={t.id} className={`toast ${t.kind}`}>
          <span className="dot" />
          <span>{t.text}</span>
        </div>
      ))}
    </div>
  );
}
