import { useEffect, useRef, useState } from "react";
import { fetchBlobUrl } from "../../lib/api";

/** <img> for protected files: fetches with the bearer token, then shows a blob URL.
 *  `lazy` defers the request until the image scrolls into view (used for thumbnails in long lists). */
export function AuthImage({ src, alt, className, fallback, lazy = false }: { src: string | null; alt: string; className?: string; fallback?: React.ReactNode; lazy?: boolean }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [visible, setVisible] = useState(!lazy);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (visible || !box.current) return;
    const io = new IntersectionObserver((entries) => { if (entries.some((e) => e.isIntersecting)) { setVisible(true); io.disconnect(); } }, { rootMargin: "200px" });
    io.observe(box.current);
    return () => io.disconnect();
  }, [visible]);

  useEffect(() => {
    if (!src || !visible) return;
    let alive = true, created: string | null = null;
    setFailed(false);
    fetchBlobUrl(src).then((u) => { created = u; alive ? setUrl(u) : URL.revokeObjectURL(u); }).catch(() => alive && setFailed(true));
    return () => { alive = false; if (created) URL.revokeObjectURL(created); };
  }, [src, visible]);

  if (!src || failed) return <>{fallback ?? null}</>;
  if (!url) return <div ref={box} className={`skeleton ${className ?? ""}`} aria-hidden />;
  return <img src={url} alt={alt} className={className} />;
}
