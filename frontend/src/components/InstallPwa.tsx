import { useEffect, useState } from "react";
import { IconDownload } from "./Icons";
import { toast } from "../lib/app";

// TypeScript interface for BeforeInstallPromptEvent
interface BeforeInstallPromptEvent extends Event {
  readonly platforms: string[];
  readonly userChoice: Promise<{
    outcome: "accepted" | "dismissed";
    platform: string;
  }>;
  prompt(): Promise<void>;
}

export function InstallPwaButton() {
  const [deferredPrompt, setDeferredPrompt] = useState<BeforeInstallPromptEvent | null>(null);
  const [isStandalone, setIsStandalone] = useState(false);
  const [isIos, setIsIos] = useState(false);
  const [showIosGuide, setShowIosGuide] = useState(false);

  useEffect(() => {
    // Check if running in standalone mode (already installed)
    const isStandaloneMode =
      window.matchMedia("(display-mode: standalone)").matches ||
      (window.navigator as unknown as { standalone?: boolean }).standalone === true;
    setIsStandalone(isStandaloneMode);

    // Detect iOS devices
    const userAgent = window.navigator.userAgent.toLowerCase();
    const isAppleDevice = /iphone|ipad|ipod/.test(userAgent);
    setIsIos(isAppleDevice);

    const handler = (e: Event) => {
      e.preventDefault();
      setDeferredPrompt(e as BeforeInstallPromptEvent);
    };

    window.addEventListener("beforeinstallprompt", handler);

    window.addEventListener("appinstalled", () => {
      setDeferredPrompt(null);
      setIsStandalone(true);
      toast("Punjab AI App installed successfully! You can now launch it from your home screen / desktop.", "ok");
    });

    return () => {
      window.removeEventListener("beforeinstallprompt", handler);
    };
  }, []);

  if (isStandalone) {
    return null; // Already running as installed app
  }

  async function handleInstall() {
    if (deferredPrompt) {
      await deferredPrompt.prompt();
      const choice = await deferredPrompt.userChoice;
      if (choice.outcome === "accepted") {
        setDeferredPrompt(null);
      }
    } else if (isIos) {
      setShowIosGuide(true);
    } else {
      toast("To install Punjab AI: click the install icon (🖥️ / ➕) in your browser address bar or menu.", "info");
    }
  }

  return (
    <>
      <button
        className="btn ghost sm pwa-install-btn"
        onClick={handleInstall}
        title="Install Punjab AI as a standalone desktop/mobile app"
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: "6px",
          color: "var(--accent, #f0a23b)",
          fontWeight: 550,
          border: "1px solid rgba(240, 162, 59, 0.35)",
          borderRadius: "6px",
          padding: "4px 8px",
          cursor: "pointer"
        }}
      >
        <IconDownload />
        <span className="hide-s">Install App</span>
      </button>

      {showIosGuide && (
        <div
          role="dialog"
          aria-modal="true"
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 9999,
            backgroundColor: "rgba(0,0,0,0.65)",
            backdropFilter: "blur(4px)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: "16px"
          }}
          onClick={() => setShowIosGuide(false)}
        >
          <div
            style={{
              background: "var(--bg-panel, #141a22)",
              border: "1px solid var(--border, #263242)",
              borderRadius: "12px",
              padding: "24px",
              maxWidth: "360px",
              width: "100%",
              color: "var(--text, #e6edf3)",
              boxShadow: "0 12px 32px rgba(0,0,0,0.5)"
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ margin: "0 0 12px 0", fontSize: "17px", fontWeight: 600 }}>Install on iPhone / iPad</h3>
            <p style={{ fontSize: "14px", lineHeight: "1.5", color: "var(--muted, #8b949e)", margin: "0 0 16px 0" }}>
              To install Punjab AI as an app on iOS:
            </p>
            <ol style={{ fontSize: "14px", lineHeight: "1.6", margin: "0 0 20px 0", paddingLeft: "20px" }}>
              <li>Tap the <b>Share</b> button <span style={{ fontSize: "16px" }}>􀈂</span> (bottom of Safari)</li>
              <li>Scroll down and tap <b>Add to Home Screen</b> <span style={{ fontSize: "16px" }}>􀎟</span></li>
              <li>Tap <b>Add</b> in the top-right corner</li>
            </ol>
            <button
              className="btn primary full"
              onClick={() => setShowIosGuide(false)}
              style={{ width: "100%" }}
            >
              Got it
            </button>
          </div>
        </div>
      )}
    </>
  );
}
