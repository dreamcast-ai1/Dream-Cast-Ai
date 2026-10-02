import { Info } from "lucide-react";
import { UsageList } from "../components/UsageList";
import { ErrorState, PageHeader, PageLoader } from "../components/ui/feedback";
import { useAsync } from "../hooks/useAsync";
import { api } from "../lib/api";
import type { UsageItem } from "../lib/types";

export default function Usage() {
  const { data, loading, error, reload } = useAsync(() => api<{ items: UsageItem[] }>("/api/usage"));
  return (
    <div>
      <PageHeader title="Usage" subtitle="Your plan's allowance for this period. Monthly limits reset at the start of each month (UTC)." />
      {loading ? <PageLoader /> : error ? <ErrorState message={error} onRetry={reload} /> : (
        <>
          <UsageList items={data!.items} />
          <p className="mt-6 flex items-start gap-2 text-sm text-muted"><Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            These are application-level limits set by an administrator. A generation counts when it is submitted; if it fails before reaching a provider (or is cancelled while queued) the allowance is returned.</p>
        </>)}
    </div>
  );
}
