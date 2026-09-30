import { LogOut, Settings, User as UserIcon } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { Menu } from "../ui/Menu";

export function UserMenu() {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  if (!user) return null;
  const initial = (user.name || user.email)[0].toUpperCase();
  return (
    <Menu label="Account menu" trigger={<span className="flex h-7 w-7 items-center justify-center rounded-full bg-accent text-xs font-semibold text-accent-fg">{initial}</span>}
      items={[
        { label: "Account", icon: <UserIcon className="h-4 w-4" />, onSelect: () => nav("/account") },
        { label: "Settings", icon: <Settings className="h-4 w-4" />, onSelect: () => nav("/settings") },
        { label: "Log out", icon: <LogOut className="h-4 w-4" />, danger: true, onSelect: () => { logout(); nav("/"); } },
      ]} />
  );
}
