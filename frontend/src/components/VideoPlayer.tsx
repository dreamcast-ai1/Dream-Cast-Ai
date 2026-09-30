import { Play } from "lucide-react";
import { useState } from "react";
import { errorMessage, streamUrl } from "../lib/api";
import { AuthImage } from "./ui/AuthImage";
import { Spinner } from "./ui/feedback";

/** Lazy video: shows the thumbnail and only requests the (signed) stream URL when the user presses play. The native <video> element
 *  streams with HTTP Range requests, so seeking works without downloading the whole file, and it brings play, pause, seek, volume
 *  and fullscreen controls. */
export function VideoPlayer({ assetId, thumbnail, title }: { assetId: string; thumbnail: string | null; title: string }) {
  const [src, setSrc] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = async () => {
    setBusy(true); setError("");
    try { setSrc(await streamUrl("asset", assetId)); } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  };

  if (src) {
    return <video controls autoPlay playsInline preload="metadata" src={src} aria-label={title} className="aspect-video max-h-[70vh] w-full rounded-lg bg-black"
      onError={() => { setSrc(null); setError("The video link expired. Press play to reload it."); }} />;
  }
  return (
    <div className="relative aspect-video w-full overflow-hidden rounded-lg bg-black">
      {thumbnail && <AuthImage src={thumbnail} alt={`Thumbnail of ${title}`} className="h-full w-full object-contain" />}
      <button onClick={load} disabled={busy} aria-label={`Play video: ${title}`}
        className="absolute inset-0 flex items-center justify-center bg-black/30 text-white transition-colors hover:bg-black/20">
        <span className="flex h-16 w-16 items-center justify-center rounded-full bg-accent text-accent-fg shadow-lg">{busy ? <Spinner className="!h-6 !w-6" /> : <Play className="h-7 w-7" aria-hidden />}</span>
      </button>
      {error && <p role="alert" className="absolute inset-x-0 bottom-0 bg-black/70 p-2 text-center text-sm text-danger">{error}</p>}
    </div>
  );
}
