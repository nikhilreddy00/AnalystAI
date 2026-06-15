// Phase 4: client-side PNG export. Uses html-to-image (not html2canvas) because
// Tailwind v4 / shadcn use modern CSS color functions like oklch() / lab() /
// color-mix() that html2canvas cannot parse — html-to-image handles them natively.
//
// D6: default background swapped to white to match the new light dashboard at
// /dashboard (D1). The dashboard is the only PNG-export consumer today.
//
// Gap 3: optional `footer` paints a small footer band below the captured image
// via an offscreen canvas. Used by the full-dashboard export; per-chart export
// omits it (call without options).

export async function downloadElementAsPng(
  element: HTMLElement,
  filename: string,
  options?: { backgroundColor?: string; footer?: string | null },
): Promise<void> {
  const backgroundColor = options?.backgroundColor ?? "#ffffff";
  const footer = options?.footer ?? null;

  // Dynamic import keeps the library out of the initial bundle — only loaded
  // when a user actually clicks download.
  const { toPng } = await import("html-to-image");

  const dataUrl = await toPng(element, {
    backgroundColor,
    pixelRatio: 2, // sharper exports
    cacheBust: true,
    filter: (node) => {
      // Skip any element opted-out of the snapshot
      return !(node instanceof HTMLElement && node.dataset.html2canvasIgnore !== undefined);
    },
  });

  const safeName = filename.replace(/[^a-z0-9_\-]/gi, "_") || "chart";
  const finalUrl = footer ? await paintFooter(dataUrl, footer, backgroundColor) : dataUrl;

  const link = document.createElement("a");
  link.href = finalUrl;
  link.download = `${safeName}.png`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

// FOOTER_H is in source-px; we multiply by the toPng pixelRatio (2) so the
// footer band visually matches the rendered dashboard's resolution.
const FOOTER_H = 48;
const PIXEL_RATIO = 2;

async function paintFooter(
  dataUrl: string,
  footer: string,
  backgroundColor: string,
): Promise<string> {
  const img = await loadImage(dataUrl);
  const canvas = document.createElement("canvas");
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight + FOOTER_H * PIXEL_RATIO;

  const ctx = canvas.getContext("2d");
  if (!ctx) return dataUrl; // graceful no-op if 2d context unavailable

  ctx.fillStyle = backgroundColor;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(img, 0, 0);

  ctx.font = `${12 * PIXEL_RATIO}px ui-sans-serif, system-ui, sans-serif`;
  ctx.fillStyle = "#9ca3af"; // zinc-400
  ctx.textBaseline = "middle";
  ctx.fillText(footer, 20 * PIXEL_RATIO, img.naturalHeight + (FOOTER_H * PIXEL_RATIO) / 2);

  return canvas.toDataURL("image/png");
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = src;
  });
}
