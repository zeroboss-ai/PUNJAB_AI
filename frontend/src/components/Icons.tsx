import type { ReactNode } from "react";

const base = { width: 18, height: 18, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true } as const;

function I({ children, size }: { children: ReactNode; size?: number }) {
  return (
    <svg {...base} width={size ?? 18} height={size ?? 18}>
      {children}
    </svg>
  );
}

export const IconDashboard = () => (
  <I>
    <rect x="3" y="3" width="7" height="9" rx="1.5" />
    <rect x="14" y="3" width="7" height="5" rx="1.5" />
    <rect x="14" y="12" width="7" height="9" rx="1.5" />
    <rect x="3" y="16" width="7" height="5" rx="1.5" />
  </I>
);
export const IconWatch = () => (
  <I>
    <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" />
    <circle cx="12" cy="12" r="3" />
  </I>
);
export const IconOrders = () => (
  <I>
    <path d="M8 6h13M8 12h13M8 18h13" />
    <path d="M3.5 6h.01M3.5 12h.01M3.5 18h.01" />
  </I>
);
export const IconPositions = () => (
  <I>
    <path d="M3 17l5-5 4 4 8-9" />
    <path d="M15 7h5v5" />
  </I>
);
export const IconAlgo = () => (
  <I>
    <rect x="4" y="7" width="16" height="12" rx="3" />
    <path d="M12 7V3.5M9 12.5v1M15 12.5v1M9.5 16.5h5" />
  </I>
);
export const IconChain = () => (
  <I>
    <path d="M4 5h6v14H4zM14 5h6v14h-6z" />
    <path d="M10 9h4M10 15h4" />
  </I>
);
export const IconOI = () => (
  <I>
    <path d="M5 20V10M10 20V4M15 20v-7M20 20V8" />
  </I>
);
export const IconJournal = () => (
  <I>
    <path d="M6 3h12a1 1 0 011 1v16a1 1 0 01-1 1H6a1 1 0 01-1-1V4a1 1 0 011-1z" />
    <path d="M9 8h6M9 12h6M9 16h3" />
  </I>
);
export const IconMarket = () => (
  <I>
    <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />
    <path d="M13 8l3-3 3 3" />
  </I>
);
export const IconBuilder = () => (
  <I>
    <rect x="3" y="3" width="7" height="6" rx="1.5" />
    <rect x="14" y="15" width="7" height="6" rx="1.5" />
    <path d="M10 6h4a3 3 0 013 3v6" />
  </I>
);
export const IconSettings = () => (
  <I>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.7 1.7 0 00.3 1.9l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.9-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.9.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.9 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.9l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.9.3h0a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5h0a1.7 1.7 0 001.9-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.9v0a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z" />
  </I>
);
export const IconSun = () => (
  <I size={16}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
  </I>
);
export const IconMoon = () => (
  <I size={16}>
    <path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z" />
  </I>
);
export const IconSearch = () => (
  <I size={16}>
    <circle cx="11" cy="11" r="7" />
    <path d="M21 21l-4.3-4.3" />
  </I>
);
export const IconX = () => (
  <I size={16}>
    <path d="M18 6L6 18M6 6l12 12" />
  </I>
);
export const IconTrash = () => (
  <I size={15}>
    <path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" />
  </I>
);
export const IconLogout = () => (
  <I size={16}>
    <path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4M16 17l5-5-5-5M21 12H9" />
  </I>
);
export const IconPlus = () => (
  <I size={16}>
    <path d="M12 5v14M5 12h14" />
  </I>
);
export const IconEdit = () => (
  <I size={15}>
    <path d="M12 20h9M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z" />
  </I>
);
export const IconAlert = () => (
  <I size={16}>
    <path d="M12 3l10 18H2zM12 10v5M12 18h.01" />
  </I>
);

export function Logo({ size = 34 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <rect width="64" height="64" rx="14" fill="var(--bg)" stroke="var(--line-2)" strokeWidth="1.5" />
      <line x1="12" y1="20" x2="12" y2="46" stroke="var(--accent)" strokeWidth="2.4" strokeLinecap="round" />
      <rect x="8.5" y="28" width="7" height="12" rx="1.8" fill="var(--accent)" />
      <line x1="22" y1="16" x2="22" y2="44" stroke="var(--down)" strokeWidth="2.4" strokeLinecap="round" />
      <rect x="18.5" y="26" width="7" height="12" rx="1.8" fill="var(--down)" />
      <line x1="32" y1="24" x2="32" y2="50" stroke="var(--down)" strokeWidth="2.4" strokeLinecap="round" />
      <rect x="28.5" y="34" width="7" height="12" rx="1.8" fill="var(--down)" />
      <line x1="42" y1="12" x2="42" y2="38" stroke="var(--accent)" strokeWidth="2.4" strokeLinecap="round" />
      <rect x="38.5" y="18" width="7" height="14" rx="1.8" fill="var(--accent)" />
      <line x1="52" y1="8" x2="52" y2="32" stroke="var(--accent)" strokeWidth="2.4" strokeLinecap="round" />
      <rect x="48.5" y="10" width="7" height="14" rx="1.8" fill="var(--accent)" />
      <path d="M7 56q5.1-5 10.2 0t10.2 0t10.2 0t10.2 0t10.2 0" fill="none" stroke="var(--up)" strokeWidth="2.8" strokeLinecap="round" />
    </svg>
  );
}
export const IconBacktest = () => (
  <I>
    <path d="M3 12a9 9 0 109-9 9 9 0 00-6.4 2.6L3 8" />
    <path d="M3 3v5h5M12 7v5l3 2" />
  </I>
);

export const IconChart = () => (
  <I>
    <path d="M6 4v3M6 13v4M12 8v3M12 16v4M18 3v4M18 12v5" />
    <rect x="4.5" y="7" width="3" height="6" rx="0.8" />
    <rect x="10.5" y="11" width="3" height="5" rx="0.8" />
    <rect x="16.5" y="7" width="3" height="5" rx="0.8" />
  </I>
);

export const IconDownload = () => (
  <I>
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
    <polyline points="7 10 12 15 17 10" />
    <line x1="12" y1="15" x2="12" y2="3" />
  </I>
);
