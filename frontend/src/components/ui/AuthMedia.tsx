import { useEffect, useState } from "react";
import { fetchBlobUrl } from "../../lib/api";

/** Video/audio for protected files (fetched with the bearer token, played from a blob URL). */
export function AuthMedia({ src, kind, label }: { src: string; kind: "video" | "audio"; label: string }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let alive = true, created: string | null = null;
    fetchBlobUrl(src).then((u) => { created = u; alive ? setUrl(u) : URL.revokeObjectURL(u); }).catch(() => undefined);
    return () => { alive = false; if (created) URL.revokeObjectURL(created); };
  }, [src]);
  if (!url) return <div className="skeleton h-24 w-full" aria-hidden />;
  return kind === "video" ? <video controls src={url} aria-label={label} className="w-full rounded-lg" /> : <audio controls src={url} aria-label={label} className="w-full" />;
}
