import { useEffect, useRef, useState } from "react";

const MODULE_ID = "autody-test-center";
const MIN_HOST_HEIGHT = 560;
const MAX_HOST_HEIGHT = 4000;

export function isValidModuleHeightMessage(event: MessageEvent, expectedSource: MessageEventSource | null): number | null {
  if (event.origin !== window.location.origin || event.source !== expectedSource) return null;
  const message = event.data;
  if (!message || message.type !== "autody-test-center:resize" || message.moduleId !== MODULE_ID) return null;
  if (typeof message.height !== "number" || !Number.isFinite(message.height)) return null;
  if (message.height < MIN_HOST_HEIGHT || message.height > MAX_HOST_HEIGHT) return null;
  return Math.ceil(message.height);
}

export function ModuleHostPage({ onRemoved }: { onRemoved: () => void }) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(MIN_HOST_HEIGHT);

  useEffect(() => {
    const listener = (event: MessageEvent) => {
      if (event.origin !== window.location.origin || event.source !== iframeRef.current?.contentWindow) return;
      if (event.data?.type === "autody-test-center:removed" && event.data?.moduleId === MODULE_ID) {
        onRemoved();
        return;
      }
      const nextHeight = isValidModuleHeightMessage(event, iframeRef.current?.contentWindow ?? null);
      if (nextHeight !== null) setHeight(nextHeight);
    };
    window.addEventListener("message", listener);
    return () => window.removeEventListener("message", listener);
  }, [onRemoved]);

  return (
    <section className="editor-page">
      <iframe
        ref={iframeRef}
        title="测试中心"
        className="module-host"
        style={{ height: `${height}px` }}
        src="/api/modules/autody-test-center/frontend/index.html"
      />
    </section>
  );
}
