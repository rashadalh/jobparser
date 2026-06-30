export default function Pills({
  label,
  items,
  testid,
  emptyText,
}: {
  label: string;
  items: string[];
  testid?: string;
  emptyText?: string; // when set, render this instead of hiding on empty
}) {
  if (items.length === 0 && !emptyText) return null;
  return (
    <div data-testid={testid}>
      <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
        {label}
      </span>
      {items.length === 0 ? (
        <p className="mt-1 text-sm text-gray-400">{emptyText}</p>
      ) : (
        <ul className="mt-1 flex flex-wrap gap-1">
          {items.map((it, i) => (
            <li
              key={i}
              className="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-700"
            >
              {it}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
