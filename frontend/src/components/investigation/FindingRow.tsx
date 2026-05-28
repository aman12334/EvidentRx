import { SeverityBadge } from "./SeverityBadge";
import type { Finding } from "@/lib/types/finding";
import type { Severity } from "@/lib/types/investigation";

interface FindingRowProps {
  finding:  Finding;
  onClick?: (finding: Finding) => void;
}

export function FindingRow({ finding, onClick }: FindingRowProps) {
  return (
    <tr
      className="border-b border-slate-100 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50 cursor-pointer"
      onClick={() => onClick?.(finding)}
    >
      <td className="py-3 pl-4 pr-2">
        <SeverityBadge severity={finding.severity as Severity} />
      </td>
      <td className="py-3 px-2">
        <span className="font-mono text-xs text-slate-600">{finding.rule_code}</span>
      </td>
      <td className="py-3 px-2">
        <span className="font-mono text-xs text-slate-500">{finding.finding_code}</span>
      </td>
      <td className="py-3 px-2 text-xs text-slate-700 dark:text-slate-300">
        {finding.entity_name ?? finding.covered_entity_id}
      </td>
      <td className="py-3 pl-2 pr-4 text-right text-xs text-slate-400">
        {finding.created_at ? new Date(finding.created_at).toLocaleDateString() : "—"}
      </td>
    </tr>
  );
}
