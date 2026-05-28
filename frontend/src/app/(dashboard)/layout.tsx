/**
 * (dashboard) route group — pass-through layout.
 *
 * This group originally held the shell layout but shell layout was moved to
 * per-segment layout.tsx files (investigations/, intelligence/, graph/) to
 * avoid Next.js duplicate-route conflicts.
 *
 * The group is kept to wrap the root redirect page cleanly.
 */
export default function DashboardGroupLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <>{children}</>;
}
