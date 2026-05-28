interface EmptyStateProps {
  title:       string;
  description: string;
  action?:     React.ReactNode;
}

export function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-slate-100 dark:bg-slate-800">
        <span className="text-2xl">📂</span>
      </div>
      <h3 className="mb-1 text-sm font-semibold text-slate-900 dark:text-white">{title}</h3>
      <p className="mb-4 text-sm text-slate-500">{description}</p>
      {action}
    </div>
  );
}
