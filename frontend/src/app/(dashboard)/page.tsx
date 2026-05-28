"use client";
/**
 * Route group layout page — delegates to the shared dashboard view.
 * URL: / (root)
 *
 * NOTE: If app/page.tsx is also present and redirects to /investigations,
 * remove this file and keep app/page.tsx only. Next.js resolves duplicate
 * root pages at build time. For single-root deployments, delete this file.
 */
import { redirect } from "next/navigation";

export default function DashboardGroupRootPage() {
  redirect("/investigations");
}
