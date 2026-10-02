export interface SupportButton { id: string; label: string }
export interface SupportReply { matched: boolean; category: string; entry: string | null; title: string; reply: string; steps: string[]; follow_up: string | null; buttons: SupportButton[] }
export interface SupportOptions { greeting: string; categories: { id: string; label: string }[]; quick_actions: { label: string; category: string }[]; ticket_limit_per_hour: number }
export interface TicketMessage { author: "user" | "admin"; body: string; created_at: string }
export interface SupportTicket {
  id: string; number: number; category: string; category_label: string; subject: string; description: string; status: "open" | "in_progress" | "resolved" | "closed";
  priority: "low" | "normal" | "high" | "critical"; page: string | null; feature: string | null; error_message: string | null; admin_response: string | null;
  created_at: string; updated_at: string; resolved_at: string | null; messages: TicketMessage[];
  diagnostic_context?: Record<string, string>; user?: { id: string; email: string | null; name: string | null };
}
export const STATUS_LABEL: Record<SupportTicket["status"], string> = { open: "Open", in_progress: "In progress", resolved: "Resolved", closed: "Closed" };

/** Where the user is, in words the support assistant understands. Only the path pattern is used; nothing else leaves the browser. */
export function pageContext(pathname: string): { page: string; project_id?: string } {
  const project = /^\/projects\/([A-Za-z0-9]{8,40})/.exec(pathname);
  if (project) return { page: "Project", project_id: project[1] };
  const first = pathname.split("/").filter(Boolean)[0] ?? "";
  const names: Record<string, string> = { create: "Create", write: "Write", plans: "Plans", admin: "Admin", usage: "Usage", library: "Library", history: "History", projects: "Projects", settings: "Settings", dashboard: "Dashboard" };
  return { page: names[first] ?? "App" };
}

/** The last UI crash message, kept for the session by ErrorBoundary so a ticket can mention it (the support assistant scrubs it on the server). */
export const LAST_ERROR_KEY = "dc-last-ui-error";
export const lastUiError = (): string => { try { return sessionStorage.getItem(LAST_ERROR_KEY) ?? ""; } catch { return ""; } };
export const clearUiError = () => { try { sessionStorage.removeItem(LAST_ERROR_KEY); } catch { /* storage unavailable */ } };
