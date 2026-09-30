import { Link } from "react-router-dom";
import { JobList } from "../../components/JobList";
import { EmptyState, ErrorState, PageLoader } from "../../components/ui/feedback";
import { useAsync } from "../../hooks/useAsync";
import { usePolling } from "../../hooks/usePolling";
import { api } from "../../lib/api";
import { ACTIVE_STATUSES, type Job } from "../../lib/types";

export function Generations({ projectId }: { projectId: string }) {
  const { data, setData, loading, error, reload } = useAsync(() => api<Job[]>(`/api/jobs?project_id=${projectId}&limit=50`), [projectId]);
  usePolling(async () => { try { setData(await api<Job[]>(`/api/jobs?project_id=${projectId}&limit=50`)); } catch { /* keep list */ } }, !!data?.some((j) => ACTIVE_STATUSES.includes(j.status)), 6000);
  if (loading && !data) return <PageLoader />;
  if (error) return <ErrorState message={error} onRetry={reload} />;
  if (!data?.length) return <EmptyState icon="✨" title="No generations in this project yet." hint="Choose this project on the Create page and the result will be saved here."
    action={<Link className="btn-primary" to={`/create?project=${projectId}`}>Create something</Link>} />;
  return <JobList jobs={data} showProject={false} />;
}
