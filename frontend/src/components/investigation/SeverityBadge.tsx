import { Badge } from "@/components/ui/Badge";
import type { Severity } from "@/lib/types/investigation";

export function SeverityBadge({ severity }: { severity: Severity }) {
  return <Badge label={severity} variant={severity} />;
}
