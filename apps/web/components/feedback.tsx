export function LoadingState({ label = "데이터를 불러오는 중입니다" }: { label?: string }) {
  return (
    <div className="panel space-y-4 p-6" role="status">
      <div className="skeleton h-5 w-40" />
      <div className="skeleton h-20 w-full" />
      <span className="sr-only">{label}</span>
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="error-box flex flex-wrap items-center justify-between gap-3" role="alert">
      <span>{message}</span>
      {onRetry && <button type="button" className="btn btn-secondary btn-sm" onClick={onRetry}>다시 시도</button>}
    </div>
  );
}

export function EmptyState({ title, description, action }: { title: string; description: string; action?: React.ReactNode }) {
  return (
    <div className="empty">
      <div>
        <div className="mx-auto mb-3 grid size-11 place-items-center rounded-full bg-[#e7f2ed] text-lg text-[#166a58]">◇</div>
        <p className="font-bold text-[#263832]">{title}</p>
        <p className="mt-1 max-w-md text-sm leading-6">{description}</p>
        {action && <div className="mt-4">{action}</div>}
      </div>
    </div>
  );
}
