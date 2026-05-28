import { redirect } from "next/navigation";

/**
 * Root entry point. Redirects to the investigations queue (the primary
 * analyst workspace). The dashboard shell layout wraps all /investigations,
 * /intelligence, and /graph routes via the (dashboard) route group.
 */
export default function RootPage() {
  redirect("/investigations");
}
